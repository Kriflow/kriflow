"""A stage's `not_before` on real DBOS: the run waits for a local time, holding no slot, and
the wait survives a restart. The clock is shifted so the time is seconds away."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kriflow_kernel import next_local
from kriflow_runs import Runner, SqliteStorage, StageDef
from runs_fakes import FakeConnector, wait_for

PICK = StageDef("approve", "gate", {"ask": "Post it?"})


def soon(seconds: float) -> tuple[str, datetime, Callable[[], datetime]]:
    """A `not_before` two hours of wall time away, the time it next comes, and a clock that
    reads `seconds` before it."""
    now = datetime.now(UTC)
    rule = f"{now + timedelta(hours=2):%H:%M} UTC"
    wake = next_local(rule, now)
    shift = wake - now - timedelta(seconds=seconds)
    return rule, wake, lambda: datetime.now(UTC) + shift


def post(rule: str) -> StageDef:
    return StageDef("post", "connector", {"calls": "publish", "not_before": rule})


def make(
    storage: SqliteStorage, tmp_path: Path, connector: FakeConnector, clock: Callable[[], datetime]
) -> Runner:
    return Runner(
        storage, tmp_path / "dbos.sqlite", connector, tmp_path, poll_seconds=0.05,
        cap_for=lambda _w: 1, clock=clock,
    )  # fmt: skip


def test_a_stage_waits_for_its_local_time_holding_no_slot_then_runs(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    rule, wake, clock = soon(3)
    runner = make(storage, tmp_path, connector, clock)
    runner.launch()
    try:
        run = runner.start("world_1", "wf_1", 1, "Timed", [post(rule)])
        wait_for(lambda: getattr(runner.get(run.id), "status", None) == "running")
        held = runner.get(run.id)
        assert held is not None and held.stages[0].status == "running"
        assert held.stages[0].runs_at == wake.isoformat()
        # The world's one slot is free while it waits: another run goes past it.
        other = runner.start("world_1", "wf_2", 1, "Other", [StageDef("render", "connector")])
        wait_for(lambda: getattr(runner.get(other.id), "status", None) == "done")
        assert [name for _, name in connector.calls] == ["render"]

        wait_for(lambda: getattr(runner.get(run.id), "status", None) == "done")
        assert clock() >= wake
        assert [name for _, name in connector.calls] == ["render", "post"]
    finally:
        runner.shutdown()


def test_a_stage_reached_after_its_time_waits_for_the_next_day_and_aborts_while_it_waits(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    rule = f"{datetime.now(UTC) - timedelta(hours=1):%H:%M} UTC"
    runner = make(storage, tmp_path, connector, lambda: datetime.now(UTC))
    runner.launch()
    try:
        run = runner.start("world_1", "wf_1", 1, "Late", [PICK, post(rule)])
        wait_for(lambda: getattr(runner.get(run.id), "status", None) == "waiting_gate")
        runner.decide(run.id)  # answered after today's time
        wait_for(lambda: (r := runner.get(run.id)) is not None and r.stages[1].runs_at is not None)
        held = runner.get(run.id)
        assert held is not None and held.status == "running"
        started = datetime.fromisoformat(held.stages[1].started_at or "")
        runs_at = datetime.fromisoformat(held.stages[1].runs_at or "")
        assert runs_at == next_local(rule, started)
        assert timedelta(hours=22) < runs_at - started <= timedelta(hours=23)

        runner.abort(run.id)
        wait_for(lambda: getattr(runner.get(run.id), "status", None) == "aborted")
        assert connector.calls == []
    finally:
        runner.shutdown()


def test_a_paused_wait_waits_again_once_resumed(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    rule, _, clock = soon(2)
    runner = make(storage, tmp_path, connector, clock)
    runner.launch()
    try:
        run = runner.start("world_1", "wf_1", 1, "Paused", [post(rule)])
        wait_for(lambda: (r := runner.get(run.id)) is not None and r.stages[0].runs_at is not None)
        runner.pause(run.id)
        wait_for(lambda: getattr(runner.get(run.id), "status", None) == "paused")
        paused = runner.get(run.id)
        assert paused is not None and paused.stages[0].status == "pending"
        runner.resume(run.id)
        wait_for(lambda: getattr(runner.get(run.id), "status", None) == "done")
        assert [name for _, name in connector.calls] == ["post"]
    finally:
        runner.shutdown()


def test_a_wait_survives_a_restart(
    tmp_path: Path, storage: SqliteStorage, connector: FakeConnector
) -> None:
    rule, wake, clock = soon(6)
    first = make(storage, tmp_path, connector, clock)
    first.launch()
    run = first.start("world_1", "wf_1", 1, "Restart", [post(rule)])
    wait_for(lambda: (r := first.get(run.id)) is not None and r.stages[0].runs_at is not None)
    first.shutdown()
    assert connector.calls == []
    time.sleep(3)  # down a while: the wait must not restart its count from the relaunch

    second = make(storage, tmp_path, connector, clock)
    second.launch()
    done_at: list[datetime] = []

    def done() -> bool:
        if getattr(storage.get_run(run.id), "status", None) != "done":
            return False
        done_at.append(clock())
        return True

    try:
        wait_for(done)
        assert wake <= done_at[0] < wake + timedelta(seconds=2)
        assert [name for _, name in connector.calls] == ["post"]
    finally:
        second.shutdown()
