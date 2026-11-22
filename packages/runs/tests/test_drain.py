"""The Drain (Safe Deploys) at the runner's seam: real DBOS on SQLite, fake connector callables.
A drain waits only for stages executing in this process; a stage that would start meanwhile
waits, parked, and starts exactly once, after a cancel or on the next launch."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dbos import DBOS

from runs_fakes import FakeConnector, wait_for
from swarmify_runs import Pinned, Run, Runner, SqliteStorage, StageDef

RENDER = StageDef("render", "connector")
PICK = StageDef("pick", "gate", {"ask": "Pick one."})


class Slow(FakeConnector):
    """Blocks a stage named `slow` until released."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def __call__(self, run: Run, stage_id: str, stage: StageDef) -> None:
        super().__call__(run, stage_id, stage)
        if stage.name == "slow":
            self.release.wait(30)


def make(storage: SqliteStorage, tmp_path: Path, connector: Callable[..., None]) -> Runner:
    return Runner(storage, tmp_path / "dbos.sqlite", connector, tmp_path, poll_seconds=0.05)


def status(runner: Runner, run_id: str) -> str | None:
    return getattr(runner.get(run_id), "status", None)


def names(connector: FakeConnector) -> list[str]:
    return [name for _, name in connector.calls]


def test_a_drain_waits_for_the_stage_at_work_and_reports_it(
    tmp_path: Path, storage: SqliteStorage
) -> None:
    slow = Slow()
    runner = make(storage, tmp_path, slow)
    runner.launch()
    try:
        assert runner.drain_state().state == "serving"
        run = runner.start("world_1", "wf_1", 1, "Busy", [StageDef("slow", "connector")])
        wait_for(lambda: names(slow) == ["slow"])
        drain = runner.drain(60)
        assert drain.state == "draining"
        assert drain.seconds_left is not None and 55 < drain.seconds_left <= 60
        (work,) = drain.at_work
        assert (work.run_id, work.run_name, work.stage, work.type) == (
            run.id,
            "Busy",
            "slow",
            "connector",
        )
        slow.release.set()
        assert runner.wait_drained().state == "drained"
        wait_for(lambda: status(runner, run.id) == "done")
    finally:
        slow.release.set()
        runner.shutdown()


def test_gates_waits_children_and_queued_runs_do_not_hold_a_drain(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    runner = make(storage, tmp_path, connector)
    runner.pin = lambda _w: Pinned("world_1", "wf_child", 1, "Child", [RENDER])
    later = f"{datetime.now(UTC) + timedelta(hours=2):%H:%M} UTC"
    timed = StageDef("post", "connector", {"calls": "x", "not_before": later})
    runner.launch()
    try:
        gated = runner.start("world_1", "wf_1", 1, "Gated", [RENDER, PICK])
        waits = runner.start("world_1", "wf_2", 1, "Timed", [timed])
        wait_for(lambda: status(runner, gated.id) == "waiting_gate")
        wait_for(lambda: (r := runner.get(waits.id)) is not None and bool(r.stages[0].runs_at))
        assert runner.drain(60).state == "drained"

        # Started during the drain: the parent waits on its child, whose stage waits to start.
        parent = runner.start(
            "world_1", "wf_3", 1, "Parent", [StageDef("sub", "workflow", {"runs": "wf_child"})]
        )
        queued = runner.start("world_1", "wf_4", 1, "Queued", [RENDER])
        wait_for(lambda: (r := runner.get(parent.id)) is not None and r.stages[0].child is not None)
        runner.decide(gated.id)  # an answer during a drain is recorded; its next stage waits
        wait_for(lambda: (r := runner.get(gated.id)) is not None and r.stages[1].status == "done")
        assert runner.drain_state().state == "drained"
        assert status(runner, queued.id) == "queued"
        assert names(connector) == ["render"]  # the gated run's first stage, before the drain
    finally:
        runner.shutdown()


def test_a_cancelled_drain_starts_the_parked_stage(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    runner = make(storage, tmp_path, connector)
    runner.launch()
    try:
        runner.drain(60)
        run = runner.start("world_1", "wf_1", 1, "Parked", [RENDER])
        wait_for(lambda: bool(DBOS.list_workflows(name="swarmify.stage")))
        assert status(runner, run.id) == "queued"
        assert runner.cancel_drain().state == "serving"
        wait_for(lambda: status(runner, run.id) == "done")
        assert names(connector) == ["render"]
    finally:
        runner.shutdown()


def test_a_stage_parked_by_a_drain_starts_exactly_once_after_a_relaunch(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    first = make(storage, tmp_path, connector)
    first.launch()
    first.drain(60)
    run = first.start("world_1", "wf_1", 1, "Parked", [RENDER, RENDER])
    wait_for(lambda: bool(DBOS.list_workflows(name="swarmify.stage")))
    first.shutdown()
    assert connector.calls == []
    assert status(first, run.id) == "queued"

    second = make(storage, tmp_path, connector)
    second.launch()
    try:
        wait_for(lambda: status(second, run.id) == "done")
        assert names(connector) == ["render", "render"]  # each stage once
        stages = DBOS.list_workflows(name="swarmify.stage")
        assert sorted(w.status for w in stages) == ["SUCCESS", "SUCCESS"]
    finally:
        second.shutdown()


def test_at_the_limit_the_cut_stage_runs_again_after_the_relaunch(
    tmp_path: Path, storage: SqliteStorage
) -> None:
    slow = Slow()
    first = make(storage, tmp_path, slow)
    first.launch()
    run = first.start("world_1", "wf_1", 1, "Cut", [StageDef("slow", "connector"), RENDER])
    wait_for(lambda: names(slow) == ["slow"])
    first.drain(0.3)
    cut = first.wait_drained()
    assert cut.state == "draining" and cut.seconds_left == 0
    assert [w.stage for w in cut.at_work] == ["slow"]
    first.shutdown()

    # The cut call ends after DBOS stopped, so nothing of it is recorded. Its thread must end
    # before the relaunch, as the old process would have: in one process it would reach the
    # new DBOS.
    slow.release.set()
    wait_for(lambda: not [t for t in threading.enumerate() if t.name.startswith("dbos-executor")])
    second = make(storage, tmp_path, slow)
    second.launch()
    try:
        wait_for(lambda: status(second, run.id) == "done")
        assert names(slow) == ["slow", "slow", "render"]
    finally:
        second.shutdown()
