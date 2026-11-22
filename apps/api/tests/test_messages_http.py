"""Messages to a running stage over HTTP (issue 534): real DBOS on SQLite and the real Agent SDK
client with a scripted CLI. A Message is a Run Change a person applies as they send it; the
worker's live session reads it as its next turn and the stage log keeps its receipt."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_swarm_http import CLAIM, FINISH, GRAPH, IDEAS, PASS, PLAN, _ended, _start, _until

from swarmify_swarm import WORKER
from swarmify_swarm.fake import Hold, Say, Script
from swarmify_swarm.worker import FROM_PERSON


@pytest.fixture
def running(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_a_message_to_the_running_stage_is_a_run_change_its_worker_reads(
    running: TestClient, script: Script, tmp_path: Path
) -> None:
    script.scripts = [
        PLAN,
        [Say("Waiting for word."), Hold(), GRAPH, CLAIM, IDEAS, FINISH, Say("Five ideas.")],
        PASS,
    ]
    script.replies = [Say("Nothing older than 30 days, then.")]
    _, run = _start(running, tmp_path)
    stage = run["stages"][0]["id"]
    log = f"/api/runs/{run['id']}/stages/{stage}/log"
    _until(lambda: any(e["detail"] == "Waiting for word." for e in running.get(log).json()))

    url = f"/api/runs/{run['id']}/messages"
    assert running.post(url, json={"stage": stage, "text": "  "}).status_code == 422
    assert running.post(url, json={"stage": stage, "text": "Hi", "to": "judge"}).status_code == 409
    sent = running.post(url, json={"stage": stage, "text": "Skip anything older than 30 days."})
    assert sent.status_code == 201, sent.text
    change = sent.json()
    assert (change["kind"], change["stage_name"], change["by"]) == (
        "message",
        "research",
        "operator",
    )
    assert change["payload"] == {
        "text": "Skip anything older than 30 days.",
        "to": None,
        "interrupt": False,
    }

    done = _ended(running, run["id"])
    assert done["status"] == "done"
    assert [c["id"] for c in done["changes"]] == [change["id"]]
    assert f"{FROM_PERSON}Skip anything older than 30 days." in script.prompts
    receipts = [
        (e["agent"], e["input"]["state"], e["ref"])
        for e in running.get(log).json()
        if e["kind"] == "message"
    ]
    assert receipts == [
        (WORKER, "queued", change["id"]),
        (WORKER, "delivered", change["id"]),
    ]
    late = running.post(url, json={"stage": stage, "text": "Too late."})
    assert late.status_code == 409  # no agent works there now
