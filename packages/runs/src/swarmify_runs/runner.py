"""The DBOS shell around the stage state machine (ADR 0014).

One DBOS workflow per run, id equal to the run id, enqueued on the world's own queue. Each stage
it executes is a workflow of its own on the world's slot queue, so each world has at most its run
cap of stages at work at once (`cap_for`, a World setting read through the composition root; `cap`
when none is given), while a run waiting at a gate, paused or on a child run holds no slot (#486).
A queued run reads queued until its first stage holds a slot. Queue configs live in DBOS's
system database and reload live, so a changed cap takes effect without a restart. Every side
effect is a DBOS step: loading the run, saving each new state, executing a stage. A crash
resumes from the last completed step, so a finished stage never runs twice. DBOS is a process
singleton, hence one module-level Runner.

A workflow stage starts a child run of another workflow's current version (ADR 0013), links it
to the stage and waits, like a gate, on the run's control topic: the child's workflow sends its
end there. Children wait on their own unpartitioned queue; their stages take slots like any.

The workflow is the only writer of run state. People act through RunControl, whose messages
(decide, pause, resume, abort) arrive on the run's control topic: at a gate or while paused the
workflow waits for them; around a stage it drains what arrived meanwhile.
"""

import json
import logging
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from dbos import DBOS, DBOSConfig, SetWorkflowID

from swarmify_kernel import Event, new_id
from swarmify_runs import budget
from swarmify_runs.control import (
    APP_NAME,
    CONTROL_TOPIC,
    SCHEDULED_WORKFLOW,
    WAKE,
    Monthly,
    OverBudget,
    Pin,
    Previews,
    RunControl,
    Spend,
    ended_event,
    no_previews,
    no_spend,
    queue_name,
    run_folder,
    slot_queue,
    stage_folder,
    with_sidecar,
)
from swarmify_runs.machine import (
    TERMINAL,
    Aborted,
    BudgetAdded,
    BudgetSpent,
    GateAnswered,
    GateRaised,
    Illegal,
    ItemsRetried,
    MonthSpent,
    OpenGate,
    Paused,
    Rerun,
    Resumed,
    RunEvent,
    RunStage,
    RunState,
    SentBack,
    Skip,
    StageFailed,
    StageFinished,
    StageStopped,
    Started,
    transition,
)
from swarmify_runs.model import RERUNS, Item, ItemView, Pinned, Run, StageDef, items_gate
from swarmify_runs.ports import Notifier, Storage

CHILD_QUEUE = "child-runs"
DEFAULT_CAP = 2
CapFor = Callable[[str], int]
"""(world id) to how many of the world's runs execute at once."""
MAX_DEPTH = 2
"""A child may start a child, no deeper (ADR 0013). Saving refuses deeper nesting already; this
guards a run against workflows edited after it pinned its version."""
# ponytail: pinned so a code change does not orphan runs in flight (DBOS only recovers
# workflows of the current application version). Bump it when the workflow's step order changes.
APP_VERSION = "6"

Connector = Callable[[Run, str, StageDef], None]
"""Calls one connector stage. Arguments: the run, the stage id (the idempotency key) and the
stage's definition."""
Swarm = Callable[[Run, str, StageDef], None]
"""Runs one swarm stage to its end, with the same arguments. Raises to fail the stage, or
OverBudget when the run's Budget stopped its agents."""
Retro = Callable[[Run, str, StageDef], bool]
"""Runs the retro stage of a run, with the same arguments. True when it recorded a proposal:
the stage then waits at a gate of kind `skill` until a person decides it (ADR 0008)."""


class Take(Protocol):
    """Copies the named artifacts of a child run into its parent's stage. Arguments: the child,
    the parent, the parent's stage id, the names and, for a fan-out stage's Item, the subfolder
    of the stage's artifacts they go in (the Item's key). Raises to fail the stage."""

    def __call__(
        self, child: Run, parent: Run, stage_id: str, names: list[str], folder: str = "", /
    ) -> None: ...


Items = Callable[[Run, int, StageDef], list[Item]]
"""The Items of the fan-out stage at an index of a run, from its `for_each` source: an earlier
stage's cards, the cards picked at a pick gate, or a JSON list file's elements (ADR 0031).
Raises to fail the stage."""

_runner: "Runner | None" = None
# Runs whose workflow thread sits in recv. Shutdown wakes them, because DBOS cannot interrupt a
# recv and Python joins executor threads at exit.
_waiting: set[str] = set()
_waiting_lock = threading.Lock()
_stopping = threading.Event()
log = logging.getLogger(__name__)


class LogNotifier:
    """The adapter when no other is given: one log line per notification. The API passes the
    Slack adapter of the Notifications context."""

    log = logging.getLogger("swarmify.notify")

    def notify(self, event: Event) -> None:
        self.log.info("%s %s", event.type, event.payload)


@dataclass(frozen=True)
class _ChildEnded:
    """A child run reached a terminal status. Not a machine event: it becomes StageFinished
    once the artifacts are taken, or StageFailed."""

    child_run_id: str
    status: str


class _Halted(Exception):
    """A pause or abort, not the Budget, stopped the stage's agents (`check_stop`)."""


class _Shutdown(BaseException):
    """Leaves a workflow PENDING so the next launch recovers it. DBOS records a workflow's
    Exception as its outcome, but lets a BaseException through unrecorded, like a crash."""


class Runner(RunControl):
    def __init__(
        self,
        storage: Storage,
        system_db: Path,
        connector: Connector,
        data_dir: Path,
        notifier: Notifier | None = None,
        cap: int = DEFAULT_CAP,
        swarm: Swarm | None = None,
        poll_seconds: float = 1.0,
        pin: Pin | None = None,
        take: Take | None = None,
        previews: Previews = no_previews,
        spend: Spend = no_spend,
        monthly: Monthly = lambda _world_id: None,
        cap_for: CapFor | None = None,
        retro: Retro | None = None,
        worlds: Callable[[], list[str]] | None = None,
        items: Items | None = None,
    ) -> None:
        """`pin` and `take` serve workflow stages; without `pin` they fail the run. `spend` sums
        the agents' estimated cost of runs, for the Budget. `monthly` is each world's Monthly
        Budget and `cap_for` its run cap (`cap` for every world when left out). `worlds` lists
        the ids of the worlds that exist; given it, launch drops the queues of any others."""
        super().__init__(
            storage, system_db, data_dir, notifier or LogNotifier(), previews, spend, monthly
        )
        self.connector = connector
        self.swarm = swarm
        self.retro = retro
        self.pin = pin
        self.take = take
        self.items = items
        self.cap_for = cap_for or (lambda _world_id: cap)
        self.poll_seconds = poll_seconds
        self.worlds = worlds
        self._caps: dict[str, int] = {}
        """The cap each world's queue was last registered with."""

    def launch(self) -> None:
        """Start DBOS. Recovers runs interrupted by a crash and re-enqueues queued ones."""
        global _runner
        _runner = self
        _stopping.clear()
        config: DBOSConfig = {
            "name": APP_NAME,
            "system_database_url": f"sqlite:///{self.system_db}",
            "application_version": APP_VERSION,
            "notification_listener_polling_interval_sec": min(self.poll_seconds, 1.0),
            "scheduler_polling_interval_sec": max(self.poll_seconds, 1.0),
            "log_level": "WARNING",
            "enable_patching": True,  # runs that ran before stage slots replay without them
        }
        DBOS(config=config)
        DBOS.launch()
        self._caps.clear()
        if self.worlds is not None:
            self._drop_leaked_queues(self.worlds)
            for world_id in self.worlds():  # a run queue that capped runs before #486 caps none
                self.set_cap(world_id)
        DBOS.register_queue(
            CHILD_QUEUE, polling_interval_sec=self.poll_seconds, on_conflict="always_update"
        )
        self.adopt()

    def shutdown(self, seconds: float = 5.0) -> None:
        """Release the waiting workflows, then stop DBOS. Interrupted runs resume on launch."""
        global _runner
        _stopping.set()
        deadline = time.monotonic() + seconds
        # Keep waking until nothing waits: a run that reaches its recv after one round of
        # wakes (a scheduled run starting mid-shutdown) would otherwise hang the exit.
        while time.monotonic() < deadline:
            with _waiting_lock:
                waiting = list(_waiting)
            if not waiting:
                break
            for run_id in waiting:
                try:
                    DBOS.send(run_id, WAKE, topic=CONTROL_TOPIC)
                except Exception:  # left by a launch that did not wind down in time
                    log.warning("could not wake run %s at shutdown", run_id)
            time.sleep(0.05)
        with _waiting_lock:
            _waiting.clear()  # anything still here belongs to this launch, which is ending
        self.close()
        DBOS.destroy()
        _runner = None

    @staticmethod
    def _drop_leaked_queues(worlds: Callable[[], list[str]]) -> None:
        """Delete the queues of deleted worlds that a delete which kept its queue left behind, each
        still polled by its own thread. Queues are listed before worlds: a world created in
        between has no queue yet, so every queue whose world is missing after is a leak."""
        names = [q.name for q in DBOS.list_queues()]
        live = {q(w) for w in worlds() for q in (queue_name, slot_queue)}
        for name in names:
            if name.startswith((queue_name(""), slot_queue(""))) and name not in live:
                DBOS.delete_queue(name)

    def promote(self, pinned: Pinned, index: int, artifact: Path, promoted_from: str) -> Run:
        """Start a Promote: a run of `pinned` that starts at stage `index` with `artifact` as
        its input. The earlier stages are skipped and the file (with its sidecar) stands in for
        their output, in the last skipped stage's artifacts folder where later stages look for
        inputs, renamed to stage `index`'s `input` (or its one `inputs`) when it names one.
        `promoted_from` is the artifact's path in the world folder, kept as lineage.
        Raises ValueError for an index that skips nothing or everything, a missing file, or a
        stage taking several inputs, which one file cannot stand in for."""
        if not 1 <= index < len(pinned.stages):
            raise ValueError(
                "a promote skips at least one stage and starts at or before the last; to run every"
                " stage, start a run"
            )
        if not artifact.is_file():
            raise ValueError(f"no artifact at {promoted_from}")
        config = pinned.stages[index].config
        names = config.get("inputs") or [config.get("input") or ""]
        if len(names) > 1:
            raise ValueError(
                f"stage '{pinned.stages[index].name}' takes {len(names)} inputs and a promote"
                " brings one file; start a run or fork one instead"
            )
        budget_usd = self.budget_for(pinned.world_id, workflow=pinned.budget_usd)
        now = datetime.now(UTC).isoformat()
        run = Run(
            new_id("run"), pinned.world_id, pinned.workflow_id, pinned.version, pinned.name,
            "queued", now, now, [], promoted_from=promoted_from, budget_usd=budget_usd,
            budget_warn_usd=pinned.budget_warn_usd,
        )  # fmt: skip
        run = self.storage.add_run(run, pinned.stages, skipped=index)
        folder = run_folder(self.data_dir, run) / run.stages[index - 1].id / "artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        files = sorted(with_sidecar(artifact), key=lambda f: len(f.name))
        # The stage finds its input by exact name, but a typed artifact is `<type>-<slug>.md`:
        # the file goes in under the starting stage's `input`, its sidecar under that plus .md.
        name = str(names[0])
        if Path(name).name != name or name in ("", ".", ".."):
            name = files[0].name
        for file, target in zip(files, (name, name + ".md"), strict=False):
            shutil.copyfile(file, folder / target)
        self._enqueue(run)
        return run

    def list_all(
        self, world_id: str, limit: int | None = None, before: str | None = None
    ) -> list[Run]:
        return self.storage.list_runs(world_id, limit, before)

    def set_cap(self, world_id: str) -> None:
        """Register (or update) the world's queues, its slots with its current cap. Call it when
        the cap setting changes; `_enqueue` calls it for a world it has not seen since launch."""
        cap = self.cap_for(world_id)
        for name, most in ((queue_name(world_id), None), (slot_queue(world_id), cap)):
            DBOS.register_queue(
                name,
                global_concurrency=most,
                polling_interval_sec=self.poll_seconds,
                on_conflict="always_update",
            )
        self._caps[world_id] = cap

    def adopt(self) -> None:
        """Enqueue every queued run, such as one the MCP process recorded (`RunControl._enqueue`).
        Idempotent: a run already enqueued attaches to its own workflow."""
        for run_id in self.storage.queued_run_ids():
            run = self.storage.get_run(run_id)
            if run is not None:
                self._enqueue(run)

    def _enqueue(self, run: Run) -> None:
        # The run id is the workflow id, so enqueueing twice attaches to the same workflow.
        if run.parent_run_id is not None:
            with SetWorkflowID(run.id):
                DBOS.enqueue_workflow(CHILD_QUEUE, execute_run, run.id)
            return
        if run.world_id not in self._caps:
            self.set_cap(run.world_id)
        with SetWorkflowID(run.id):
            DBOS.enqueue_workflow(queue_name(run.world_id), execute_run, run.id)


def _current() -> Runner:
    assert _runner is not None, "Runner.launch() was not called"
    return _runner


@DBOS.workflow(name=SCHEDULED_WORKFLOW)
def scheduled_run(scheduled_at: datetime, context: Any) -> str | None:
    """What a workflow's cron fires: a run of its current version, as `start` would make one.
    The schedule's context names the workflow. A workflow deleted since does nothing."""
    started = _start_scheduled(str(context["workflow_id"]), scheduled_at.isoformat())
    if started is None:
        return None
    run_id, world_id = started
    runner = _current()
    if world_id not in runner._caps:
        runner.set_cap(world_id)
    with SetWorkflowID(run_id):
        DBOS.enqueue_workflow(queue_name(world_id), execute_run, run_id)
    return run_id


@DBOS.step(name="swarmify.start_scheduled")
def _start_scheduled(workflow_id: str, fired_at: str) -> tuple[str, str] | None:
    runner = _current()
    if runner.pin is None:
        raise NotImplementedError("scheduled runs need a pin to find their workflow")
    try:
        pinned = runner.pin(workflow_id)
    except KeyError:
        log.warning("schedule fired for %s at %s, but it no longer exists", workflow_id, fired_at)
        return None
    except ValueError as exc:
        log.warning(
            "schedule fired for %s at %s, but its current version fails today's workflow check: %s",
            workflow_id,
            fired_at,
            exc,
        )
        return None
    if pinned.schedule is None:
        # DBOS learns of a dropped schedule only at its next poll; a tick in that gap would
        # start a run of a version that has no schedule.
        log.info(
            "schedule fired for %s at %s, but its current version has none", workflow_id, fired_at
        )
        return None
    try:
        budget_usd = runner.budget_for(pinned.world_id, workflow=pinned.budget_usd)
    except MonthSpent:
        log.warning(
            "schedule fired for %s at %s, but its world's month is spent", workflow_id, fired_at
        )
        return None
    now = datetime.now(UTC).isoformat()
    run = Run(
        new_id("run"), pinned.world_id, pinned.workflow_id, pinned.version, pinned.name,
        "queued", now, now, [], budget_usd=budget_usd, budget_warn_usd=pinned.budget_warn_usd,
    )  # fmt: skip
    run = runner.storage.add_run(run, pinned.stages)
    return run.id, run.world_id


@DBOS.workflow(name="swarmify.run")
def execute_run(run_id: str) -> str:
    kinds, status, stages, send_backs, parent, over_budget = _load(run_id)
    if status in TERMINAL:  # aborted between dequeue and load: nothing to run or wait for
        return status
    state = RunState(status, tuple(stages), send_backs, over_budget)  # type: ignore[arg-type]
    # A run that waited at a gate before stage slots existed replays without them (#486).
    world = _world_of(run_id) if DBOS.patch("stage-slots") else None
    events: list[RunEvent | _ChildEnded] = [*_drain(run_id), Started()]
    error: str | None = None
    while True:
        if not events:  # at a gate, paused, waiting on a child, or after a stale message
            events = [e for e in [_as_event(_wait(run_id))] if e is not None]
            continue
        event, events = events[0], events[1:]
        if isinstance(event, _ChildEnded):
            taken = _take(run_id, event.child_run_id, event.status)
            if taken is None:
                continue  # a child of an earlier visit to the stage
            index, error, fan = taken
            if fan:  # one Item settled: start the next, or join once all have
                _, settled, error = _fan_out(run_id, index, entering=False)
                if settled is None:
                    continue
                event = settled
            else:
                event = StageFinished(index) if error is None else StageFailed(index)
        prior = state.status
        try:
            state, commands = transition(tuple(kinds), state, event)
        except Illegal as exc:
            if isinstance(event, Rerun | Skip) and event.change:
                _drop_change(event.change, str(exc))  # e.g. its stage started meanwhile
            continue  # a stale message, e.g. a resume for a run that is not paused
        gate_opened = bool(commands and isinstance(commands[0], OpenGate))
        outcome_next = events and isinstance(
            events[-1], StageFinished | StageFailed | BudgetSpent | StageStopped
        )
        if outcome_next and not gate_opened and state.status not in TERMINAL:
            continue  # a pause sent mid-stage is saved with the stage's outcome, never before it
        # A queued run starting reads queued until its first stage holds a slot: the slot saves it.
        start = world is not None and prior == "queued" and not events and _starts(kinds, commands)
        if not start:
            _save(run_id, state, error, gate_opened)
        if state.status in TERMINAL:
            if parent is not None:
                # ponytail: a parent already ended (aborted first) never reads this, so it stays
                # in DBOS's notifications table; prune old rows if that table grows noticeably
                message = {"action": "child", "child": run_id, "status": state.status}
                DBOS.send(parent, message, topic=CONTROL_TOPIC)
            return state.status
        if isinstance(event, Resumed) and not commands and "running" in state.stages:
            running = state.stages.index("running")
            if kinds[running] == "workflow":  # a fan-out starts the Items a pause held back
                _, settled, error = _fan_out(run_id, running, entering=False)
                events = [*events, *([settled] if settled else [])]
                continue
        if events or not commands or isinstance(commands[0], OpenGate):
            continue
        index, error = commands[0].index, None
        if kinds[index] == "workflow":
            fan, settled, error = _fan_out(run_id, index, entering=True)
            if fan:  # its children run on; their ends arrive on the control topic
                events = [*_drain(run_id), *([settled] if settled else [])]
                continue
            try:
                child = _start_child(run_id, index)
            except Exception as exc:
                events = [*_drain(run_id), StageFailed(index)]
                error = f"{type(exc).__name__}: {exc}"
                continue
            with SetWorkflowID(child):  # a workflow-level op, so a replay never enqueues twice
                DBOS.enqueue_workflow(CHILD_QUEUE, execute_run, child)
            events = list(_drain(run_id))  # then wait for the child's end on the control topic
            continue
        got: list[RunEvent | _ChildEnded] = []
        if world is None:
            result, error = _run_stage(run_id, index)
        else:
            runner = _current()
            if world not in runner._caps:  # a run recovered at launch, before any enqueue
                runner.set_cap(world)
            DBOS.enqueue_workflow(
                slot_queue(world), stage_in_slot, run_id, index, state if start else None
            )
            result, error, got = _await_stage(run_id)
        outcome = OUTCOMES_OF[result](index)
        # ponytail: a connector stage or a running child run is not stopped midway; a pause or
        # abort reaches it once it ends (abort still cascades to the child, and cancels a
        # connector stage's long job).
        events = [*got, *_drain(run_id), outcome]


OUTCOMES_OF: dict[str, Callable[[int], RunEvent]] = {
    "finished": StageFinished,
    "raised": GateRaised,
    "stopped": StageStopped,  # a pause or abort stopped the agents; its message drains too
    "over": BudgetSpent,  # the agents stopped; the stage waits at a budget gate
    "failed": StageFailed,  # a failed stage fails the run; the error lands on the stage
}


def _starts(kinds: list[str], commands: list[Any]) -> bool:
    return (
        bool(commands)
        and isinstance(commands[0], RunStage)
        and kinds[commands[0].index] != "workflow"
    )


def _run_stage(run_id: str, index: int) -> tuple[str, str | None]:
    """Execute the stage at `index`: its outcome (an OUTCOMES_OF key) and error."""
    try:
        return ("raised" if _execute(run_id, index) else "finished"), None
    except _Halted:
        return "stopped", None
    except OverBudget:
        return "over", None
    except Exception as exc:
        return "failed", f"{type(exc).__name__}: {exc}"


@DBOS.workflow(name="swarmify.stage")
def stage_in_slot(run_id: str, index: int, start: RunState | None) -> None:
    """One stage of a run at work, holding one of its world's run slots until it ends (#486).
    `start` is the state a queued run starts in, saved once it holds the slot. Sends the outcome
    to the run's control topic."""
    result, error = _run_stage(run_id, index) if _ready(run_id, start) else ("stopped", None)
    DBOS.send(run_id, {"action": "stage", "result": result, "error": error}, topic=CONTROL_TOPIC)


@DBOS.step(name="swarmify.ready")
def _ready(run_id: str, start: RunState | None) -> bool:
    """False when a pause or abort came while the stage waited for its slot."""
    runner = _current()
    if runner.storage.stop_of(run_id):
        return False
    if start is not None:
        run = runner.storage.get_run(run_id)
        if run is None or run.status != "queued":  # aborted while queued
            return False
        runner.storage.save_state(run_id, start)
    return True


def _await_stage(run_id: str) -> tuple[str, str | None, list[RunEvent | _ChildEnded]]:
    """Wait for the stage's outcome, keeping the control messages that came first, in order."""
    got: list[RunEvent | _ChildEnded] = []
    while (message := _wait(run_id))["action"] != "stage":
        if (event := _as_event(message)) is not None:
            got.append(event)
    return message["result"], message["error"], got


@DBOS.step(name="swarmify.world_of")
def _world_of(run_id: str) -> str:
    run = _current().storage.get_run(run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    return run.world_id


def _as_event(message: dict[str, Any]) -> RunEvent | _ChildEnded | None:
    match message["action"]:
        case "child":
            return _ChildEnded(message["child"], message["status"])
        case "decide" if message.get("retry"):
            return ItemsRetried(message["index"])
        case "decide" if "to" in message:
            return SentBack(message["index"], message["to"])
        case "decide":
            return GateAnswered(message["index"])
        case "rerun":
            return Rerun(message["to"], message.get("change", ""), tuple(message.get("keep", ())))
        case "skip":
            return Skip(message["index"], message.get("change", ""))
        case "budget":
            return BudgetAdded()
        case "pause":
            return Paused()
        case "resume":
            return Resumed()
        case "abort":
            return Aborted()
        case action:
            log.warning("ignoring unknown control action %r", action)
            return None


def _drain(run_id: str) -> list[RunEvent | _ChildEnded]:
    """Every control message that arrived while the workflow was busy, in order."""
    events: list[RunEvent | _ChildEnded] = []
    while (message := DBOS.recv(CONTROL_TOPIC, timeout_seconds=0)) is not None:
        if message != WAKE and (event := _as_event(message)) is not None:
            events.append(event)
    return events


def _wait(run_id: str) -> dict[str, Any]:
    with _waiting_lock:
        _waiting.add(run_id)
    try:
        # Checked after registering: shutdown sets _stopping, then wakes the runs registered,
        # so a run registering later sees _stopping here instead of waiting out the hour.
        while not _stopping.is_set():
            message = DBOS.recv(CONTROL_TOPIC, timeout_seconds=3600)
            if message is not None and message != WAKE:
                return message
        raise _Shutdown(run_id)
    finally:
        with _waiting_lock:
            _waiting.discard(run_id)


@DBOS.step(name="swarmify.load")
def _load(run_id: str) -> tuple[list[str], str, list[str], int, str | None, bool]:
    run = _current().storage.get_run(run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    kinds, stages = [s.type for s in run.stages], [s.status for s in run.stages]
    return kinds, run.status, stages, run.send_backs, run.parent_run_id, run.over_budget


@DBOS.step(name="swarmify.start_child")
def _start_child(run_id: str, index: int) -> str:
    """Record the child run of the workflow stage at `index` and link it, once per visit: a
    retried step finds the link and returns the same child."""
    runner = _current()
    run = runner.storage.get_run(run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    if (child := run.stages[index].child) is not None:
        return child.id
    pin = _child_pin(runner, run)
    _, stage = runner.storage.stage_def(run_id, index)
    pinned = pin(str(stage.config.get("runs") or ""))
    now = datetime.now(UTC).isoformat()
    child = Run(
        new_id("run"), pinned.world_id, pinned.workflow_id, pinned.version, pinned.name,
        "queued", now, now, [], parent_run_id=run_id, baseline=run.baseline,
        budget_usd=runner.budget_for(pinned.world_id, parent_run_id=run_id),
    )  # fmt: skip
    # ponytail: a crash between these two leaves an unlinked queued child that launch() runs
    # and whose end nobody reads; one storage call for both if that window ever bites
    child = runner.storage.add_run(child, pinned.stages)
    runner.storage.set_child(run_id, index, child.id)
    return child.id


def _child_pin(runner: Runner, run: Run) -> Pin:
    """The pin a child of `run` starts with. Raises when there is none or the child would nest
    deeper than MAX_DEPTH (ADR 0013)."""
    if runner.pin is None:
        raise NotImplementedError("workflow stages need a pin to find their workflow")
    depth, above = 1, run.parent_run_id
    while above is not None:
        depth, parent = depth + 1, runner.storage.get_run(above)
        above = parent.parent_run_id if parent else None
    if depth > MAX_DEPTH:
        raise ValueError(f"child runs nest at most {MAX_DEPTH} deep")
    return runner.pin


def _fan_out(run_id: str, index: int, entering: bool) -> tuple[bool, RunEvent | None, str | None]:
    """Move the fan-out stage at `index` on (ADR 0031): start pending Items up to its
    `max_parallel`, enqueue their children and, once every Item settled, finish the stage or
    raise its items gate when some failed or, with `approve`, some are not approved yet.
    Returns (whether the stage fans out, the event its settling makes or None while children
    run, an error that fails it)."""
    try:
        started, outcome = _fan_next(run_id, index, entering)
    except Exception as exc:
        return True, StageFailed(index), f"{type(exc).__name__}: {exc}"
    for child in started:
        with SetWorkflowID(child):  # a workflow-level op, so a replay never enqueues twice
            DBOS.enqueue_workflow(CHILD_QUEUE, execute_run, child)
    settled = {"done": StageFinished(index), "failed": GateRaised(index)}.get(outcome)
    return outcome != "plain", settled, None


@DBOS.step(name="swarmify.fan_next")
def _fan_next(run_id: str, index: int, entering: bool) -> tuple[list[str], str]:
    """Start the next children of the fan-out stage at `index`. Returns the ids started and
    `plain` (no for_each), `wait` (children run), `done` or `failed` (every Item settled, some
    failed or, with `approve`, not approved: its gate opens; the name stays for DBOS replays).
    On `entering` the stage just became running: its visit's Items are planned, or, on a retry
    from its gate, the failed ones go again (with `approve` only the Items sent back go again,
    already started by `review_items`)."""
    runner = _current()
    run = runner.storage.get_run(run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    stage_id, stage = runner.storage.stage_def(run_id, index)
    if not stage.config.get("for_each"):
        return [], "plain"
    if run.stages[index].status != "running":
        return [], "wait"  # a child that ended after the stage moved on
    visit = run.send_backs
    rows = runner.storage.items(run_id, index, visit)
    if not rows:
        rows = _plan(runner, run, index, stage, visit)
    elif entering and not stage.config.get("approve"):
        for _, view in rows:
            if view.status in ("failed", "aborted"):
                runner.storage.set_item(run_id, index, visit, view.key, "pending", None)
        rows = runner.storage.items(run_id, index, visit)
    # A child that ended while the stage was not running (one sent back from its gate before
    # the stage ran again) had its end ignored: settle it now.
    for _, view in rows:
        if view.status == "running" and view.child is not None:
            ended = runner.storage.get_run(view.child.id)
            if ended is not None and ended.status in TERMINAL:
                _take_item(runner, run_id, ended, ended.status)
    rows = runner.storage.items(run_id, index, visit)
    held = run.status == "paused" or runner.storage.stop_of(run_id) is not None
    live = sum(1 for _, v in rows if v.status == "running")
    most = int(stage.config.get("max_parallel") or 2)
    started: list[str] = []
    for item, view in rows:
        if held or live >= most:
            break
        if view.status == "pending":
            started.append(_start_item(runner, run, index, visit, item, stage))
            live += 1
    rows = runner.storage.items(run_id, index, visit)
    if any(v.status in ("pending", "running") for _, v in rows):
        return started, "wait"
    _join(runner, run, index, stage_id, [v for _, v in rows])
    approve = bool(stage.config.get("approve"))
    passed = all(v.status == "done" and (v.approved or not approve) for _, v in rows)
    return started, "done" if passed else "failed"


def _plan(
    runner: Runner, run: Run, index: int, stage: StageDef, visit: int
) -> list[tuple[Item, ItemView]]:
    """Record the Items of a new visit. A revisit whose note names Items (by key or title,
    whole words, any case) runs only those, except ones named only in a sentence that says to
    keep them (`_KEEP`); the others keep what their last visit gathered."""
    if runner.items is None:
        raise NotImplementedError("fan-out stages need items to read their source")
    items, keys = [], set()
    for item in runner.items(run, index, stage):
        key, n = item.key, 1
        while key in keys:  # two cards of one slug still get a folder each
            n, key = n + 1, f"{item.key}-{n + 1}"
        keys.add(key)
        items.append(Item(key, item.title, item.file, item.data))
    note = run.send_back_note(stage.name)
    named = {i.key for i in items if any(_says(note, w) for w in (i.key, i.title))}
    asks = [c for c in re.split(r"[.!?](?:\s|$)|[;\n]", note) if not _KEEP.search(c)]
    redo = {i.key for i in items if any(_says(c, w) for c in asks for w in (i.key, i.title))}
    before = {v.key: v for _, v in runner.storage.items(run.id, index)} if visit else {}
    rows = [
        (i, before[i.key] if named and i.key not in redo and i.key in before else _fresh(i))
        for i in items
    ]
    runner.storage.add_items(run.id, index, visit, rows)
    return rows


def _fresh(item: Item) -> ItemView:
    return ItemView(item.key, item.title, "pending")


# A note's sentence with one of these words names Items to keep, not to run again.
_KEEP = re.compile(r"\b(keeps?|kept|pass|passes|fine|leave|unchanged|as is|as they are)\b", re.I)


def _says(note: str, word: str) -> bool:
    return bool(word) and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", note, re.I) is not None


def _start_item(
    runner: Runner, run: Run, index: int, visit: int, item: Item, stage: StageDef
) -> str:
    """Record the child run of one Item, with the Item as its given input, and link it."""
    pinned = _child_pin(runner, run)(str(stage.config.get("runs") or ""))
    now = datetime.now(UTC).isoformat()
    child = Run(
        new_id("run"), pinned.world_id, pinned.workflow_id, pinned.version,
        f"{pinned.name}: {item.title}", "queued", now, now, [], parent_run_id=run.id,
        baseline=run.baseline,
    )  # fmt: skip
    given = run_folder(runner.data_dir, child) / "given"
    given.mkdir(parents=True, exist_ok=True)
    name = str(stage.config.get("as_") or "item.md")
    if item.file:
        files = sorted(with_sidecar(Path(item.file)), key=lambda f: len(f.name))
        for file, target in zip(files, (name, name + ".md"), strict=False):
            shutil.copyfile(file, given / target)
    else:
        (given / name).write_text(json.dumps(item.data, indent=2, ensure_ascii=False) + "\n")
    # ponytail: a crash between these two leaves an unlinked queued child, as _start_child does
    child = runner.storage.add_run(child, pinned.stages)
    runner.storage.set_item(run.id, index, visit, item.key, "running", child.id)
    return child.id


def _join(runner: Runner, run: Run, index: int, stage_id: str, views: list[ItemView]) -> None:
    """Write the stage's `items.json`: every Item with its status, child and gathered files."""
    folder = stage_folder(runner.data_dir, run, index) / "artifacts"
    folder.mkdir(parents=True, exist_ok=True)
    out = [
        {
            "key": v.key,
            "title": v.title,
            "status": v.status,
            "child_run_id": v.child.id if v.child else None,
            "files": sorted(
                str(p.relative_to(folder)) for p in (folder / v.key).rglob("*") if p.is_file()
            ),
        }
        for v in views
    ]
    path = folder / "items.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    world = runner.data_dir / "worlds" / run.world_id
    runner.storage.record(
        Event.new(
            "artifact.created",
            world_id=run.world_id,
            run_id=run.id,
            payload={
                "type": "file",
                "path": str(path.relative_to(world)),
                "author": f"workflow:{run.stages[index].name}",
                "stage_id": stage_id,
            },
        )
    )


def _take_item(
    runner: Runner, run_id: str, child: Run, status: str
) -> tuple[int, str | None, bool] | None:
    """Settle the Item a fan-out child worked on: gather its `take` artifacts into the stage's
    `<key>/` folder, or record why it failed. None when no running visit waits on it."""
    found = runner.storage.item_of(child.id)
    run = runner.storage.get_run(run_id)
    if found is None or run is None or found[0] != run_id:
        return None
    _, index, visit, key = found
    stage = run.stages[index]
    current = {v.key: v for _, v in runner.storage.items(run_id, index, visit)}.get(key)
    if stage.status != "running" or visit != run.send_backs or current is None:
        return None
    if current.status != "running" or current.child is None or current.child.id != child.id:
        return None
    folder = stage_folder(runner.data_dir, run, index) / "artifacts" / key
    shutil.rmtree(folder, ignore_errors=True)  # what an earlier visit gathered gives way
    error: str | None = None
    if status != "done":
        failed = next((s.error for s in child.stages if s.status == "failed" and s.error), None)
        error = failed or f"child run {child.name} ended {status}"
    else:
        names = [
            str(n) for n in runner.storage.stage_def(run_id, index)[1].config.get("take") or []
        ]
        try:
            if names:
                if runner.take is None:
                    raise NotImplementedError("taking artifacts needs a take")
                runner.take(child, run, stage.id, names, key)
        except Exception as exc:
            shutil.rmtree(folder, ignore_errors=True)
            error = f"{type(exc).__name__}: {exc}"
    settled = "done" if error is None else ("aborted" if status == "aborted" else "failed")
    runner.storage.set_item(run_id, index, visit, key, settled, child.id, error)
    return index, None, True


@DBOS.step(name="swarmify.take")
def _take(run_id: str, child_run_id: str, status: str) -> tuple[int, str | None, bool] | None:
    """The stage a child ended for, its error and whether it is a fan-out stage's Item, after
    copying the artifacts it names into the stage. None when no running stage waits on this
    child."""
    runner = _current()
    child = runner.storage.get_run(child_run_id)
    if child is not None and (fan := _take_item(runner, run_id, child, status)) is not None:
        return fan
    run = runner.storage.get_run(run_id)
    stage = next(
        (s for s in (run.stages if run else []) if s.child and s.child.id == child_run_id),
        None,
    )
    if run is None or child is None or stage is None or stage.status != "running":
        return None
    if status != "done":
        return stage.index, f"child run {child.name} ended {status}", False
    names = [
        str(n) for n in runner.storage.stage_def(run_id, stage.index)[1].config.get("take") or []
    ]
    try:
        if names:
            if runner.take is None:
                raise NotImplementedError("taking artifacts needs a take")
            runner.take(child, run, stage.id, names)
    except Exception as exc:
        return stage.index, f"{type(exc).__name__}: {exc}", False
    return stage.index, None, False


@DBOS.step(name="swarmify.drop_change")
def _drop_change(change_id: str, reason: str) -> None:
    _current().storage.remove_change(change_id, reason)


@DBOS.step(name="swarmify.save")
def _save(run_id: str, state: RunState, error: str | None, gate_opened: bool) -> None:
    """Save the state; when a gate opened or the run ended, record the event and notify."""
    # ponytail: one step, so a crash between save, record and notify replays all three and the
    # world stream gets the run.updated and gate.opened or run.ended events twice. Split into
    # steps, or key events by (run, state), if duplicates start to matter.
    runner = _current()
    before = runner.storage.get_run(run_id)
    run = runner.storage.save_state(run_id, state, error)
    if run is None:
        return
    family_budget = before is not None and before.over_budget and run.parent_run_id is not None
    if before is not None and (closed := gate_closed(before, run)) and not family_budget:
        runner.notify(closed)  # a child's budget gate is its top run's: set_budget closes it
    if gate_opened:
        index = state.stages.index("waiting")
        _, stage = runner.storage.stage_def(run_id, index)
        shown = runner.preview_after(run, index)
        kind, ask = stage.config.get("kind") or "approve", stage.config.get("ask") or ""
        if stage.type == "workflow":  # a fan-out stage whose Items settled: failed or to review
            views = [v for _, v in runner.storage.items(run_id, index)]
            kind, ask = items_gate(stage.config, views)
        at, visit, name = run, state.send_backs, stage.name
        if state.over_budget:
            top, shown = runner.top(run), None
            kind, ask = "budget", budget.ask(top.spent_usd, top.budget_usd or 0.0)
            if top.id != run.id:
                # One budget gate for the family, on its top run (ADR 0013): the first member
                # to stop announces it. Two stopping at once may both announce; never neither.
                over = [
                    m
                    for m in runner.family(top.id)
                    if (r := runner.storage.get_run(m))
                    and r.over_budget
                    and r.status not in TERMINAL
                ]
                if min(over, default=run.id) != run.id:
                    return
                active = next((s for s in top.stages if s.status in ("running", "waiting")), None)
                at, visit = top, top.send_backs
                index, name = (active.index, active.name) if active else (index, name)
        event = Event.new(
            "gate.opened",
            world_id=at.world_id,
            run_id=at.id,
            payload={
                "run_id": at.id,
                "name": at.name,
                "stage_index": index,
                "visit": visit,
                "stage": name,
                "kind": kind,
                "ask": ask,
                "publish": asdict(shown) if shown else None,
            },
        )
    elif state.status in TERMINAL:
        event = ended_event(run)
    else:
        return
    runner.storage.record(event)
    runner.notify(event)


OUTCOMES = {
    "approve": "approved",
    "approve_items": "approved",
    "pick": "picked",
    "confirm": "confirmed",
    "skill": "answered",
}


def gate_closed(before: Run, after: Run) -> Event | None:
    """`gate.closed` when the gate `before` waited at is no longer waiting in `after`: answered
    (by whom, when it was answered from outside the web UI), sent back, given money or aborted.
    A Notification only, for a Remote Approval to update the message it posted; never recorded."""
    index = next((s.index for s in before.stages if s.status == "waiting"), None)
    if index is None:
        return None
    same = (after.stages[index].status, after.send_backs, after.over_budget)
    if same == ("waiting", before.send_backs, before.over_budget):
        return None
    decision = next((d for d in reversed(after.decisions) if d.stage_index == index), None)
    rerun = next((c for c in reversed(after.changes) if c.kind in RERUNS), None)
    resolution: dict[str, Any] = {"outcome": "aborted"}
    again = after.stages[index].status == "running" and after.send_backs == before.send_backs
    if after.status != "aborted" and before.over_budget:
        resolution = {"outcome": "budget_added"}
    elif after.status != "aborted" and again and before.stages[index].config.get("approve"):
        # Items of a fan-out stage with `approve` were sent back (review_items): no decision.
        resolution = {"outcome": "sent_back", "send_back_to": before.stages[index].name}
    elif after.status != "aborted" and rerun and (not decision or rerun.at > decision.decided_at):
        resolution = {
            "outcome": "rerun",
            "send_back_to": rerun.stage_name,
            "note": rerun.payload["note"],
            "by": rerun.by,
        }
    elif after.status != "aborted" and decision is not None:
        resolution = {
            "outcome": "sent_back"
            if decision.send_back_to
            else OUTCOMES.get(decision.kind, "answered"),
            "choice": decision.choice,
            "send_back_to": decision.send_back_to,
            "note": decision.note,
            "by": decision.by,
        }
        if len(decision.artifacts) > 1:
            resolution["picked"] = [decision.titles.get(a, a) for a in decision.artifacts]
    return Event.new(
        "gate.closed",
        world_id=after.world_id,
        run_id=after.id,
        payload={
            "run_id": after.id,
            "name": after.name,
            "stage_index": index,
            "visit": before.send_backs,
            "stage": before.stages[index].name,
            "resolution": resolution,
        },
    )


@DBOS.step(name="swarmify.execute")
def _execute(run_id: str, index: int) -> bool:
    """True when the stage raised a gate of its own instead of finishing (a retro's proposal)."""
    runner = _current()
    stage_id, stage = runner.storage.stage_def(run_id, index)
    calls: dict[str, Callable[[Run, str, StageDef], Any] | None] = {
        "connector": runner.connector,
        "swarm": runner.swarm,
        "retro": runner.retro,
    }
    call = calls.get(stage.type)
    if call is None:
        raise NotImplementedError(f"{stage.type} stages cannot run yet")
    run = runner.storage.get_run(run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    shown = runner.previews(run, stage, {}) if stage.type == "connector" else None
    if shown is not None and not shown.private:
        before = run.stages[index - 1] if index > 0 else None
        if before is None or before.type != "gate" or before.status != "done":
            # The editor refuses this too, but a connector can turn publish after the save.
            raise PermissionError(
                f"'{stage.name}' calls a publish connector, so it runs only right after an answered"
                " gate (ADR 0007)"
            )
    try:
        return bool(call(run, stage_id, stage))
    except OverBudget:
        if runner.storage.stop_of(run_id):  # read in the step, so a replay sees the same answer
            raise _Halted() from None
        raise
