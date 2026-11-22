"""A swarm stage that names a World Login (security review of #577): its tool servers reach the
agent runtime as MCP config, which the CLI is started with on its command line, so the login's
values must be in neither; only the path of the mode-600 file the sandbox's launcher reads."""

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from api_fakes import install
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarmify_swarm import tool_id
from swarmify_swarm.fake import LAST, Answer, Call, Script

SIGNER = "packages/connectors/tests/fixtures/packages/signer"
PASSWORD, TOTP = "Pa55-word-swarm-1", "JBSWY3DPEHPK3PXP"
PLAN = [Answer({"tasks": [{"title": "Sign in."}]})]
WORK = [Call(tool_id("claim_task")), Call(tool_id("finish_task"), {"task_id": LAST, "note": "ok"})]
PASS = [Answer({"passed": True, "reasons": ["Signed in."]})]


def test_no_login_value_reaches_the_agent_runtimes_command_line(
    app: FastAPI, script: Script
) -> None:
    script.scripts = [PLAN, WORK, PASS]
    with TestClient(app) as client:
        check(client, script)


def check(client: TestClient, script: Script) -> None:
    w = client.post("/api/worlds", json={"name": "Shop"}).json()["id"]
    assert install(client, SIGNER).status_code == 201
    assert client.put(f"/api/worlds/{w}/packages/signer", json={"version": "1.0.0"}).is_success
    body = {"name": "shop", "domains": ["shop.example.com"], "username": "ana"}
    body |= {"password": PASSWORD, "totp_secret": TOTP}
    assert client.post(f"/api/worlds/{w}/logins", json=body).status_code == 201
    yaml = (
        "name: Look\nstages:\n  - swarm: look\n    goal: Sign in.\n    done_when: signed in\n"
        "    tools: [signer]\n    login: shop\n"
    )
    saved = client.post(f"/api/worlds/{w}/workflows", json={"yaml": yaml})
    assert saved.status_code == 201, saved.text
    run = client.post(f"/api/workflows/{saved.json()['workflow_id']}/runs", json={}).json()
    deadline = time.monotonic() + 30
    while client.get(f"/api/runs/{run['id']}").json()["status"] not in ("done", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.05)

    worker = next(o for o in script.sessions if "signer" in dict(o.mcp_servers or {}))  # type: ignore[arg-type]
    config: dict[str, Any] = dict(worker.mcp_servers)["signer"]  # type: ignore[arg-type]
    assert "SWARMIFY_LOGIN_FILE" in config["env"]
    started = replace(worker, cli_path="/usr/local/bin/claude")  # the command, as it would run
    argv = SubprocessCLITransport(prompt="go", options=started)._build_command()
    assert any("signer" in a for a in argv)  # its MCP config is on the command line
    said = json.dumps(argv)
    for value in (PASSWORD, TOTP):
        assert value not in said
    assert Path(config["env"]["SWARMIFY_LOGIN_FILE"]).name == "login.json"
