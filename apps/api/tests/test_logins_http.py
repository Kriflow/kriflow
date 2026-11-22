"""World Logins over HTTP and MCP (spec seam 1): create, list, change, rotate, forget a session,
delete; a taken name, bad domains and a bad authenticator secret refused; no response body or
event ever holds a password, an authenticator secret or a session, refused bodies included; a
login a saved workflow names cannot be deleted; MCP lists logins and cannot create one."""

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from api_fakes import install
from fastapi.testclient import TestClient
from mcp import Client

from swarmify_api.main import (
    connector_service,
    run_control,
    task_board,
    workflow_service,
    world_service,
)
from swarmify_api.mcp import server
from swarmify_connectors.logins import FileSecrets, ref
from swarmify_kernel.sqlite import connect

SIGNER = "packages/connectors/tests/fixtures/packages/signer"
PASSWORD, NEW_PASSWORD = "Pa55-word-9183", "N3w-pa55-2207"
TOTP = "JBSWY3DPEHPK3PXP"
SESSION = '{"cookies": [{"name": "sid", "value": "s3ssion-c00kie-41"}]}'
SECRETS = (PASSWORD, NEW_PASSWORD, TOTP, "s3ssion-c00kie-41")


def events(data: Path) -> str:
    """Every event of every world, as the stream would serve them."""
    with closing(connect(data / "swarmify.db")) as conn:
        return "\n".join(f"{r[0]} {r[1]}" for r in conn.execute("SELECT type, payload FROM events"))


def clean(text: str) -> None:
    for value in SECRETS:
        assert value not in text, value


def test_a_login_lives_through_the_api_without_ever_showing_a_secret(
    client: TestClient, tmp_path: Path
) -> None:
    w = client.post("/api/worlds", json={"name": "Shop"}).json()["id"]
    seen: list[str] = []

    def call(method: str, path: str, **kw: Any) -> Any:
        r = client.request(method, f"/api/worlds/{w}/logins{path}", **kw)
        seen.append(r.text)
        return {"status": r.status_code, "body": r.json() if r.content else None}

    body = {
        "name": "shop_admin",
        "domains": ["admin.myshop.com", "*.myshop.com"],
        "username": "ana",
        "password": PASSWORD,
        "totp_secret": TOTP.lower(),
    }
    made = call("POST", "", json=body)
    assert made["status"] == 201, made
    assert made["body"]["has_totp"] and not made["body"]["has_session"]
    assert made["body"]["username"] == "ana" and made["body"]["workflows"] == []
    assert call("POST", "", json=body)["status"] == 409
    for bad, says in [
        ({**body, "name": "two", "domains": ["*.com"]}, "Domains: *.com is too broad"),
        ({**body, "name": "two", "domains": ["169.254.169.254"]}, "is this box itself"),
        ({**body, "name": "two", "domains": ["127.0.0.1:8000"]}, "is this box itself"),
        ({**body, "name": "two", "totp_secret": "nope!"}, "Authenticator secret"),
        ({**body, "name": "Two"}, "Name"),
        ({**body, "name": "two", "password": ""}, "Password"),
    ]:
        refused = call("POST", "", json=bad)
        assert refused["status"] == 422 and says in refused["body"]["detail"], refused
    # A body pydantic cannot read is refused without echoing it
    assert call("POST", "", json={**body, "name": "two", "domains": PASSWORD})["status"] == 422
    assert call("POST", "", content=f'"{PASSWORD}"')["status"] == 422

    listed = call("GET", "")
    assert [x["name"] for x in listed["body"]] == ["shop_admin"]
    changed = call("PATCH", "/shop_admin", json={"username": "ana2"})
    assert changed["body"]["username"] == "ana2" and changed["body"]["has_totp"]
    assert call("PATCH", "/shop_admin", json={"domains": ["*"]})["status"] == 422

    # the session a stage saved, as the core keeps it
    FileSecrets(tmp_path / "secrets.json").put(ref(w, "shop_admin", "session"), SESSION)
    assert call("GET", "/shop_admin")["body"]["has_session"]
    forgot = call("DELETE", "/shop_admin/session")
    assert not forgot["body"]["has_session"]
    rotated = call(
        "POST", "/shop_admin/rotate", json={"password": NEW_PASSWORD, "clear_totp": True}
    )
    assert not rotated["body"]["has_totp"]
    assert call("POST", "/nope/rotate", json={"password": "x"})["status"] == 404
    assert call("GET", "/shop_admin/uses")["body"] == []

    for kind in ("added", "changed", "rotated", "session_forgotten"):
        assert f"login.{kind}" in events(tmp_path)
    assert call("DELETE", "/shop_admin")["status"] == 204
    assert "login.deleted" in events(tmp_path)
    assert call("GET", "")["body"] == []
    clean("\n".join(seen) + events(tmp_path))


def test_deleting_a_login_a_saved_workflow_names_is_refused_with_its_name(
    client: TestClient,
) -> None:
    w = client.post("/api/worlds", json={"name": "Shop"}).json()["id"]
    assert install(client, SIGNER).status_code == 201  # a package that accepts a login
    assert client.put(f"/api/worlds/{w}/packages/signer", json={"version": "1.0.0"}).is_success
    body = {"name": "shop_admin", "domains": ["a.com"], "username": "u", "password": PASSWORD}
    assert client.post(f"/api/worlds/{w}/logins", json=body).status_code == 201
    yaml = (
        "name: Restock\nstages:\n  - connector: order\n    calls: signer\n"
        "    params: {login: shop_admin}\n"
    )
    saved = client.post(f"/api/worlds/{w}/workflows", json={"yaml": yaml})
    assert saved.status_code == 201, saved.text
    refused = client.delete(f"/api/worlds/{w}/logins/shop_admin")
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["workflows"] == [{"id": saved.json()["workflow_id"], "name": "Restock"}]
    assert "Restock names this login" in detail["message"]
    listed = client.get(f"/api/worlds/{w}/logins").json()
    assert listed[0]["workflows"][0]["name"] == "Restock"


def test_a_world_delete_takes_its_logins(client: TestClient, tmp_path: Path) -> None:
    w = client.post("/api/worlds", json={"name": "Shop"}).json()["id"]
    body = {"name": "shop_admin", "domains": ["a.com"], "username": "u", "password": PASSWORD}
    client.post(f"/api/worlds/{w}/logins", json=body)
    assert client.delete(f"/api/worlds/{w}").status_code == 204
    assert PASSWORD not in (tmp_path / "secrets.json").read_text()


@pytest.mark.anyio
async def test_mcp_lists_logins_without_secrets_and_cannot_add_one(tmp_path: Path) -> None:
    worlds = world_service(tmp_path)
    w = worlds.create("Shop").id
    registry = connector_service(tmp_path)
    registry.logins.add(w, "shop_admin", ["a.com"], "ana", PASSWORD, TOTP)
    tools = server(
        worlds, workflow_service(tmp_path), task_board(tmp_path), run_control(tmp_path),
        logins=registry.logins,
    )  # fmt: skip
    async with Client(tools) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert "list_logins" in names
        assert not [n for n in names if "login" in n and n != "list_logins"]
        listed = await client.call_tool("list_logins", {"world_id": w})
        found = listed.structured_content["result"]  # type: ignore[index]
        assert [(x["name"], x["username"], x["has_totp"]) for x in found] == [
            ("shop_admin", "ana", True)
        ]
        clean(str(listed))
