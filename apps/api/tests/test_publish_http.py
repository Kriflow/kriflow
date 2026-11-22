"""Publishing over HTTP (ADR 0007): the fake_publish fixture's sign-in, the confirm gate that
shows exactly what goes out, the call that sends it, the publish artifact with its link, and the
editor and runner refusing a publish stage no gate precedes. The fake ConnectorTransport stands in
for the connector processes, answering `preview` as fake_publish's own does; DBOS runs for
real on SQLite."""

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from api_fakes import FAKE_PUBLISH_ENV, sign_in_fake_publish
from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarmify_connectors import ConnectorError, FakeTransport, oauth
from swarmify_connectors.fake_oauth import FakeProvider

MAKER = {
    "name": "maker",
    "kind": "stage",
    "server": "uvx maker-mcp",
    "credentials": {},
    "stage_types": ["connector"],
}


def weekly(calls: str = "fake_publish", extra: str = "") -> str:
    return f"""name: Weekly short
stages:
  - connector: render
    calls: maker
    output: short.mp4
  - gate: confirm
    ask: Post it?
    kind: confirm
  - connector: publish
    calls: {calls}
    input: short.mp4
    params:
      title: Five hooks that work
      visibility: public
      schedule: "2099-01-01T10:00:00+01:00"{extra}
"""


@pytest.fixture
def running(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def vals(shown: dict) -> dict:
    """A gate's publish preview as its values by key."""
    return {f["key"]: f["value"] for f in shown["fields"]}


def until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def setup(client: TestClient, yaml: str) -> tuple[str, str]:
    world = client.post("/api/worlds", json={"name": "Video"}).json()["id"]
    assert client.post(f"/api/worlds/{world}/connectors", json=MAKER).status_code == 201
    saved = client.post(f"/api/worlds/{world}/workflows", json={"yaml": yaml})
    assert saved.status_code == 201, saved.text
    return world, saved.json()["workflow_id"]


def at_gate(client: TestClient, tmp_path: Path, world: str, workflow: str) -> dict:
    run = client.post(f"/api/workflows/{workflow}/runs", json={}).json()
    until(lambda: client.get(f"/api/runs/{run['id']}").json()["status"] == "waiting_gate")
    # What the render stage would have left in the run folder.
    out = tmp_path / "worlds" / world / "runs" / run["id"] / "01-render" / "short.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"video")
    return client.get(f"/api/runs/{run['id']}").json()


def test_a_publish_package_whose_account_is_signed_in_from_the_connectors_page(
    running: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = running.post("/api/worlds", json={"name": "Video"}).json()["id"]
    listed = {c["name"]: c for c in running.get(f"/api/worlds/{world}/connectors").json()}
    assert listed["fake_publish"]["version"] == "1.0.0"
    assert listed["fake_publish"]["missing"] == ["account"]  # the account, not keys

    url = f"/api/worlds/{world}/packages/fake_publish/setup"
    form = running.get(url).json()
    assert [(f["key"], f["type"]) for f in form["fields"]] == [
        ("account_client_id", "string"),
        ("account_client_secret", "string"),
        ("account", "oauth"),
    ]
    body = {"shared": {"account_client_id": "app", "account_client_secret": "shh"}}
    assert running.put(url, json=body).status_code == 200
    started = running.post(f"{url}/oauth/account").json()
    asked = urlparse(started["url"])
    q = {k: v[0] for k, v in parse_qs(asked.query).items()}
    assert f"{asked.scheme}://{asked.netloc}{asked.path}" == "https://auth.example.org/authorize"
    assert q["scope"].split() == ["upload", "read"]
    assert q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == started["redirect_uri"]

    # The platform, played by the fake provider: it consents, then trades the code for tokens.
    with FakeProvider(client_id="app", client_secret="shh") as platform:
        post = httpx.post
        monkeypatch.setattr(
            oauth.httpx, "post", lambda _url, **kw: post(f"{platform.url}/token", **kw)
        )
        back = urlparse(httpx.get(f"{platform.url}/authorize?{asked.query}").headers["location"])
        done = running.get(f"/api/setup/oauth/callback?{back.query}", follow_redirects=False)
        assert done.headers["location"] == f"/worlds/{world}/connectors?sign_in=fake_publish"
        assert platform.grants == ["authorization_code"]
    listed = {c["name"]: c for c in running.get(f"/api/worlds/{world}/connectors").json()}
    assert listed["fake_publish"]["missing"] == [] and listed["fake_publish"]["sign_in"] == []
    assert "rt-" not in running.get(url).text and "at-" not in running.get(url).text


def test_the_confirm_gate_shows_what_goes_out_and_confirming_posts_exactly_that(
    running: TestClient, transport: FakeTransport, tmp_path: Path
) -> None:
    sign_in_fake_publish(tmp_path)
    world, workflow = setup(running, weekly())
    run = at_gate(running, tmp_path, world, workflow)

    (gate,) = running.get("/api/gates").json()
    assert gate["kind"] == "confirm"
    assert (gate["publish"]["connector"], gate["publish"]["problems"]) == ("fake_publish", [])
    assert vals(gate["publish"]) == {
        "title": "Five hooks that work",
        "description": "",
        "visibility": "public",
        "schedule": "2099-01-01T09:00:00Z",
        "video": "short.mp4",
    }
    assert [f["key"] for f in gate["publish"]["fields"] if f["editable"]] == [
        "title",
        "description",
    ]
    # its own preview, over the transport: when the workflow was saved, then at the gate
    assert [c[:2] for c in transport.calls if c[1] != "preview"] == [("maker", "maker")]
    assert transport.calls[0][:2] == ("fake_publish", "preview")

    transport.outputs["publish"] = [
        {
            "id": "vid1",
            "url": "https://youtu.be/vid1",
            "status": "scheduled",
            "public": True,
            "scheduled_for": "2099-01-01T09:00:00Z",
        }
    ]
    assert running.post(f"/api/runs/{run['id']}/decision", json={}).status_code == 200
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] == "done")

    name, tool, args, env = transport.calls[-1]
    assert (name, tool, env) == ("fake_publish", "publish", FAKE_PUBLISH_ENV)
    assert args["fields"] == vals(gate["publish"]) and args["idempotency_key"]
    assert args["input_path"].endswith(f"{run['id']}/01-render/short.mp4")

    stage_id = run["stages"][2]["id"]
    artifact = tmp_path / "worlds" / world / "runs" / run["id"] / stage_id / "artifacts"
    text = (artifact / "publish.md").read_text()
    assert text.startswith('---\ntype: "publish"\n')
    assert 'links: {"fake_publish": "https://youtu.be/vid1"}\n' in text
    assert 'status: "scheduled"\n' in text and 'id: "vid1"\n' in text
    # for upkeep to check: the connector's word that it went out for everyone, and when, and
    # that the stage said so itself (every param its connector's private_when names)
    assert "public: true\n" in text and 'scheduled_for: "2099-01-01T09:00:00Z"\n' in text
    assert "visibility_said: true\n" in text and 'visibility: "public"' not in text
    assert "- When: 2099-01-01T09:00:00Z\n" in text and "- Visibility: public\n" in text
    assert "- fake_publish: https://youtu.be/vid1\n" in text


@pytest.mark.parametrize("kind", ["confirm", "approve"])
def test_a_gate_before_a_broken_publish_stage_refuses_to_go_on(
    running: TestClient, transport: FakeTransport, tmp_path: Path, kind: str
) -> None:
    # the metadata file is missing only at run time; the check on save cannot see that
    yaml = weekly(extra="\n      metadata: script.md").replace("kind: confirm", f"kind: {kind}")
    world, workflow = setup(running, yaml)
    run = at_gate(running, tmp_path, world, workflow)
    shown = running.get("/api/gates").json()[0]["publish"]
    assert any("script.md" in p for p in shown["problems"])
    refused = running.post(f"/api/runs/{run['id']}/decision", json={})
    assert refused.status_code == 409 and "script.md" in refused.json()["detail"]
    assert running.get(f"/api/runs/{run['id']}").json()["status"] == "waiting_gate"
    assert [c[1] for c in transport.calls if c[1] != "preview"] == ["maker"]


SCRIPT = """---
title: "Why owls turn their heads"
description: |
  Owls cannot move their eyes.
  So they turn their heads.
hashtags: ["#Owls", "#AnimalFacts"]
tags: [owls, owl facts]
---

# Script
"""


def test_title_and_description_come_from_an_earlier_artifact_and_the_gate_edits_them(
    running: TestClient, transport: FakeTransport, tmp_path: Path
) -> None:
    sign_in_fake_publish(tmp_path)
    extra = "\n      metadata: script.md\n      description: fallback"
    world, workflow = setup(running, weekly(extra=extra))
    run = at_gate(running, tmp_path, world, workflow)
    folder = tmp_path / "worlds" / world / "runs" / run["id"] / "01-render"
    (folder / "script.md").write_text(SCRIPT)

    preview = running.get("/api/gates").json()[0]["publish"]
    shown = vals(preview)
    assert shown["title"] == "Why owls turn their heads"
    assert shown["description"] == "Owls cannot move their eyes.\nSo they turn their heads."
    assert preview["problems"] == []
    assert shown["schedule"] == "2099-01-01T09:00:00Z"

    url = f"/api/runs/{run['id']}/decision"
    refused = running.post(url, json={"edits": {"title": "x" * 101}})
    assert refused.status_code == 422 and "the limit is 100" in refused.json()["detail"]
    fixed = running.post(url, json={"edits": {"visibility": "private"}})
    assert fixed.status_code == 422 and "visibility cannot be edited" in fixed.json()["detail"]
    transport.outputs["publish"] = [{"id": "v", "url": "https://youtu.be/v"}]
    edited = {"title": "Owls, explained", "description": "Edited."}
    stale = {**shown, "schedule": "2099-01-02T09:00:00Z"}
    moved = running.post(url, json={"edits": edited, "seen": stale})
    assert moved.status_code == 422 and "When changed since" in moved.json()["detail"]
    assert running.post(url, json={"edits": edited, "seen": shown}).status_code == 200
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] == "done")

    _, tool, args, _ = transport.calls[-1]
    (decision,) = running.get(f"/api/runs/{run['id']}").json()["decisions"]
    assert decision["publish"] == {**shown, **edited}
    assert (tool, args["fields"]) == ("publish", decision["publish"])
    stage_id = run["stages"][2]["id"]
    artifact = tmp_path / "worlds" / world / "runs" / run["id"] / stage_id / "artifacts"
    assert (
        (artifact / "publish.md")
        .read_text()
        .split("\n---\n", 1)[1]
        .startswith("\n# Owls, explained")
    )


def test_an_emoji_in_the_metadata_title_reaches_the_gate_whole(
    running: TestClient, tmp_path: Path
) -> None:
    """Frontmatter is written with json.dumps, which escapes an emoji as a surrogate pair; the
    gate list once answered 500 on it, so the gate had no Approve button."""
    world, workflow = setup(running, weekly(extra="\n      metadata: script.md"))
    run = at_gate(running, tmp_path, world, workflow)
    folder = tmp_path / "worlds" / world / "runs" / run["id"] / "01-render"
    title = json.dumps("Why a cast saw can't cut you \N{FEARFUL FACE}")
    (folder / "script.md").write_text(f"---\ntitle: {title}\n---\n\n# Script\n")
    assert "\\ud83d\\ude28" in (folder / "script.md").read_text()

    gates = running.get("/api/gates")
    assert gates.status_code == 200
    title = vals(gates.json()[0]["publish"])["title"]
    assert title == "Why a cast saw can't cut you \N{FEARFUL FACE}"


def test_a_missing_metadata_artifact_is_a_problem_the_gate_shows(
    running: TestClient, tmp_path: Path
) -> None:
    world, workflow = setup(running, weekly(extra="\n      metadata: script.md"))
    at_gate(running, tmp_path, world, workflow)
    shown = running.get("/api/gates").json()[0]["publish"]
    assert vals(shown)["title"] == "Five hooks that work"
    assert any("script.md" in p for p in shown["problems"])


def test_the_editor_refuses_a_publish_stage_without_a_gate(client: TestClient) -> None:
    world = client.post("/api/worlds", json={"name": "Video"}).json()["id"]
    yaml = "name: Sneaky\nstages:\n  - connector: publish\n    calls: fake_publish\n"
    checked = client.post(f"/api/worlds/{world}/workflows/check", json={"yaml": yaml}).json()
    assert [v["rule"] for v in checked["violations"]][0] == "publish_without_gate"
    saved = client.post(f"/api/worlds/{world}/workflows", json={"yaml": yaml})
    assert saved.status_code == 422


def test_a_private_upload_with_no_gate_runs_and_posts(
    running: TestClient, transport: FakeTransport, tmp_path: Path
) -> None:
    sign_in_fake_publish(tmp_path)
    yaml = weekly().replace("  - gate: confirm\n    ask: Post it?\n    kind: confirm\n", "")
    yaml = yaml.replace("visibility: public", "visibility: private").replace(
        '      schedule: "2099-01-01T10:00:00+01:00"\n', ""
    )
    world, workflow = setup(running, yaml)

    def render(args: dict) -> dict:  # what the render stage would leave in the run folder
        out = Path(args["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"video")
        return {"ok": True}

    transport.outputs["maker.maker"] = [render]
    transport.outputs["publish"] = [
        {"id": "vid1", "url": "https://youtu.be/vid1", "status": "private"}
    ]
    run = running.post(f"/api/workflows/{workflow}/runs", json={}).json()
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] in ("done", "failed"))
    ended = running.get(f"/api/runs/{run['id']}").json()
    assert ended["status"] == "done", ended["stages"][-1]["error"]
    name, tool, args, _ = transport.calls[-1]
    assert (name, tool, args["fields"]["visibility"]) == ("fake_publish", "publish", "private")


def test_a_run_keeps_the_version_it_was_saved_with_when_a_later_one_turned_publish(
    running: TestClient, transport: FakeTransport
) -> None:
    """The run-start check (#102) and the run itself both use the package version the workflow
    version recorded (#223), so a later version that publishes never runs ungated."""
    yaml = "name: Sneaky\nstages:\n  - connector: post\n    calls: poster\n"
    poster = {**MAKER, "name": "poster"}
    world, workflow = setup(running, "name: x\nstages:\n  - connector: m\n    calls: maker\n")
    running.post(f"/api/worlds/{world}/connectors", json=poster).raise_for_status()
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": yaml}).json()
    running.delete(f"/api/worlds/{world}/packages/poster")
    publishing = {**poster, "kind": "publish"}
    assert running.post(f"/api/worlds/{world}/connectors", json=publishing).status_code == 201
    assert running.post(f"/api/worlds/{world}/workflows", json={"yaml": yaml}).status_code == 422

    run = running.post(f"/api/workflows/{saved['workflow_id']}/runs", json={})
    assert run.status_code == 201, run.text
    until(lambda: running.get(f"/api/runs/{run.json()['id']}").json()["status"] == "done")
    called = [(c[1], v) for c, v in zip(transport.calls, transport.versions, strict=True)]
    assert ("poster", saved["packages"]["poster"]) in called
    assert not [c for c in transport.calls if c[1] == "publish"]  # previewed only by the save


def test_a_worlds_own_publish_connector_posts_text_through_the_same_gate(
    running: TestClient, transport: FakeTransport, tmp_path: Path
) -> None:
    """The core holds the gate and knows no fields: a webhook posting a message works the same."""
    hook = {**MAKER, "name": "webhook", "kind": "publish", "server": "uvx webhook-mcp"}
    yaml = weekly("webhook").replace("input: short.mp4", "input: short.mp4\n    params: {}")
    yaml = yaml.split("    params:\n      title")[0]
    world = running.post("/api/worlds", json={"name": "News"}).json()["id"]
    for connector in (MAKER, hook):
        assert running.post(f"/api/worlds/{world}/connectors", json=connector).status_code == 201
    message = {"key": "text", "label": "Message", "value": "New video is up", "editable": True}
    transport.outputs["preview"] = [
        {"fields": [{"key": "to", "label": "To", "value": "#news"}, message], "problems": []}
    ]
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": yaml})
    assert saved.status_code == 201, saved.text
    workflow = saved.json()["workflow_id"]
    run = at_gate(running, tmp_path, world, workflow)
    shown = running.get("/api/gates").json()[0]["publish"]
    assert vals(shown) == {"to": "#news", "text": "New video is up"}

    transport.outputs["publish"] = [{"url": "https://hooks.example/m/1"}]
    edits = {"text": "It is up"}
    answer = running.post(f"/api/runs/{run['id']}/decision", json={"edits": edits})
    assert answer.status_code == 200
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] == "done")
    (decision,) = running.get(f"/api/runs/{run['id']}").json()["decisions"]
    assert decision["publish"] == {"to": "#news", "text": "It is up"}
    name, tool, args, _ = transport.calls[-1]
    assert (name, tool, args["fields"]) == ("webhook", "publish", decision["publish"])
    assert args["input_path"].endswith("01-render/short.mp4")
    # the connector hears the edits, and they stand though this fake ignores them
    assert edits in [c[2]["edits"] for c in transport.calls if c[1] == "preview"]


MAKE = """name: Make
given: [item.md]
stages:
  - connector: render
    calls: maker
    output: short.mp4
  - gate: confirm
    ask: Post it everywhere?
    kind: confirm
    entry: {approve: review}
"""
POST = """  - connector: %s
    calls: fake_publish
    input: short.mp4
    params:
      title: Five hooks that work
      visibility: public
      schedule: "2099-01-01T10:00:00+01:00"
"""


@pytest.mark.parametrize("broken", [False, True], ids=["moves", "links-field-broken"])
def test_a_listed_entry_is_claimed_approved_and_published_by_both_stages_after_one_gate(
    running: TestClient, transport: FakeTransport, tmp_path: Path, broken: bool
) -> None:
    """Issue 541 end to end: a daily fan-out claims the top Entry, the person's confirm moves
    it, both publish stages after that one gate post, and the Entry ends published with both
    links and no claim. An Entry whose fields cannot take the links stays where the gate put
    it, and the posts stand: the stages still end done, nothing posts twice."""
    sign_in_fake_publish(tmp_path)
    world, make = setup(running, MAKE + POST % "post" + POST % "again")
    lists = f"/api/worlds/{world}/lists/tickets"
    statuses = ["new", "working", "review", "published"]
    declared = running.put(lists, json={"statuses": statuses, "published": "published"})
    assert declared.status_code == 200, declared.text
    assert declared.json()["published"] == "published"
    for key, rank in (("later", 2), ("first", 1)):
        fields = {"rank": rank} | ({"links": "x", "pad": "y" * 3960} if broken else {})
        body = {"key": key, "title": key.title(), "fields": fields}
        assert running.post(f"{lists}/entries", json=body).status_code == 201
    daily = (
        "name: Daily\nstages:\n  - workflow: make\n"
        f"    runs: {make}\n"
        "    from_list: {list: tickets, status: new, order: rank, on_start: working}\n"
    )
    saved = running.post(f"/api/worlds/{world}/workflows", json={"yaml": daily})
    assert saved.status_code == 201, saved.text
    parent = running.post(f"/api/workflows/{saved.json()['workflow_id']}/runs", json={}).json()

    def child() -> dict:
        found = running.get(f"/api/runs/{parent['id']}").json()["stages"][0]["items"]
        run_id = found[0]["child"]["id"] if found and found[0]["child"] else None
        return running.get(f"/api/runs/{run_id}").json() if run_id else {"status": "none"}

    until(lambda: child()["status"] == "waiting_gate")
    made = child()
    out = tmp_path / "worlds" / world / "runs" / made["id"] / made["stages"][0]["id"]
    (out / "short.mp4").parent.mkdir(parents=True, exist_ok=True)
    (out / "short.mp4").write_bytes(b"video")
    entry = running.get(f"{lists}/entries/first").json()
    assert (entry["status"], entry["claimed_by"]) == ("working", made["id"])

    transport.outputs["publish"] = [
        {"id": "v1", "url": "https://youtu.be/v1", "public": True},
        {"id": "v2", "links": {"again": "https://again.example/v2"}, "public": True},
    ]
    assert running.post(f"/api/runs/{made['id']}/decision", json={}).status_code == 200
    until(lambda: running.get(f"/api/runs/{parent['id']}").json()["status"] == "done")
    assert [c[1] for c in transport.calls if c[0] == "fake_publish"].count("publish") == 2
    entry = running.get(f"{lists}/entries/first").json()
    if broken:  # over MAX_FIELDS once the links merge in
        assert (entry["status"], entry["claimed_by"], entry["fields"]["links"]) == (
            "review",
            None,
            "x",
        )
        return
    assert (entry["status"], entry["claimed_by"]) == ("published", None)
    assert entry["fields"]["links"] == {
        "fake_publish": "https://youtu.be/v1",
        "again": "https://again.example/v2",
    }
    assert running.get(f"{lists}/entries/later").json()["status"] == "new"


def test_a_publish_that_cannot_tell_whether_it_posted_asks_and_it_posted_records_the_links(
    running: TestClient, transport: FakeTransport, tmp_path: Path
) -> None:
    sign_in_fake_publish(tmp_path)
    world, workflow = setup(running, weekly())
    run = at_gate(running, tmp_path, world, workflow)
    transport.outputs["publish"] = [ConnectorError("unsure: the upload session did not answer")]
    assert running.post(f"/api/runs/{run['id']}/decision", json={}).status_code == 200
    until(lambda: [g["kind"] for g in running.get("/api/gates").json()] == ["posted"])
    (gate,) = running.get("/api/gates").json()
    assert (gate["kind"], gate["stage_name"], gate["publish"]) == ("posted", "publish", None)
    assert gate["ask"] == (
        "Fake Publish was cut off while posting and could not check whether it posted: the"
        " upload session did not answer. Look for it on Fake Publish, then answer."
    )
    url = f"/api/runs/{run['id']}/decision"
    refused = running.post(url, json={"choice": "posted", "links": []})
    assert refused.status_code == 422 and "link" in refused.json()["detail"]
    assert running.post(url, json={"choice": "posted", "links": ["not a link"]}).status_code == 422
    link = "https://www.youtube.com/shorts/vid1"
    assert running.post(url, json={"choice": "posted", "links": [link]}).status_code == 200
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] == "done")
    assert [c[1] for c in transport.calls].count("publish") == 1  # never posted again

    stage_id = run["stages"][2]["id"]
    folder = tmp_path / "worlds" / world / "runs" / run["id"] / stage_id / "artifacts"
    text = (folder / "publish.md").read_text()
    assert (
        f'links: {{"youtube": "{link}"}}\n' in text
    )  # named by the link's host and 'recorded_by: "person"\n' in text
    log = running.get(f"/api/runs/{run['id']}/stages/{stage_id}/log").text
    assert "nothing was posted again" in log  # the stage's history says why


def test_post_it_now_posts_again_under_a_new_key(
    running: TestClient, transport: FakeTransport, tmp_path: Path
) -> None:
    sign_in_fake_publish(tmp_path)
    world, workflow = setup(running, weekly())
    run = at_gate(running, tmp_path, world, workflow)
    transport.outputs["publish"] = [
        ConnectorError("unsure: the upload session did not answer"),
        {"id": "vid2", "url": "https://youtu.be/vid2", "public": True},
    ]
    running.post(f"/api/runs/{run['id']}/decision", json={})
    until(lambda: [g["kind"] for g in running.get("/api/gates").json()] == ["posted"])
    answered = running.post(f"/api/runs/{run['id']}/decision", json={"choice": "post"})
    assert answered.status_code == 200
    until(lambda: running.get(f"/api/runs/{run['id']}").json()["status"] == "done")
    keys = [c[2]["idempotency_key"] for c in transport.calls if c[1] == "publish"]
    assert len(keys) == 2 and keys[0] != keys[1]
    decisions = running.get(f"/api/runs/{run['id']}").json()["decisions"]
    assert (decisions[-1]["kind"], decisions[-1]["choice"]) == ("posted", "post")
