"""Fan-out Stages (ADR 0031) through the API, with a non-video example: translate each
paragraph. Over HTTP a JSON list drives the children, each child's first stage finds its given
item.json as its input, and the join lands per Item; the sources read from real run files."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from api_fakes import until
from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarmify_api import fanout
from swarmify_api.main import migrated_db
from swarmify_connectors import FakeTransport
from swarmify_memory import Memory
from swarmify_runs import Decision, Run, RunStageView, StageDef

PARA = {"name": "para", "kind": "stage", "server": "uvx para-mcp", "stage_types": ["connector"]}
ELEMENTS = [
    {"id": "intro", "title": "Intro", "text": "Hello"},
    {"text": "Two"},
    {"title": "Three", "text": "3"},
]
CHILD = """name: Translate
given: [item.json]
stages:
  - connector: translate
    calls: para
    input: item.json
    output: out.md
"""
PARENT = """name: Book
stages:
  - connector: split
    calls: para
    output: paragraphs.json
  - workflow: translate each
    runs: {child}
    for_each: paragraphs.json
    max_parallel: 2
    take: [out.md]
"""


@pytest.fixture
def read(transport: FakeTransport) -> list[Any]:
    """`split` writes the paragraphs; `translate` reads its input and writes out.md."""
    seen: list[Any] = []

    def para(args: dict[str, Any]) -> dict[str, Any]:
        out = Path(args["output_path"])
        if out.name == "paragraphs.json":
            out.write_text(json.dumps(ELEMENTS))
        else:
            seen.append(json.loads(Path(args["input_path"]).read_text()))
            out.write_text(f"Translated {seen[-1]['text']}")
        return {"ok": True}

    transport.outputs["para"] = [para]
    return seen


@pytest.fixture
def running(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_a_json_list_fans_out_each_child_reads_its_given_input_and_the_join_lands_per_item(
    running: TestClient, read: list[Any], tmp_path: Path
) -> None:
    world = running.post("/api/worlds", json={"name": "Books"}).json()["id"]
    assert running.post(f"/api/worlds/{world}/connectors", json=PARA).status_code == 201
    child = running.post(f"/api/worlds/{world}/workflows", json={"yaml": CHILD})
    assert child.status_code == 201, child.json()
    parent_yaml = PARENT.format(child=child.json()["workflow_id"])
    parent = running.post(f"/api/worlds/{world}/workflows", json={"yaml": parent_yaml})
    assert parent.status_code == 201, parent.json()
    run = running.post(f"/api/workflows/{parent.json()['workflow_id']}/runs", json={}).json()
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] in ("done", "failed"))

    done = running.get(f"/api/runs/{run['id']}").json()
    items = done["stages"][1]["items"]
    assert [(i["key"], i["title"], i["status"]) for i in items] == [
        ("intro", "Intro", "done"), ("1", "1", "done"), ("2", "Three", "done"),
    ]  # fmt: skip
    assert sorted(read, key=lambda e: e["text"]) == sorted(ELEMENTS, key=lambda e: e["text"])
    names = {running.get(f"/api/runs/{i['child']['id']}").json()["name"] for i in items}
    assert names == {"Translate: Intro", "Translate: 1", "Translate: Three"}
    folder = tmp_path / "worlds" / world / "runs" / run["id"] / done["stages"][1]["id"]
    assert (folder / "artifacts/intro/out.md").read_text().endswith("Translated Hello")
    index = json.loads((folder / "artifacts/items.json").read_text())
    assert [(i["key"], i["files"]) for i in index] == [
        ("intro", ["intro/out.md"]), ("1", ["1/out.md"]), ("2", ["2/out.md"]),
    ]  # fmt: skip
    retry = running.post(f"/api/runs/{run['id']}/items/intro/retry")
    assert retry.status_code == 409  # the run is done: nothing fans out
    assert running.post("/api/runs/run_nope/items/intro/retry").status_code == 404


def test_a_child_workflow_must_declare_its_given_input(client: TestClient) -> None:
    world = client.post("/api/worlds", json={"name": "Books"}).json()["id"]
    assert client.post(f"/api/worlds/{world}/connectors", json=PARA).status_code == 201
    bare = CHILD.replace("given: [item.json]\n", "")
    refused = client.post(f"/api/worlds/{world}/workflows", json={"yaml": bare})
    assert [v["rule"] for v in refused.json()["violations"]] == ["input_not_produced"]


def stage(index: int, name: str, kind: str) -> RunStageView:
    return RunStageView(f"stage_{index}", index, name, kind, "done")


def card(memory: Memory, stage_id: str, slug: str, title: str, rank: int, **more: Any) -> str:
    return memory.write_artifact(
        "w", "r", stage_id, "judge", f"idea-{slug}.md", f"About {title}.", "idea",
        {"title": title, "rank": rank, **more},
    )  # fmt: skip


def test_items_come_from_ranked_cards_picked_cards_or_a_json_list(tmp_path: Path) -> None:
    memory = Memory(migrated_db(tmp_path), tmp_path)
    card(memory, "stage_0", "cats", "Cats", 2)
    first = card(memory, "stage_0", "dogs", "Dogs", 1)
    card(memory, "stage_0", "gone", "Gone", 3, deleted="2026-11-01")
    memory.write_artifact("w", "r", "stage_0", "judge", "notes.md", "not a card")
    now = datetime.now(UTC).isoformat()
    picked = Decision("dec_1", 1, "pick", "pick", "Dogs", "", None, "", now, first)
    stages = [stage(0, "ideas", "swarm"), stage(1, "pick", "gate"), stage(2, "each", "workflow")]
    run = Run("r", "w", "wf", 1, "R", "running", now, now, stages, decisions=[picked])

    def of(source: str) -> list[tuple[str, str, str | None]]:
        found = fanout.items(memory, run, 2, StageDef("each", "workflow", {"for_each": source}))
        return [(i.key, i.title, i.file and Path(i.file).name) for i in found]

    assert of("ideas") == [("dogs", "Dogs", "idea-dogs.md"), ("cats", "Cats", "idea-cats.md")]
    assert of("pick") == [("dogs", "Dogs", "idea-dogs.md")]

    given = tmp_path / "worlds/w/runs/r/given"
    given.mkdir(parents=True)
    (given / "list.json").write_text(json.dumps([{"id": "a/b", "title": "AB"}, "plain"]))
    found = fanout.items(memory, run, 2, StageDef("each", "workflow", {"for_each": "list.json"}))
    assert [(i.key, i.title, i.data) for i in found] == [
        ("a-b", "AB", {"id": "a/b", "title": "AB"}), ("1", "1", "plain"),
    ]  # fmt: skip
    (given / "list.json").write_text('{"not": "a list"}')
    with pytest.raises(ValueError, match="JSON list"):
        of("list.json")
    with pytest.raises(ValueError, match="no earlier stage"):
        of("missing.json")


CARDS = """name: Choose
stages:
  - connector: make
    calls: para
    output: cards.md
  - gate: choose
    kind: pick
    picks: 3
    ask: Choose three.
  - workflow: each
    runs: {child}
    for_each: choose
    max_parallel: 1
    take: [out.md]
"""
TWO_STEPS = """name: Develop
given: [item.md]
stages:
  - connector: draft
    calls: para
    input: item.md
    output: mid.md
  - connector: polish
    calls: para
    input: mid.md
    output: out.md
"""


def test_a_fan_out_over_a_multi_pick_runs_the_picks_in_order_and_a_promote_unparks(
    running: TestClient, transport: FakeTransport
) -> None:
    """Five cards, three picked out of rank order, one rejected with a reason, one parked: the
    children run the picks in pick order; the Library lists the parked one until it is
    promoted."""

    def para(args: dict[str, Any]) -> dict[str, Any]:
        out = Path(args["output_path"])
        if out.name == "cards.md":
            for i in range(1, 6):
                (out.parent / f"card-{i}.md").write_text(
                    f'---\ntype: "card"\ntitle: "Card {i}"\nrank: {i}\n---\n\nAbout card {i}.\n'
                )
        out.write_text(f"from {Path(args.get('input_path') or 'nothing').name}")
        return {"ok": True}

    transport.outputs["para"] = [para]
    world = running.post("/api/worlds", json={"name": "Cards"}).json()["id"]
    assert running.post(f"/api/worlds/{world}/connectors", json=PARA).status_code == 201
    child = running.post(f"/api/worlds/{world}/workflows", json={"yaml": TWO_STEPS}).json()
    made = running.post(
        f"/api/worlds/{world}/workflows", json={"yaml": CARDS.format(child=child["workflow_id"])}
    )
    assert made.status_code == 201, made.json()
    run = running.post(f"/api/workflows/{made.json()['workflow_id']}/runs", json={}).json()
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] == "waiting_gate")
    (gate,) = [g for g in running.get("/api/gates").json() if g["run_id"] == run["id"]]
    assert gate["picks"] == 3

    shown = running.get(f"/api/runs/{run['id']}/artifacts", params={"before": 1}).json()
    path = {a["title"]: a["path"] for a in shown if a["type"] == "card"}
    too_many = {"artifacts": [path[f"Card {i}"] for i in (1, 2, 3, 4)]}
    assert running.post(f"/api/runs/{run['id']}/decision", json=too_many).status_code == 422
    answer = {
        "artifacts": [path["Card 4"], path["Card 1"], path["Card 3"]],
        "rejects": [{"artifact": path["Card 2"], "reason": "too thin"}],
    }
    decided = running.post(f"/api/runs/{run['id']}/decision", json=answer)
    assert decided.status_code == 200, decided.json()
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] in ("done", "failed"))
    done = running.get(f"/api/runs/{run['id']}").json()
    assert done["status"] == "done"
    (decision,) = done["decisions"]
    assert decision["parked"] == [path["Card 5"]] and decision["choice"] == "Card 4"
    items = done["stages"][2]["items"]
    assert [i["title"] for i in items] == ["Card 4", "Card 1", "Card 3"]

    def listed(status: str) -> list[tuple[str, str]]:
        found = running.get(f"/api/worlds/{world}/library", params={"status": status}).json()
        return [(a["title"], a["reason"]) for a in found]

    assert listed("parked") == [("Card 5", "")]
    assert listed("rejected") == [("Card 2", "too thin")]
    assert sorted(listed("picked")) == [("Card 1", ""), ("Card 3", ""), ("Card 4", "")]
    promoted = running.post(
        f"/api/workflows/{child['workflow_id']}/promote",
        json={"stage": 1, "artifact": path["Card 5"]},
    )
    assert promoted.status_code == 201, promoted.json()
    assert listed("parked") == []
