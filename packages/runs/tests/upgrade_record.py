"""Records the upgrade fixture (kriflow#592): a data folder with runs left in flight by this code,
which test_upgrade_fixture.py loads and finishes. Recorded once, by the code before the module
rename, so its DBOS records pickle `swarmify_*` module paths; the renamed code must still finish
them. Recorded by the renamed code it would test nothing, so after the rename record it only
from a checkout of the pre-rename commit. To record it again (say, after a DBOS upgrade changed
its system database), from the repo root:

    uv run python packages/runs/tests/upgrade_record.py

It replaces upgrade_fixture/ with: a run waiting at a gate, a run waiting on its `not_before`
time, a fan-out parent waiting on its first child (at a gate), two runs queued on a world with
one slot (stopped by a drain, as a deploy stops them: one stage holds the slot, one waits for
it, each with a pickled RunState), and an every-second schedule with the runs it started.

The fakes below serve the recording and the test alike.
"""

import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dbos import DBOS

from kriflow_runs import Item, Pinned, Run, Runner, SqliteStorage, StageDef, runner
from kriflow_runs.control import slot_queue
from runs_fakes import migrated, wait_for

FIXTURE = Path(__file__).parent / "upgrade_fixture"
EVERY_SECOND = "* * * * * *"
RENDER = StageDef("render", "connector", {"calls": "render"})
PICK = StageDef("pick", "gate", {"ask": "Pick one.", "kind": "pick"})
CHILD = Pinned("world_fan", "wf_child", 1, "Child", [PICK])
SCHEDULED = Pinned("world_sched", "wf_sched", 1, "Scheduled", [RENDER], schedule=EVERY_SECOND)
FAN = StageDef(
    "each", "workflow", {"runs": "wf_child", "for_each": "items", "max_parallel": 1, "take": []}
)


def pin(workflow_id: str, _number: int | None = None) -> Pinned:
    return {"wf_child": CHILD, "wf_sched": SCHEDULED}[workflow_id]


def items(_run: Run, _index: int, _stage: StageDef) -> list[Item]:
    return [Item("a", "A", data={"n": 1}), Item("b", "B", data={"n": 2})]


def take(*_args: object, **_kwargs: object) -> None:
    pass


def connector(_run: Run, _stage_id: str, _stage: StageDef) -> None:
    pass


def make(root: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Runner:
    return Runner(
        SqliteStorage(migrated(root)), root / "dbos.sqlite", connector, root,
        poll_seconds=0.05, pin=pin, take=take, items=items, cap_for=lambda _w: 1, clock=clock,
    )  # fmt: skip


def status(r: Runner, run_id: str) -> str | None:
    return getattr(r.get(run_id), "status", None)


def record(root: Path) -> None:
    r = make(root)
    r.launch()
    try:
        gate = r.start("world_gate", "wf_gate", 1, "At a gate", [RENDER, PICK])
        wait_for(lambda: status(r, gate.id) == "waiting_gate")

        rule = f"{datetime.now(UTC) + timedelta(hours=2):%H:%M} UTC"
        post = StageDef("post", "connector", {"calls": "publish", "not_before": rule})
        hold = r.start("world_hold", "wf_hold", 1, "On a time hold", [post])
        wait_for(lambda: (h := r.get(hold.id)) is not None and h.stages[0].runs_at is not None)
        wait_for(lambda: hold.id in runner._waiting)

        fan = r.start("world_fan", "wf_fan", 1, "Fan-out", [FAN])
        wait_for(lambda: len(r.storage.child_run_ids(fan.id)) == 1)
        (child,) = r.storage.child_run_ids(fan.id)
        wait_for(lambda: status(r, child) == "waiting_gate")

        r.schedule("wf_sched", EVERY_SECOND)
        wait_for(lambda: any(x.status == "done" for x in r.list_all("world_sched")))

        r.drain()  # as a deploy does: queued runs stop before their stage starts
        first = r.start("world_queue", "wf_queue", 1, "Queued 1", [RENDER])
        wait_for(lambda: (first.id, 0) in runner._parked)
        second = r.start("world_queue", "wf_queue", 1, "Queued 2", [RENDER])
        wait_for(
            lambda: bool(
                DBOS.list_workflows(status="ENQUEUED", queue_name=slot_queue("world_queue"))
            )
        )
        assert status(r, first.id) == status(r, second.id) == "queued"
    finally:
        r.shutdown()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record(root)
        shutil.rmtree(FIXTURE, ignore_errors=True)
        shutil.copytree(root, FIXTURE, ignore=shutil.ignore_patterns("*-wal", "*-shm"))
        for name in ("swarmify.db", "dbos.sqlite"):  # folds the WAL in and drops free pages
            (FIXTURE / name).unlink()
            with closing(sqlite3.connect(root / name)) as conn:
                conn.execute("VACUUM INTO ?", (str(FIXTURE / name),))
    print(f"recorded {FIXTURE}", file=sys.stderr)


if __name__ == "__main__":
    main()
