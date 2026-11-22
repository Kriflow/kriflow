"""Typed contracts (issue 133) through the API: a connector declares the type of its input, the
workflow check and the run start refuse a swarm stage that produces it without promising that
`output_type`, and a promised one is projected by the judge in the type's schema. A fixture
connector declaring a recipe shows the core knows nothing of video. A real package voicing a
projected script is its package's to test, in Kriflow/packages."""

import io
import json
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from api_fakes import until
from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarmify_connectors import ConnectorError, FakeTransport
from swarmify_swarm import tool_id
from swarmify_swarm.fake import LAST, Answer, Call, Say, Script

RECIPE = {
    "type": "object",
    "properties": {"steps": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
    "required": ["steps"],
}
COOK = {
    "name": "cook",
    "kind": "stage",
    "server": "uvx cook-mcp",
    "stage_types": ["connector"],
    "types": {"recipe": RECIPE},
    "input_types": ["recipe"],
}


def recipe_flow(output_type: str = "output_type: recipe") -> str:
    return f"""name: Soup
stages:
  - swarm: write
    goal: Write a soup recipe.
    done_when: A recipe.
    {output_type}
    produces: [recipe.md]
  - connector: cook it
    calls: cook
    input: recipe.md
"""


PLAN = [Answer({"tasks": [{"title": "Write it."}]})]
WORK = [
    Call(tool_id("query_graph"), {"question": "What did we cook?"}),
    Call(tool_id("claim_task")),
    Call(tool_id("write_artifact"), {"name": "recipe.md", "content": "Boil it, I guess."}),
    Call(tool_id("finish_task"), {"task_id": LAST, "note": "Done."}),
    Say("Done."),
]


def judged(fields: dict[str, Any], body: str = "Boil, then season.") -> list:
    item = {"title": "Soup", "body": body, "rank": 1, "sources": [], "fields": fields}
    return [Answer({"passed": True, "reasons": ["It is one."], "items": [item]})]


@pytest.fixture
def running(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def world_with_cook(client: TestClient, cook: dict[str, Any] = COOK) -> str:
    world = client.post("/api/worlds", json={"name": "Kitchen"}).json()["id"]
    added = client.post(f"/api/worlds/{world}/connectors", json=cook)
    assert added.status_code == 201, added.json()
    return world


def test_the_check_refuses_a_typed_input_its_swarm_stage_does_not_promise(
    running: TestClient,
) -> None:
    world = world_with_cook(running)
    (cook,) = [
        c for c in running.get(f"/api/worlds/{world}/connectors").json() if c["name"] == "cook"
    ]
    assert cook["types"] == {"recipe": RECIPE} and cook["input_types"] == ["recipe"]

    check = f"/api/worlds/{world}/workflows/check"
    found = running.post(check, json={"yaml": recipe_flow("")}).json()["violations"]
    assert [(v["rule"], v["stage"]) for v in found] == [("output_type", "cook it")]
    assert "from swarm stage 'write' as type recipe" in found[0]["message"]
    wrong = running.post(check, json={"yaml": recipe_flow("output_type: script")}).json()
    assert "declares output_type script" in wrong["violations"][0]["message"]
    assert running.post(check, json={"yaml": recipe_flow()}).json()["violations"] == []

    bad = {**COOK, "name": "bad_cook", "types": {"recipe": {"type": "soup"}}}
    refused = running.post(f"/api/worlds/{world}/connectors", json=bad)
    assert refused.status_code == 422 and "not a JSON Schema" in refused.json()["detail"]


def test_a_run_start_rechecks_the_contract_at_the_versions_it_recorded(
    running: TestClient,
) -> None:
    untyped = {k: v for k, v in COOK.items() if k not in ("types", "input_types")}
    world = world_with_cook(running, untyped)
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": recipe_flow("")})
    assert saved.status_code == 201, saved.json()
    workflow = saved.json()["workflow_id"]
    assert running.delete(f"/api/worlds/{world}/packages/cook").status_code == 204
    assert running.post(f"/api/worlds/{world}/connectors", json=COOK).status_code == 201

    # The run uses the untyped cook it was saved with (#223), so its check passes; a new
    # version, on the typed cook the world pins now, does not.
    again = running.post(f"/api/workflows/{workflow}/versions", json={"yaml": recipe_flow("")})
    assert [v["rule"] for v in again.json()["violations"]] == ["output_type"]
    started = running.post(f"/api/workflows/{workflow}/runs", json={})
    assert started.status_code == 201, started.json()
    running.post(f"/api/runs/{started.json()['id']}/abort").raise_for_status()

    assert running.delete(f"/api/worlds/{world}/packages/cook").status_code == 204
    gone = running.post(f"/api/workflows/{workflow}/runs", json={})
    assert gone.status_code == 409, gone.json()
    assert [v["rule"] for v in gone.json()["violations"]] == ["unknown_connector"]


def test_a_promised_type_is_projected_in_its_schema_and_reaches_the_connector(
    running: TestClient, script: Script, transport: FakeTransport, tmp_path: Path
) -> None:
    script.scripts = [PLAN, WORK, judged({"steps": ["Boil.", "Season."]})]
    world = world_with_cook(running)
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": recipe_flow()}).json()
    run = running.post(f"/api/workflows/{saved['workflow_id']}/runs", json={}).json()
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] in ("done", "failed"))
    ended = running.get(f"/api/runs/{run['id']}").json()
    assert ended["status"] == "done", ended

    judge = script.sessions[2]
    item = (judge.output_format or {})["schema"]["properties"]["items"]["items"]
    assert item["properties"]["fields"] == RECIPE
    write = ended["stages"][0]["id"]
    recipe = tmp_path / "worlds" / world / "runs" / run["id"] / write / "artifacts" / "recipe.md"
    text = recipe.read_text()
    assert 'type: "recipe"' in text or "type: recipe" in text
    fenced = json.loads(text.split("```json\n")[1].split("\n```")[0])
    assert fenced == {"steps": ["Boil.", "Season."]} and "Boil it, I guess." not in text
    ((name, _, args, _),) = [c for c in transport.calls if c[0] == "cook"]
    assert args["input_path"] == str(recipe)


# World-dependent types (issue 140): the fixture cook answers its recipe for a world, with the
# pots that world has as an enum. The core passes the schema on and never reads it.
POTS = {
    **RECIPE,
    "properties": {**RECIPE["properties"], "pot": {"type": "string", "enum": ["wok", "stockpot"]}},
}
WORLD_COOK = {**COOK, "world_types": "recipe_types"}


def test_the_judge_projects_the_type_as_the_connector_answers_it_for_the_world(
    running: TestClient, script: Script, transport: FakeTransport
) -> None:
    transport.outputs["recipe_types"] = [{"types": {"recipe": POTS}}]
    script.scripts = [PLAN, WORK, judged({"steps": ["Boil."], "pot": "wok"})]
    world = world_with_cook(running, WORLD_COOK)
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": recipe_flow()}).json()
    run = running.post(f"/api/workflows/{saved['workflow_id']}/runs", json={}).json()
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] in ("done", "failed"))
    assert running.get(f"/api/runs/{run['id']}").json()["status"] == "done"
    item = (script.sessions[2].output_format or {})["schema"]["properties"]["items"]["items"]
    assert item["properties"]["fields"] == POTS
    asked = [c for c in transport.calls if c[1] == "recipe_types"]
    assert asked and all(c[2]["world"] == world for c in asked)


def test_a_connector_that_cannot_answer_for_the_world_falls_back_to_its_declared_type(
    running: TestClient, script: Script, transport: FakeTransport
) -> None:
    transport.outputs["recipe_types"] = [ConnectorError("cook: could not start")]
    script.scripts = [PLAN, WORK, judged({"steps": ["Boil."]})]
    world = world_with_cook(running, WORLD_COOK)
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": recipe_flow()}).json()
    run = running.post(f"/api/workflows/{saved['workflow_id']}/runs", json={}).json()
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] in ("done", "failed"))
    assert running.get(f"/api/runs/{run['id']}").json()["status"] == "done"
    item = (script.sessions[2].output_format or {})["schema"]["properties"]["items"]["items"]
    assert item["properties"]["fields"] == RECIPE


def test_the_check_and_the_run_start_compare_the_types_as_answered_for_the_world(
    running: TestClient, transport: FakeTransport
) -> None:
    transport.outputs["recipe_types"] = [{"types": {"recipe": RECIPE}}]
    world = world_with_cook(running, WORLD_COOK)
    bake = {**COOK, "name": "bake"}  # declares the same recipe, never world-dependent
    assert running.post(f"/api/worlds/{world}/connectors", json=bake).status_code == 201
    check = f"/api/worlds/{world}/workflows/check"
    untyped = "name: Plain\nstages:\n  - swarm: s\n    goal: g\n    done_when: d\n"
    assert running.post(check, json={"yaml": untyped}).json()["violations"] == []
    assert not [c for c in transport.calls if c[1] == "recipe_types"]  # nothing promised
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": recipe_flow()})
    assert saved.status_code == 201, saved.json()

    transport.outputs["recipe_types"] = [{"types": {"recipe": POTS}}]  # the world changed
    found = running.post(check, json={"yaml": recipe_flow()}).json()["violations"]
    assert [v["rule"] for v in found] == ["type_conflict"]
    started = running.post(f"/api/workflows/{saved.json()['workflow_id']}/runs", json={})
    assert started.status_code == 409
    assert [v["rule"] for v in started.json()["violations"]] == ["type_conflict"]


def _wav(seconds: float) -> bytes:
    """A silent mono WAV of `seconds`, as a speech engine answers."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(seconds * 16000))
    return buf.getvalue()
