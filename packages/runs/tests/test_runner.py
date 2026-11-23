"""The DBOS runner at its seam: real DBOS on SQLite, a fake connector behind it."""

from contextlib import closing

from dbos import DBOS

from kriflow_kernel.events import read_after
from kriflow_kernel.sqlite import connect
from kriflow_runs import Runner, SqliteStorage, StageDef, queue_name
from runs_fakes import FakeConnector, wait_for

RENDER = StageDef("render", "connector", {"calls": "remotion_render"})
PICK = StageDef("pick", "gate", {"ask": "Pick one.", "kind": "pick"})


def status(runner: Runner, run_id: str) -> str:
    run = runner.get(run_id)
    assert run is not None
    return run.status


def test_a_run_executes_its_connector_waits_at_its_gate_and_finishes(
    runner: Runner, connector: FakeConnector, storage: SqliteStorage
) -> None:
    run = runner.start("world_1", "wf_1", 3, "Weekly short", [RENDER, PICK])
    assert run.status == "queued" and run.workflow_version == 3
    assert [s.status for s in run.stages] == ["pending", "pending"]

    wait_for(lambda: status(runner, run.id) == "waiting_gate")
    waiting = runner.get(run.id)
    assert waiting is not None
    assert [s.status for s in waiting.stages] == ["done", "waiting"]
    assert connector.calls == [(waiting.stages[0].id, "render")]
    assert waiting.stages[0].started_at and waiting.stages[0].ended_at

    runner.decide(run.id, choice="idea 2")
    wait_for(lambda: status(runner, run.id) == "done")
    assert connector.calls == [(waiting.stages[0].id, "render")]

    def events_now() -> list:
        with closing(connect(storage.path)) as conn:
            return read_after(conn, "world_1", 0)

    # The done state is saved a moment before run.ended is recorded, in the same step.
    wait_for(lambda: any(e.type == "run.ended" for e in events_now()))
    events = events_now()
    assert all(e.run_id == run.id for e in events)
    assert [e.type for e in events if e.type != "run.updated"] == ["gate.opened", "run.ended"]
    events = [e for e in events if e.type == "run.updated"]
    seen = [(e.payload["status"], [s["status"] for s in e.payload["stages"]]) for e in events]
    assert seen == [
        ("queued", ["pending", "pending"]),
        ("running", ["running", "pending"]),
        ("waiting_gate", ["done", "waiting"]),
        ("done", ["done", "done"]),
    ]


def test_a_failing_stage_fails_the_run_with_its_error(runner: Runner) -> None:
    run = runner.start("world_1", "wf_1", 1, "Broken", [StageDef("explode", "connector"), PICK])
    wait_for(lambda: status(runner, run.id) == "failed")
    failed = runner.get(run.id)
    assert failed is not None
    assert [s.status for s in failed.stages] == ["failed", "pending"]
    assert failed.stages[0].error == "RuntimeError: connector said no"


def test_stage_types_without_an_executor_fail_honestly(runner: Runner) -> None:
    swarm = StageDef("research", "swarm", {"goal": "Find ideas.", "done_when": "5 ideas"})
    run = runner.start("world_1", "wf_1", 1, "Research", [swarm])
    wait_for(lambda: status(runner, run.id) == "failed")
    failed = runner.get(run.id)
    assert failed is not None and "swarm stages cannot run yet" in (failed.stages[0].error or "")


def test_a_swarm_stage_runs_through_the_swarm_it_was_given(runner: Runner) -> None:
    swarm = FakeConnector()
    runner.swarm = swarm
    stages = [StageDef("research", "swarm", {"goal": "g"}), StageDef("explode", "swarm")]
    run = runner.start("world_1", "wf_1", 1, "Research", stages)
    wait_for(lambda: status(runner, run.id) == "failed")
    ended = runner.get(run.id)
    assert ended is not None and [s.status for s in ended.stages] == ["done", "failed"]
    assert swarm.calls == [(ended.stages[0].id, "research"), (ended.stages[1].id, "explode")]


def test_runs_waiting_at_gates_go_past_the_run_cap(runner: Runner) -> None:
    """The run cap of two counts stages at work, never runs waiting for a person (#486)."""
    a = [runner.start("world_a", "wf_a", 1, f"A{i}", [PICK]) for i in range(3)]
    wait_for(lambda: all(status(runner, r.id) == "waiting_gate" for r in a))
    runner.decide(a[0].id, choice="A")
    wait_for(lambda: status(runner, a[0].id) == "done")


def test_runs_list_newest_first_and_go_with_their_world(runner: Runner) -> None:
    old = runner.start("world_1", "wf_1", 1, "Old", [PICK])
    new = runner.start("world_1", "wf_1", 1, "New", [PICK])
    assert [r.id for r in runner.list_all("world_1")] == [new.id, old.id]
    assert runner.list_all("world_2") == []
    wait_for(lambda: status(runner, new.id) == "waiting_gate")

    assert DBOS.retrieve_queue(queue_name("world_1")) is not None
    runner.delete_world("world_1")
    assert runner.list_all("world_1") == []
    assert runner.get(old.id) is None
    assert DBOS.retrieve_queue(queue_name("world_1")) is None  # its polling thread stops
