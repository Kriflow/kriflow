"""A multi-pick gate is never answered half in chat: a Remote Approval gets the link, no buttons."""

from kriflow_api.remote import answers, said
from kriflow_memory import ArtifactFile
from kriflow_runs import Gate

CARDS = [
    ArtifactFile(f"card-{i}.md", f"runs/r/s/artifacts/card-{i}.md", "card", "judge", f"Card {i}",
                 {"rank": i}, "")
    for i in range(1, 4)
]  # fmt: skip


def gate(picks: int) -> Gate:
    return Gate("r", "w", "Run", "s1", 1, "choose", "pick", "Choose.", "", 0, [], picks=picks)


def test_a_multi_pick_gate_offers_no_buttons_and_a_single_pick_keeps_them() -> None:
    assert answers(gate(3), CARDS) == []
    assert [a["label"] for a in answers(gate(1), CARDS)] == ["Card 1", "Card 2", "Card 3"]
    several = {"outcome": "picked", "choice": "Card 2", "picked": ["Card 2", "Card 1"]}
    assert said(several) == "Picked Card 2 and Card 1 in Kriflow."
    assert said({"outcome": "picked", "choice": "Card 2"}) == "Picked Card 2 in Kriflow."
