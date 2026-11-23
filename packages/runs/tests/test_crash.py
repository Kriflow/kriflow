"""Crash recovery: kill the process mid-stage, start a new one, and the run resumes at the
last completed stage. A completed step never runs again."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

from dbos import DBOS
from pytest import MonkeyPatch

from kriflow_kernel.events import read_after
from kriflow_kernel.sqlite import connect
from kriflow_runs import Runner, SqliteStorage, StageDef, queue_name
from runs_fakes import FakeConnector, wait_for

CHILD = Path(__file__).parent / "crash_child.py"


def child(root: Path, mode: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": str(CHILD.parent)}
    return subprocess.Popen(
        [sys.executable, str(CHILD), str(root), mode], stdout=subprocess.PIPE, text=True, env=env
    )


def test_killed_mid_stage_the_run_resumes_without_repeating_finished_stages(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls.log"
    first = child(tmp_path, "start")
    try:
        wait_for(lambda: log.exists() and log.read_text().splitlines() == ["a", "slow"], 30)
    finally:
        first.send_signal(signal.SIGKILL)
        first.wait()

    storage = SqliteStorage(tmp_path / "swarmify.db")
    (run,) = storage.list_runs("world_1")
    assert run.status == "running"
    assert [s.status for s in run.stages] == ["done", "running", "pending"]

    second = child(tmp_path, "resume")
    out, _ = second.communicate(timeout=60)
    assert second.returncode == 0

    # `a` finished before the kill and is not called again; `slow` never finished, so it reruns.
    assert log.read_text().splitlines() == ["a", "slow", "slow"]
    steps = json.loads(out.strip().splitlines()[-1])
    assert steps.count("swarmify.execute") == 2  # one per connector stage, none duplicated
    done = storage.get_run(run.id)
    assert done is not None and done.status == "done"
    with closing(connect(storage.path)) as conn:
        snapshots = [
            [s["status"] for s in e.payload["stages"]]
            for e in read_after(conn, "world_1", 0)
            if e.type == "run.updated"
        ]
    assert snapshots.count(["done", "running", "pending"]) == 1


def test_a_run_waiting_at_its_gate_survives_a_restart(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    stages = [StageDef("render", "connector"), StageDef("pick", "gate")]
    first = Runner(storage, tmp_path / "dbos.sqlite", connector, tmp_path, poll_seconds=0.05)
    first.launch()
    run = first.start("world_1", "wf_1", 1, "Restart", stages)
    wait_for(lambda: getattr(storage.get_run(run.id), "status", None) == "waiting_gate")
    first.shutdown()

    second = Runner(storage, tmp_path / "dbos.sqlite", connector, tmp_path, poll_seconds=0.05)
    second.launch()
    try:
        second.decide(run.id)
        wait_for(lambda: getattr(storage.get_run(run.id), "status", None) == "done")
    finally:
        second.shutdown()
    assert [name for _, name in connector.calls] == ["render"]


def test_runs_at_gates_from_before_stage_slots_go_on_and_hold_no_slot_after_a_restart(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector, monkeypatch: MonkeyPatch
) -> None:
    """#486: runs that reached their gates before stage slots replay their history on launch and
    go on without slots, and their world's queue, which capped runs then, caps them no more."""
    stages = [
        StageDef("render", "connector"),
        StageDef("pick", "gate"),
        StageDef("post", "connector"),
    ]

    def runner() -> Runner:
        return Runner(
            storage, tmp_path / "dbos.sqlite", connector, tmp_path, poll_seconds=0.05,
            cap_for=lambda _w: 1, worlds=lambda: ["world_1"],
        )  # fmt: skip

    def status(run_id: str) -> str | None:
        return getattr(storage.get_run(run_id), "status", None)

    with monkeypatch.context() as m:
        m.setattr(DBOS, "patch", staticmethod(lambda _name: False))  # the code before slots
        before = runner()
        before.launch()
        old = [before.start("world_1", "wf_1", 1, f"Old {i}", stages) for i in range(2)]
        for run in old:
            wait_for(lambda run_id=run.id: status(run_id) == "waiting_gate")
        DBOS.register_queue(
            queue_name("world_1"), global_concurrency=1, on_conflict="always_update"
        )
        before.shutdown()

    after = runner()
    after.launch()
    try:
        ids = [r.id for r in old]
        wait_for(lambda: {w.status for w in DBOS.list_workflows(workflow_ids=ids)} == {"PENDING"})
        for run in old:
            after.decide(run.id)
        for run in old:
            wait_for(lambda run_id=run.id: status(run_id) == "done")
        fresh = after.start("world_1", "wf_2", 1, "Fresh", [StageDef("render", "connector")])
        wait_for(lambda: status(fresh.id) == "done")
    finally:
        after.shutdown()
    assert sorted(name for _, name in connector.calls) == [
        "post",
        "post",
        "render",
        "render",
        "render",
    ]


def test_a_queued_run_left_behind_is_enqueued_on_launch(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    from kriflow_runs import Run

    stub = Run("run_left", "world_1", "wf_1", 1, "Left", "queued", "t", "t", [])
    storage.add_run(stub, [StageDef("render", "connector")])
    runner = Runner(storage, tmp_path / "dbos.sqlite", connector, tmp_path, poll_seconds=0.05)
    runner.launch()
    try:
        wait_for(lambda: getattr(storage.get_run("run_left"), "status", None) == "done")
    finally:
        runner.shutdown()


def test_a_run_reaching_its_gate_while_shutdown_runs_does_not_outlive_it(
    tmp_path: Path, storage: SqliteStorage
) -> None:
    """Shutdown wakes the runs waiting when it starts, a run waiting on its stage too. A stage
    that ends a moment later must not keep any thread waiting, or the process cannot exit; the
    run reaches its gate on the next launch."""
    release, ended = threading.Event(), threading.Event()

    def status() -> str | None:
        return getattr(storage.get_run(run.id), "status", None)

    class Late(Runner):
        def close(self) -> None:  # shutdown has woken the waiting runs; now this one waits
            release.set()
            wait_for(ended.is_set)
            time.sleep(0.2)
            super().close()

    def render(*_: object) -> None:
        release.wait(10)
        ended.set()

    stages = [StageDef("render", "connector"), StageDef("pick", "gate")]
    late = Late(storage, tmp_path / "dbos.sqlite", render, tmp_path)
    late.launch()
    run = late.start("world_1", "wf_1", 1, "Late", stages)
    wait_for(lambda: status() == "running")
    late.shutdown()
    wait_for(lambda: not [t for t in threading.enumerate() if t.name.startswith("dbos-executor")])
    again = Runner(storage, tmp_path / "dbos.sqlite", render, tmp_path, poll_seconds=0.05)
    again.launch()
    try:
        wait_for(lambda: status() == "waiting_gate")
    finally:
        again.shutdown()
