"""Follow-ups and Attempts over HTTP (issue 535): real DBOS on SQLite and the real Agent SDK
client with a scripted CLI. A Follow-up on a finished swarm stage is a Run Change: proposed first,
naming the later stages that run again and the gate that waits again, then applied. The stage's
worker continues its session with the person's words, as Attempt 2, and Attempt 1 keeps the
files it made."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_swarm_http import CLAIM, FINISH, GRAPH, PASS, PLAN, _until

from kriflow_swarm import WORKER
from kriflow_swarm.fake import Call, Say, Script
from kriflow_swarm.ports import tool_id
from kriflow_swarm.worker import FROM_PERSON

DRAFT = """name: Draft
stages:
  - swarm: draft
    goal: Write a short draft.
    done_when: one draft
  - gate: review
    ask: Read it. Approve or send back.
    kind: approve
"""


def write(text: str) -> Call:
    return Call(tool_id("write_artifact"), {"name": "draft.md", "content": text})


@pytest.fixture
def running(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_a_follow_up_continues_the_stage_session_as_a_new_attempt(
    running: TestClient, script: Script
) -> None:
    script.scripts = [
        PLAN,
        [GRAPH, CLAIM, write("Three points."), FINISH, Say("Drafted.")],
        PASS,
        [CLAIM, write("Two points."), FINISH, Say("Cut the third.")],
        PASS,
    ]
    world = running.post("/api/worlds", json={"name": "Notes"}).json()["id"]
    wf = running.post(f"/api/worlds/{world}/workflows", json={"yaml": DRAFT}).json()
    run = running.post(f"/api/workflows/{wf['workflow_id']}/runs", json={}).json()
    url = f"/api/runs/{run['id']}"

    def waiting() -> dict:
        _until(lambda: running.get(url).json()["status"] == "waiting_gate")
        return running.get(url).json()

    run = waiting()
    draft = run["stages"][0]
    first = next(a for a in running.get(f"{url}/agents").json() if a["name"] == WORKER)
    words = "Drop the third point."

    body = {"kind": "follow_up", "stage": draft["id"], "payload": {"note": words}}
    proposal = running.post(f"{url}/changes/propose", json=body).json()
    assert proposal["violations"] == [] and proposal["in_place"]
    assert proposal["before"] == {"draft": "done", "review": "waiting"}

    applied = running.post(f"{url}/changes", json={**body, "revision": proposal["revision"]})
    assert applied.status_code == 201, applied.text
    assert (applied.json()["kind"], applied.json()["payload"]) == ("follow_up", {"note": words})
    _until(lambda: len(script.sessions) == 5)
    run = waiting()

    # The worker continued its session, the person's words its next turn.
    assert script.sessions[3].resume == first["session_id"]
    assert script.prompts[3].startswith(f"{FROM_PERSON}{words}")

    attempts = running.get(f"{url}/stages/{draft['id']}/attempts").json()
    assert [a["number"] for a in attempts] == [1, 2]
    assert attempts[0]["cause"] is None
    cause = attempts[1]["cause"]
    assert (cause["kind"], cause["note"]) == ("follow_up", words)
    assert all(a["ended_at"] for a in attempts) and attempts[1]["spent_usd"] > 0

    files = f"{url}/stages/{draft['id']}/artifacts"
    kept = {a["name"] for a in running.get(files, params={"attempt": 1}).json()}
    assert "draft.md" in kept
    now = next(a for a in running.get(files).json() if a["name"] == "draft.md")
    old = next(
        a for a in running.get(files, params={"attempt": 1}).json() if a["name"] == "draft.md"
    )
    assert now["path"] != old["path"]
