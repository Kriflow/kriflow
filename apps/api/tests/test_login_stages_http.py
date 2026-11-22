"""A stage names a World Login (spec seam 3): saving a workflow that names a missing login, or a
login for a connector that takes none, is refused; `params.login` never reaches the package's
params check; only the naming stage's process gets the `SWARMIFY_LOGIN_*` values and the session
file, a sibling stage's does not; its sandbox reaches the login's domains and not another origin;
the session it saves round-trips into the Secrets Store and both scratch files are gone; and
`login.used` is recorded without values. Real DBOS, the real MCP transport and sandbox, the signer
fixture package and the fake site on loopback."""

import json
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from api_fakes import install
from fastapi.testclient import TestClient

from swarmify_api.main import create_app
from swarmify_connectors.fake_site import FakeSite
from swarmify_connectors.logins import FileSecrets, ref
from swarmify_kernel.sqlite import connect

SIGNER = Path(__file__).parents[3] / "packages/connectors/tests/fixtures/packages/signer"
USER, PASSWORD = "ana", "Pa55-word-77031"


@pytest.fixture
def site() -> Iterator[FakeSite]:
    with FakeSite(USER, PASSWORD) as s:
        yield s


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path / "data", poll_seconds=0.05, retro=False)) as c:
        yield c


def signer_world(client: TestClient, tmp_path: Path, site: FakeSite) -> str:
    source = tmp_path / "signer"
    shutil.copytree(SIGNER, source)
    toml = source / "swarmify-connector.toml"
    toml.write_text(
        toml.read_text().replace(
            '["python3", "server.py"]', json.dumps([sys.executable, "server.py"])
        )
    )
    assert install(client, str(source)).status_code == 201
    w = client.post("/api/worlds", json={"name": "Shop"}).json()["id"]
    assert client.put(f"/api/worlds/{w}/packages/signer", json={"version": "1.0.0"}).is_success
    body = {"name": "shop_admin", "domains": [site.host_a], "username": USER, "password": PASSWORD}
    assert client.post(f"/api/worlds/{w}/logins", json=body).status_code == 201
    return w


def run_to_end(client: TestClient, w: str, yaml: str) -> dict[str, Any]:
    saved = client.post(f"/api/worlds/{w}/workflows", json={"yaml": yaml})
    assert saved.status_code == 201, saved.text
    run = client.post(f"/api/workflows/{saved.json()['workflow_id']}/runs", json={}).json()
    deadline = time.monotonic() + 90
    while (run := client.get(f"/api/runs/{run['id']}").json())["status"] not in ("done", "failed"):
        assert time.monotonic() < deadline, run
        time.sleep(0.1)
    return run


def events(data: Path, kind: str) -> list[dict[str, Any]]:
    with closing(connect(data / "swarmify.db")) as conn:
        rows = conn.execute("SELECT payload FROM events WHERE type = ?", (kind,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def test_saving_refuses_a_missing_login_or_one_no_connector_takes(
    client: TestClient, tmp_path: Path, site: FakeSite
) -> None:
    w = signer_world(client, tmp_path, site)
    cases = {
        "params: {login: nobody}": "this world has no login named 'nobody'",
        "params: {login: Bad-Name}": "is not a login name",
    }
    for params, says in cases.items():
        yaml = f"name: S\nstages:\n  - connector: go\n    calls: signer\n    {params}\n"
        refused = client.post(f"/api/worlds/{w}/workflows", json={"yaml": yaml})
        assert refused.status_code == 422 and says in refused.text, refused.text
    swarm = (
        "name: S\nstages:\n  - swarm: look\n    goal: g\n    done_when: d\n"
        "    tools: [web_search]\n    login: shop_admin\n"
    )
    refused = client.post(f"/api/worlds/{w}/workflows", json={"yaml": swarm})
    assert refused.status_code == 422 and "none of its tools accepts a login" in refused.text
    ok = swarm.replace("[web_search]", "[signer]")
    assert client.post(f"/api/worlds/{w}/workflows", json={"yaml": ok}).status_code == 201


def test_only_the_naming_stage_signs_in_and_its_session_round_trips(
    client: TestClient, tmp_path: Path, site: FakeSite
) -> None:
    data = tmp_path / "data"
    w = signer_world(client, tmp_path, site)
    stages = (
        "  - connector: sign\n    calls: signer\n"
        f"    params: {{login: shop_admin, other: '{site.b}/'}}\n"
        "  - connector: sibling\n    calls: signer\n"
    )
    run = run_to_end(client, w, f"name: Sign\nstages:\n{stages}")
    assert run["status"] == "done", run
    named, sibling = (client.get(f"/api/stages/{s['id']}/call").json() for s in run["stages"])
    assert "login" not in named["request"]  # taken out before the package's params check
    said = named["response"]
    assert said["login_result"] == "signed_in", said
    assert said["names"] == [
        "SWARMIFY_LOGIN_DOMAINS", "SWARMIFY_LOGIN_NAME", "SWARMIFY_LOGIN_PASSWORD",
        "SWARMIFY_LOGIN_SESSION", "SWARMIFY_LOGIN_USERNAME",
    ]  # fmt: skip
    assert said["other"] != 200  # origin B, not the login's, is out of reach
    assert ("b", "/") not in site.hits and ("a", "/login") in site.hits
    assert sibling["response"]["names"] == []  # a stage naming no login gets none of it

    store = FileSecrets(data / "secrets.json")
    session = store.get(ref(w, "shop_admin", "session"))
    assert session and json.loads(session)["cookies"][0]["value"] in site.sessions
    assert not list((data / "sandbox").rglob("login-session*.json"))  # both gone at once

    # The next run reuses the saved session, handed in its mode-600 file
    run = run_to_end(client, w, f"name: Again\nstages:\n{stages}")
    again = client.get(f"/api/stages/{run['stages'][0]['id']}/call").json()["response"]
    assert again["login_result"] == "reused_session" and again["session_mode"] == "0o600"

    used = events(data, "login.used")
    assert [u["result"] for u in used] == ["signed_in", "reused_session"]
    assert {u["package"] for u in used} == {"signer"}
    uses = client.get(f"/api/worlds/{w}/logins/shop_admin/uses").json()
    assert uses[0]["result"] == "reused_session" and uses[0]["stage_name"] == "sign"
    with closing(connect(data / "swarmify.db")) as conn:
        everything = json.dumps([list(r) for r in conn.execute("SELECT payload FROM events")])
    assert (
        PASSWORD not in everything and json.loads(session)["cookies"][0]["value"] not in everything
    )
    for file in (data / "worlds" / w / "runs").rglob("*"):
        assert not file.is_file() or PASSWORD.encode() not in file.read_bytes(), file


def test_a_login_gone_by_the_stage_fails_it_with_a_clear_message(
    client: TestClient, tmp_path: Path, site: FakeSite
) -> None:
    data = tmp_path / "data"
    w = signer_world(client, tmp_path, site)
    yaml = (
        "name: S\nstages:\n  - gate: ok\n    ask: Go?\n"
        "  - connector: sign\n    calls: signer\n    params: {login: shop_admin}\n"
    )
    saved = client.post(f"/api/worlds/{w}/workflows", json={"yaml": yaml}).json()
    run = client.post(f"/api/workflows/{saved['workflow_id']}/runs", json={}).json()
    deadline = time.monotonic() + 60
    while client.get(f"/api/runs/{run['id']}").json()["status"] != "waiting_gate":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    with closing(connect(data / "swarmify.db")) as conn:  # gone meanwhile, as by another path
        conn.execute("DELETE FROM world_logins")
    assert client.post(f"/api/runs/{run['id']}/decision", json={}).status_code == 200
    while (run := client.get(f"/api/runs/{run['id']}").json())["status"] not in ("done", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert run["status"] == "failed"
    assert "no login named shop_admin any more" in run["stages"][1]["error"]


def test_a_secret_a_stage_leaks_is_scrubbed_everywhere_its_output_goes(
    client: TestClient, tmp_path: Path, site: FakeSite
) -> None:
    """Seam 3, scrubbing: the signer leaks the password and the session cookie in its answer, a
    page's text, stderr, a text artifact and an error; the call record, the events, the stage's
    error, the stderr tail and the artifact all hold `[secret: shop_admin]` instead."""
    data = tmp_path / "data"
    w = signer_world(client, tmp_path, site)
    stages = (
        "  - connector: sign\n    calls: signer\n    params: {login: shop_admin}\n"
        "  - connector: leak\n    calls: signer\n    output: leak.txt\n"
        "    params: {login: shop_admin, tool: leak}\n"
        "  - connector: fail\n    calls: signer\n"
        "    params: {login: shop_admin, tool: leak_error}\n"
    )
    run = run_to_end(client, w, f"name: Leak\nstages:\n{stages}")
    assert run["status"] == "failed", run
    session = FileSecrets(data / "secrets.json").get(ref(w, "shop_admin", "session"))
    cookie = json.loads(session or "{}")["cookies"][0]["value"]
    secrets = (PASSWORD, cookie)

    leaked = client.get(f"/api/stages/{run['stages'][1]['id']}/call").json()
    assert leaked["response"]["said"] == "my password is [secret: shop_admin]"
    assert "pw=[secret: shop_admin] sid=[secret: shop_admin]" in leaked["response"]["page"]
    assert "the site refused [secret: shop_admin]" in run["stages"][2]["error"]
    artifact = next((data / "worlds" / w / "runs" / run["id"]).rglob("leak.txt")).read_text()
    assert artifact == "the password is [secret: shop_admin] and the cookie [secret: shop_admin]\n"
    tails = [p.read_text() for p in (data / "sandbox").rglob("stderr.log")]
    assert any("stderr says [secret: shop_admin]" in t for t in tails), tails
    with closing(connect(data / "swarmify.db")) as conn:
        stored = json.dumps(
            [
                list(r)
                for t in ("events", "connector_calls", "runs")
                for r in conn.execute(f"SELECT * FROM {t}")
            ]
        )
    for value in secrets:
        assert value not in stored
        assert all(value not in t for t in tails)
        for file in (data / "worlds" / w / "runs").rglob("*"):
            assert not file.is_file() or value.encode() not in file.read_bytes(), file
    assert "[secret: shop_admin]" in stored


def start(client: TestClient, w: str, yaml: str) -> str:
    saved = client.post(f"/api/worlds/{w}/workflows", json={"yaml": yaml})
    assert saved.status_code == 201, saved.text
    return client.post(f"/api/workflows/{saved.json()['workflow_id']}/runs", json={}).json()["id"]


def ended(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    while (run := client.get(f"/api/runs/{run_id}").json())["status"] not in ("done", "failed"):
        assert time.monotonic() < deadline, run
        time.sleep(0.1)
    return run


def waiting_check(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while not (found := client.get(f"/api/runs/{run_id}/person-checks").json()):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    return found[0]


def test_a_person_check_is_answered_with_a_code_on_the_web_and_the_code_stays_secret(
    client: TestClient, tmp_path: Path, site: FakeSite
) -> None:
    """Seam 3, Person Check: the signer's request opens a check on the run and in Needs you, with
    its screenshot; the code typed reaches the process in a mode-600 file and nowhere else."""
    data = tmp_path / "data"
    w = signer_world(client, tmp_path, site)
    yaml = (
        "name: Code\nstages:\n  - connector: sign\n    calls: signer\n"
        "    params: {login: shop_admin, tool: ask, kind: code, ask: 'Enter the code we sent'}\n"
    )
    run_id = start(client, w, yaml)
    check = waiting_check(client, run_id)
    assert (check["kind"], check["ask"], check["site"]) == (
        "code",
        "Enter the code we sent",
        site.host_a,
    )
    assert (check["run_name"], check["stage_name"]) == ("Code", "sign")
    assert client.get("/api/person-checks").json()[0]["id"] == check["id"]
    notices = client.get("/api/notices").json()
    assert any(n["kind"] == "person_check" and n["run_id"] == run_id for n in notices)
    shot = client.get(f"/api/person-checks/{check['id']}/screenshot")
    assert shot.status_code == 200 and shot.content.startswith(b"\x89PNG")

    url = f"/api/person-checks/{check['id']}"
    assert client.post(url, json={"code": "two\nlines"}).status_code == 422
    assert client.post(url, json={"code": "Code-90210"}).status_code == 204
    assert client.post(url, json={"code": "Code-90210"}).status_code == 404  # answered once
    run = ended(client, run_id)
    assert run["status"] == "done", run
    said = client.get(f"/api/stages/{run['stages'][0]['id']}/call").json()["response"]
    assert said["answer_mode"] == "0o600" and said["echo"] == "[secret: shop_admin]"
    with closing(connect(data / "swarmify.db")) as conn:
        stored = json.dumps([list(r) for r in conn.execute("SELECT * FROM events")])
    assert "Code-90210" not in stored and "code given" in stored
    assert not client.get("/api/person-checks").json()


def test_stop_at_a_person_check_fails_the_stage(
    client: TestClient, tmp_path: Path, site: FakeSite
) -> None:
    w = signer_world(client, tmp_path, site)
    yaml = (
        "name: Look\nstages:\n  - connector: sign\n    calls: signer\n"
        "    params: {login: shop_admin, tool: ask, kind: look, ask: 'A captcha'}\n"
    )
    run_id = start(client, w, yaml)
    check = waiting_check(client, run_id)
    assert check["kind"] == "look"
    assert (
        client.post(f"/api/person-checks/{check['id']}", json={"action": "stop"}).status_code == 204
    )
    run = ended(client, run_id)
    assert (
        run["status"] == "failed" and "You stopped the sign-in check" in run["stages"][0]["error"]
    )
