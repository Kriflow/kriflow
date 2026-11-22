"""The upgrade fixture (kriflow#592): runs left in flight by the code before the module rename,
whose DBOS records pickle `swarmify_*` module paths, finish on this code. Before the rename it
proves the fixture is sound; after, that no recorded run was stranded. upgrade_record.py
records it and says when to record it again. The same runs with their records rewritten to
`kriflow_*` paths, as the renamed code writes them, finish too: a rollback to this commit."""

import base64
import contextlib
import pickle
import re
import shutil
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from runs_fakes import wait_for
from swarmify_kernel.sqlite import connect
from swarmify_runs import Illegal, Runner
from swarmify_runs.machine import RunState
from upgrade_record import EVERY_SECOND, FIXTURE, make

RECORDED = ["At a gate", "On a time hold", "Fan-out", "Queued 1", "Queued 2"]


def pickled_modules(dbos: Path) -> set[str]:
    """The module paths pickled in the inputs of the workflows left in flight."""
    with closing(connect(dbos)) as conn:
        rows = conn.execute(
            "SELECT i.inputs FROM workflow_input i JOIN workflow_status s USING (workflow_uuid)"
            " WHERE s.status IN ('PENDING', 'ENQUEUED')"
        ).fetchall()
    return {m.decode() for (i,) in rows for m in re.findall(rb"\w+_\w+\.\w+", base64.b64decode(i))}


def by_name(r: Runner, worlds: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for world in worlds:
        for run in r.list_all(world):
            out.setdefault(run.name, []).append(run.id)
    return out


def as_renamed(dbos: Path) -> None:
    """Rewrite the pickled inputs as the renamed code writes them: RunState from kriflow_runs."""
    with closing(connect(dbos)) as conn:
        for uuid, inputs in conn.execute("SELECT workflow_uuid, inputs FROM workflow_input"):
            obj = pickle.loads(base64.b64decode(inputs))
            RunState.__module__ = "kriflow_runs.machine"  # found through the alias
            try:
                renamed = pickle.dumps(obj)
            finally:
                RunState.__module__ = "swarmify_runs.machine"
            conn.execute(
                "UPDATE workflow_input SET inputs = ? WHERE workflow_uuid = ?",
                (base64.b64encode(renamed).decode(), uuid),
            )
        conn.commit()


def test_runs_recorded_before_the_rename_finish_and_the_schedule_fires(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE, tmp_path / "data")
    assert "swarmify_runs.machine" in pickled_modules(tmp_path / "data" / "dbos.sqlite")
    finish(tmp_path / "data")


def test_after_a_rollback_runs_the_renamed_code_recorded_finish(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE, tmp_path / "data")
    as_renamed(tmp_path / "data" / "dbos.sqlite")
    modules = pickled_modules(tmp_path / "data" / "dbos.sqlite")
    assert "kriflow_runs.machine" in modules and "swarmify_runs.machine" not in modules
    finish(tmp_path / "data")


def finish(root: Path) -> None:
    """Launch over `root`, answer every gate, and expect each recorded run done and the
    recorded schedule to start a run."""
    worlds = ["world_gate", "world_hold", "world_fan", "world_queue", "world_sched"]
    launched = datetime.now(UTC).isoformat()
    # two days on: past the time hold's `not_before`, whenever it was recorded
    r = make(root, clock=lambda: datetime.now(UTC) + timedelta(days=2))
    r.launch()
    try:
        assert r.schedules() == {"wf_sched": EVERY_SECOND}
        runs = by_name(r, worlds)
        assert all(len(runs[name]) == 1 for name in RECORDED)
        (gate,), (fan,) = runs["At a gate"], runs["Fan-out"]

        wait_for(lambda: getattr(r.get(gate), "status", None) == "waiting_gate", 20)
        r.decide(gate, choice="idea 1")

        def fan_done() -> bool:
            for child in r.storage.child_run_ids(fan):
                if getattr(r.get(child), "status", None) == "waiting_gate":
                    with contextlib.suppress(Illegal):
                        r.decide(child, choice="idea 1")
            return getattr(r.get(fan), "status", None) == "done"

        wait_for(fan_done, 30)
        assert len(r.storage.child_run_ids(fan)) == 2  # the second Item ran after the first
        for name in RECORDED:
            wait_for(lambda name=name: getattr(r.get(runs[name][0]), "status", None) == "done", 30)

        def fired() -> bool:
            fresh = [x for x in r.list_all("world_sched") if x.created_at > launched]
            return any(x.status == "done" for x in fresh)

        wait_for(fired, 30)
        recorded_ticks = [x for x in r.list_all("world_sched") if x.created_at <= launched]
        wait_for(lambda: all(r.get(x.id).status == "done" for x in recorded_ticks), 30)  # type: ignore[union-attr]
    finally:
        r.shutdown()
