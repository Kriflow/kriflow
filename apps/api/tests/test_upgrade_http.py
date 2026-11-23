"""Upgrading a V1 data directory (issues 213, 214 and 215, ADR 0023): V1's built-in connectors
became default Connector Packages, installed and given to every world at startup. A Setting moves
from the app's env and a Credential from the credentials file into the package's shared Setup,
and a workflow saved in V1 with a stage calling a built-in runs unchanged. An app and the refresh
token V1's terminal `authorize` printed move from `.env` into the package's sign-in, so it needs
no new sign-in. Fixture packages play the built-ins: `echo` a stage connector with a Setting and
a Credential, `fake_publish` one signed in with OAuth."""

import json
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from kriflow_api import main
from kriflow_connectors import FakeTransport, oauth
from kriflow_connectors.fake import FIXTURE_DEFAULTS, FIXTURES, default_list
from kriflow_connectors.fake_oauth import FakeProvider
from kriflow_kernel import Event, new_id
from kriflow_workflows import Workflow
from kriflow_workflows.sqlite import SqliteStorage as WorkflowStorage

V1_WORKFLOW = """name: Bedtime
stages:
  - connector: write
    calls: script_writer
    output: line.md
  - connector: say
    calls: echo
    input: line.md
    output: said.wav
"""


@pytest.fixture
def with_echo(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """echo is a default too, standing in for a V1 built-in with a Setting and a Credential."""
    names = (*FIXTURE_DEFAULTS, "echo")
    listed = default_list(tmp_path_factory.mktemp("list"), {n: FIXTURES / n for n in names})
    monkeypatch.setenv("KRIFLOW_DEFAULT_PACKAGES", str(listed))


def writes(args: dict[str, Any]) -> dict[str, Any]:
    """A connector writing its stage's output, as script_writer and echo would."""
    Path(args["output_path"]).write_text("said")
    return {"output": args["output_path"]}


def v1_data_dir(root: Path) -> tuple[str, str]:
    """What V1 left: a world with its own script writer and a saved workflow whose echo stage
    called the built-in, echo's key in the credentials file, no packages folder, no Setup, and no
    package versions recorded."""
    world = main.world_service(root).create("Bedtime")  # no defaults given: V1 had none
    command = [sys.executable, str(Path(__file__).parent / "script_server.py")]
    with sqlite3.connect(main.migrated_db(root)) as conn:  # V1's row for a world's connector
        conn.execute(
            "INSERT INTO connectors (id, world_id, name, kind, command, url, credentials,"
            " stage_types, created_at, types, input_types, world_types)"
            " VALUES (?, ?, 'script_writer', 'stage', ?, NULL, '[]', '[\"connector\"]', ?,"
            " '{}', '[]', NULL)",
            (new_id("conn"), world.id, json.dumps(command), "2026-10-28T09:00:00+00:00"),
        )
    flow = Workflow(new_id("wf"), world.id, "Bedtime", 1, "2026-10-28T09:00:00+00:00")
    saved = Event.new("workflow.saved", world_id=world.id, payload={"workflow_id": flow.id})
    WorkflowStorage(main.migrated_db(root)).add_workflow(flow, V1_WORKFLOW, saved)
    (root / ".env").write_text("ECHO_KEY=ec-k3y\n")
    (root / ".env").chmod(0o600)
    assert not (root / "packages").exists() and not (root / "setup.json").exists()
    return world.id, flow.id


def test_a_v1_workflow_calling_a_built_in_runs_unchanged_after_upgrade(
    tmp_path: Path, transport: FakeTransport, with_echo: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, flow = v1_data_dir(tmp_path)
    monkeypatch.setenv("ENDPOINT", "http://127.0.0.1:8099")  # as V1's app env set it
    transport.outputs["script_writer"] = [writes]
    transport.outputs["echo"] = [writes]
    transport.outputs["check"] = [{"problems": []}]

    app = main.create_app(tmp_path, transport=transport, poll_seconds=0.05, retro=False)
    with TestClient(app) as client:  # the upgrade
        assert client.get(f"/api/worlds/{world}/packages").json() == [
            {"name": "echo", "version": "1.0.0"},
            {"name": "fake_publish", "version": "1.0.0"},
            {"name": "kit", "version": "1.0.0"},
            {"name": "script_writer", "version": "0.0.1"},  # the world's own, moved (#211)
            {"name": "sketch", "version": "1.0.0"},
        ]
        version = client.get(f"/api/workflows/{flow}/versions/1").json()
        assert version["packages"] == {"echo": "1.0.0", "script_writer": "0.0.1"}  # stamped
        fields = {
            f["key"]: f
            for f in client.get(f"/api/worlds/{world}/packages/echo/setup").json()["fields"]
        }
        key, endpoint = fields["api_key"], fields["endpoint"]
        assert (key["env"], key["shared_set"], key["shared"]) == ("ECHO_KEY", True, None)
        assert (endpoint["env"], endpoint["shared"]) == ("ENDPOINT", "http://127.0.0.1:8099")
        echo = next(
            c for c in client.get(f"/api/worlds/{world}/connectors").json() if c["name"] == "echo"
        )
        assert echo["missing"] == [] and "ec-k3y" not in json.dumps(echo)

        run = client.post(f"/api/workflows/{flow}/runs", json={"version": 1}).json()
        deadline = time.monotonic() + 60
        while (got := client.get(f"/api/runs/{run['id']}").json())["status"] != "done":
            assert got["status"] not in ("failed", "cancelled"), got
            assert time.monotonic() < deadline, got
            time.sleep(0.1)
    said = [c for c in transport.calls if c[:2] == ("echo", "echo")]
    assert said and said[0][3]["ECHO_KEY"] == "ec-k3y"

    # Restarted, nothing is given twice: a world that disabled echo keeps it disabled.
    off = TestClient(main.create_app(tmp_path, transport=transport, retro=False))
    assert off.delete(f"/api/worlds/{world}/packages/echo").status_code == 204
    again = TestClient(main.create_app(tmp_path, transport=transport, retro=False))
    assert [p["name"] for p in again.get(f"/api/worlds/{world}/packages").json()] == [
        "fake_publish",
        "kit",
        "script_writer",
        "sketch",
    ]
    new = again.post("/api/worlds", json={"name": "New"}).json()["id"]
    assert [p["name"] for p in again.get(f"/api/worlds/{new}/packages").json()] == [
        "echo",
        "fake_publish",
        "kit",
        "sketch",
    ]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeProvider]:
    """The platform's token endpoint, played by the fake provider, holding the refresh token V1
    kept."""
    post = httpx.post

    def to_fake(url: str, **kwargs: Any) -> httpx.Response:
        assert url == "https://auth.example.org/token"
        return post(f"{fake.url}/token", **kwargs)

    with FakeProvider(client_id="app", client_secret="app-secret") as fake:
        fake.refresh_tokens.add("rt-from-v1")
        monkeypatch.setattr(oauth.httpx, "post", to_fake)
        yield fake


def test_a_v1_sign_in_moves_into_the_package_with_no_new_sign_in(
    tmp_path: Path, transport: FakeTransport, provider: FakeProvider
) -> None:
    world, _ = v1_data_dir(tmp_path)
    env = tmp_path / ".env"  # V1's keys, the refresh token as `authorize` printed it
    env.write_text(
        "ACCOUNT_CLIENT_ID=app\nACCOUNT_CLIENT_SECRET=app-secret\nACCOUNT_REFRESH_TOKEN=rt-from-v1\n"
    )
    env.chmod(0o600)

    with TestClient(main.create_app(tmp_path, transport=transport, retro=False)) as client:
        form = client.get(f"/api/worlds/{world}/packages/fake_publish/setup")
        fields = {f["key"]: f for f in form.json()["fields"]}
        assert (
            [k for k, f in fields.items() if f["shared_set"]]
            == list(fields)
            == ["account_client_id", "account_client_secret", "account"]
        )
        assert not fields["account"]["needs_sign_in"] and "rt-from-v1" not in form.text
        (row,) = [c for c in client.get(f"/api/worlds/{world}/connectors").json()
                  if c["name"] == "fake_publish"]  # fmt: skip
        assert (row["missing"], row["sign_in"]) == ([], [])

        # Its first call trades V1's refresh token for an access token; its own preview checks
        # a publish stage as V1's built-in did.
        yaml = (
            "name: Weekly\nstages:\n  - gate: ok\n    ask: Post it?\n    kind: confirm\n"
            "  - connector: publish\n    calls: fake_publish\n    input: short.mp4\n"
            "    params: {visibility: secret}\n"
        )
        checked = client.post(f"/api/worlds/{world}/workflows/check", json={"yaml": yaml})
        said = [v["message"] for v in checked.json()["violations"] if v["stage"] == "publish"]
        assert {
            "a publish stage needs params.title",
            "visibility is one of public, unlisted, private, got 'secret'",
        } <= set(said)
    assert provider.grants == ["refresh_token"]
    saved = json.loads((tmp_path / "setup.json").read_text())["fake_publish"]["shared"]["account"]
    assert saved["access_token"] in provider.access_tokens  # kept, with the refresh token it gave
    assert saved["refresh_token"] in provider.refresh_tokens
    previewed = next(c for c in transport.calls if c[:2] == ("fake_publish", "preview"))
    assert previewed[3] == {"FAKE_PUBLISH_TOKEN": saved["access_token"]}
