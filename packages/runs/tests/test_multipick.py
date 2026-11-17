"""Multi-pick: a pick gate takes up to `picks` cards in order; the rest are Parked unless the
operator Rejects them with a reason. Generic cards, no domain words."""

from pathlib import Path

import pytest
from test_gates import at, fetch

from swarmify_runs import Run, Runner, StageDef

MAKE = StageDef("make", "connector", {"calls": "maker"})
CHOOSE = StageDef("choose", "gate", {"ask": "Choose three.", "kind": "pick", "picks": 3})


def cards(runner: Runner, run: Run, n: int) -> list[str]:
    """`n` cards in the first stage's artifacts, as paths in the world folder; the gate offers
    them in that order."""
    folder = runner.data_dir / "worlds" / run.world_id / "runs" / run.id / run.stages[0].id
    (folder / "artifacts").mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(1, n + 1):
        (folder / "artifacts" / f"card-{i}.md").write_text(f'---\ntitle: "Card {i}"\n---\n\nbody\n')
        paths.append(f"runs/{run.id}/{run.stages[0].id}/artifacts/card-{i}.md")
    runner.offered = lambda r, index: paths if r.id == run.id and index == 1 else []
    return paths


def test_three_picks_in_order_one_rejected_with_its_reason_the_last_parked(
    runner: Runner,
) -> None:
    run = runner.start("world_1", "wf_1", 1, "Choose", [MAKE, CHOOSE])
    at(runner, run.id, "waiting_gate", "choose")
    (gate,) = runner.gates()
    assert gate.picks == 3
    c = cards(runner, run, 5)
    done = runner.decide(
        run.id, artifacts=[c[3], c[0], c[2]], rejects=[{"artifact": c[1], "reason": "too thin"}]
    )
    (decision,) = done.decisions
    assert decision.artifacts == [c[3], c[0], c[2]] and decision.artifact == c[3]
    assert decision.choice == "Card 4"
    assert decision.rejected == [{"artifact": c[1], "reason": "too thin"}]
    assert decision.parked == [c[4]]
    text = Path(decision.path).read_text()
    assert f'artifacts: ["{c[3]}", "{c[0]}", "{c[2]}"]' in text
    assert f'parked: ["{c[4]}"]' in text
    assert "Picked, in order:\n1. Card 4\n2. Card 1\n3. Card 3\n" in text
    assert "Parked:\n- Card 5\n" in text and "Rejected:\n- Card 2: too thin\n" in text
    at(runner, run.id, "done")
    assert fetch(runner, run.id).decisions == [decision], "the row reads back whole"


def test_too_many_picks_a_card_not_offered_and_a_card_picked_and_rejected_are_refused(
    runner: Runner,
) -> None:
    run = runner.start("world_1", "wf_1", 1, "Choose", [MAKE, CHOOSE])
    at(runner, run.id, "waiting_gate", "choose")
    c = cards(runner, run, 5)
    with pytest.raises(ValueError, match="at most 3 picks"):
        runner.decide(run.id, artifacts=c[:4])
    elsewhere = c[0].replace("card-1", "card-9")
    (runner.data_dir / "worlds/world_1" / elsewhere).write_text('---\ntitle: "Card 9"\n---\n')
    with pytest.raises(ValueError, match="not a card this gate offered"):
        runner.decide(run.id, artifacts=[c[0], elsewhere])
    with pytest.raises(ValueError, match="not an artifact of this run"):
        runner.decide(run.id, artifacts=["runs/other/stage/artifacts/x.md"])
    with pytest.raises(ValueError, match="both picked and rejected"):
        runner.decide(run.id, artifacts=[c[0]], rejects=[{"artifact": c[0]}])
    assert fetch(runner, run.id).decisions == []


def test_a_single_pick_may_reject_and_parks_the_rest(runner: Runner) -> None:
    one = StageDef("choose", "gate", {"ask": "Choose one.", "kind": "pick"})
    run = runner.start("world_1", "wf_1", 1, "Choose", [MAKE, one])
    at(runner, run.id, "waiting_gate", "choose")
    c = cards(runner, run, 3)
    with pytest.raises(ValueError, match="at most 1 pick$"):
        runner.decide(run.id, artifacts=c[:2])
    (decision,) = runner.decide(run.id, artifact=c[1], rejects=[{"artifact": c[2]}]).decisions
    assert (decision.choice, decision.artifacts, decision.parked) == ("Card 2", [c[1]], [c[0]])
    assert decision.rejected == [{"artifact": c[2], "reason": ""}]
