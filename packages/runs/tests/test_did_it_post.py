"""The "Did this post go out?" gate at the runner's seam: a connector stage that could not tell
whether its publish posted waits at a gate of kind `posted` with its question, which the inbox
lists; either answer (It posted, Post it now) runs the stage again, Abort ends the run."""

import pytest

from runs_fakes import FakeNotifier, wait_for
from swarmify_runs import Illegal, Runner, StageDef
from swarmify_runs.model import Run

PUBLISH = StageDef("publish", "connector", {"calls": "youtube"})
ASK = "YouTube was cut off while posting and could not check whether it posted: gone."


class Unsure:
    """Asks on its first call, then finishes; records every call."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, run: Run, stage_id: str, stage: StageDef) -> str | None:
        self.calls += 1
        return ASK if self.calls == 1 else None


def status(runner: Runner, run_id: str) -> str:
    run = runner.get(run_id)
    assert run is not None
    return run.status


@pytest.mark.parametrize("choice", ["posted", "post"])
def test_either_answer_runs_the_stage_again_and_the_run_goes_on(
    runner: Runner, notifier: FakeNotifier, choice: str
) -> None:
    runner.connector = unsure = Unsure()
    run = runner.start("world_1", "wf_1", 1, "Short", [PUBLISH])
    wait_for(lambda: status(runner, run.id) == "waiting_gate")
    (gate,) = runner.gates()
    assert (gate.kind, gate.ask, gate.publish) == ("posted", ASK, None)
    wait_for(lambda: any(e.type == "gate.opened" for e in notifier.events))
    (opened,) = [e for e in notifier.events if e.type == "gate.opened"]
    assert (opened.payload["kind"], opened.payload["ask"]) == ("posted", ASK)

    with pytest.raises(ValueError, match="takes choice posted or post"):
        runner.decide(run.id)
    with pytest.raises(Illegal, match="cannot send back"):
        runner.decide(run.id, choice="post", send_back_to="publish")
    runner.decide(run.id, choice=choice, person=True)
    wait_for(lambda: status(runner, run.id) == "done")
    assert unsure.calls == 2
    done = runner.get(run.id)
    assert done is not None and done.stages[0].error is None
    assert (done.decisions[0].kind, done.decisions[0].choice) == ("posted", choice)


def test_abort_ends_the_run_at_the_gate(runner: Runner) -> None:
    runner.connector = unsure = Unsure()
    run = runner.start("world_1", "wf_1", 1, "Short", [PUBLISH])
    wait_for(lambda: status(runner, run.id) == "waiting_gate")
    runner.abort(run.id, person=True)
    wait_for(lambda: status(runner, run.id) == "aborted")
    assert unsure.calls == 1 and runner.gates() == []
