"""World Lists (issue 539) over HTTP, MCP and the `lists` Agent Tool of a scripted swarm."""

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client

from swarmify_api.auth import CALLER, Token
from swarmify_api.main import (
    run_control,
    task_board,
    workflow_service,
    world_lists,
    world_service,
)
from swarmify_api.mcp import server
from swarmify_swarm import tool_id
from swarmify_swarm.fake import LAST, Answer, Call, Say, Script

IDEAS = {"statuses": ["pitched", "new", "making", "rejected"], "done": [], "rejected": ["rejected"]}


def _world(client: TestClient) -> tuple[str, str]:
    world = client.post("/api/worlds", json={"name": "Lists"}).json()["id"]
    declared = client.put(f"/api/worlds/{world}/lists/ideas", json=IDEAS)
    assert declared.status_code == 200, declared.text
    return world, f"/api/worlds/{world}/lists/ideas"


def _add(client: TestClient, url: str, key: str, **more: object) -> dict:
    added = client.post(f"{url}/entries", json={"key": key, "title": key.title(), **more})
    assert added.status_code == 201, added.text
    return added.json()


def _keys(client: TestClient, url: str, **query: Any) -> list[str]:
    found = client.get(f"{url}/entries", params=query)
    assert found.status_code == 200, found.text
    return [e["key"] for e in found.json()]


def test_a_list_is_declared_with_statuses_and_undeclared_ones_are_refused(
    client: TestClient,
) -> None:
    world, url = _world(client)
    (listed,) = client.get(f"/api/worlds/{world}/lists").json()
    assert (listed["name"], listed["statuses"], listed["rejected"]) == (
        "ideas",
        IDEAS["statuses"],
        ["rejected"],
    )
    stray = {"statuses": ["a"], "done": ["b"]}
    assert client.put(f"/api/worlds/{world}/lists/x", json=stray).status_code == 422
    assert client.put(f"/api/worlds/{world}/lists/Bad Name", json=IDEAS).status_code == 422
    assert client.put("/api/worlds/world_nope/lists/x", json=IDEAS).status_code == 404

    _add(client, url, "cats")
    assert (
        client.post(f"{url}/entries", json={"key": "b", "title": "B", "status": "x"}).status_code
        == 422
    )
    moved = client.post(f"{url}/status", json={"keys": ["cats"], "status": "published"})
    assert moved.status_code == 422 and "not a status of ideas" in moved.text
    # Declaring again may add statuses, never drop one an Entry is in.
    again = {**IDEAS, "statuses": ["new", "making", "rejected"]}
    assert client.put(url, json=again).status_code == 422
    more = {**IDEAS, "statuses": [*IDEAS["statuses"], "published"]}
    assert client.put(url, json=more).json()["statuses"][-1] == "published"
    assert client.get(f"/api/worlds/{world}/lists/nope/entries").status_code == 404


def test_add_is_idempotent_by_key_and_returns_the_entry_shape(client: TestClient) -> None:
    _, url = _world(client)
    entry = _add(
        client, url, "cats-sleep", score=7.5, fields={"format": "pov"}, file="ideas/cats.md"
    )
    assert {k: entry[k] for k in ("list", "key", "title", "status", "score", "fields", "file")} == {
        "list": "ideas",
        "key": "cats-sleep",
        "title": "Cats-Sleep",
        "status": "pitched",  # the list's first status
        "score": 7.5,
        "fields": {"format": "pov"},
        "file": "ideas/cats.md",
    }
    assert entry["claimed_by"] is None and entry["runs"] == []
    assert entry["created_at"] and entry["updated_at"]

    again = client.post(f"{url}/entries", json={"key": "cats-sleep", "title": "Other", "score": 1})
    assert again.status_code == 200 and again.json() == entry

    big = {"key": "big", "title": "Big", "fields": {"blob": "x" * 5000}}
    assert client.post(f"{url}/entries", json=big).status_code == 422
    out = {"key": "out", "title": "Out", "file": "../secrets.md"}
    assert client.post(f"{url}/entries", json=out).status_code == 422


def test_read_filters_by_status_and_fields_and_orders(client: TestClient) -> None:
    _, url = _world(client)
    _add(client, url, "a", score=3, fields={"format": "pov", "rank": 2})
    _add(client, url, "b", score=9, fields={"format": "facts", "rank": 1})
    _add(client, url, "c", fields={"format": "pov", "rank": 3}, status="new")
    _add(client, url, "d", score=5, status="new")

    assert _keys(client, url) == ["b", "d", "a", "c"]  # best score first, none last
    assert _keys(client, url, order="score") == ["a", "d", "b", "c"]
    assert _keys(client, url, order="time") == ["a", "b", "c", "d"]
    assert _keys(client, url, order="-time", limit=2) == ["d", "c"]
    assert _keys(client, url, order="rank") == ["b", "a", "c", "d"]
    assert _keys(client, url, status="new") == ["d", "c"]
    assert _keys(client, url, status=["new", "pitched"], where="format=pov") == ["a", "c"]
    assert _keys(client, url, where="rank=2") == ["a"]
    assert client.get(f"{url}/entries", params={"order": "rank;drop"}).status_code == 422
    assert client.get(f"{url}/entries", params={"status": "gone"}).status_code == 422


def test_status_and_fields_change_with_who_and_note_in_the_history(
    client: TestClient, tmp_path: Path
) -> None:
    world, url = _world(client)
    for key in ("a", "b", "c"):
        _add(client, url, key)

    moved = client.post(
        f"{url}/status", json={"keys": ["a", "b"], "status": "new", "note": "Good week."}
    )
    assert [(e["key"], e["status"]) for e in moved.json()] == [("a", "new"), ("b", "new")]
    unknown = client.post(f"{url}/status", json={"keys": ["c", "zzz"], "status": "rejected"})
    assert unknown.status_code == 404
    assert client.get(f"{url}/entries/c").json()["status"] == "pitched"  # all or none

    changed = client.patch(
        f"{url}/entries/a", json={"fields": {"format": "pov", "rank": 1}, "score": 8, "note": "Up"}
    )
    assert changed.json()["fields"] == {"format": "pov", "rank": 1}
    removed = client.patch(f"{url}/entries/a", json={"fields": {"rank": None}})
    assert removed.json()["fields"] == {"format": "pov"} and removed.json()["score"] == 8
    assert client.patch(f"{url}/entries/zzz", json={"fields": {}}).status_code == 404

    history = client.get(f"{url}/entries/a").json()["history"]
    assert [(h["type"], h.get("to"), h.get("note")) for h in history] == [
        ("entry.added", None, None),
        ("entry.changed", "new", "Good week."),
        ("entry.changed", None, "Up"),
        ("entry.changed", None, ""),
    ]
    assert history[1]["from"] == "pitched" and history[1]["by"] == "operator"
    assert history[2]["fields"] == {"format": "pov", "rank": 1} and history[2]["score"] == 8

    # A key may be a URL: it reads back through its path.
    _add(client, url, "https://example.com/a/b")
    assert client.get(f"{url}/entries/https://example.com/a/b").json()["title"]

    # Every add and change is on the world's stream; deleting the world drops its lists.
    types = [e.type for e in world_service(tmp_path).events_after(world)]
    assert types.count("entry.added") == 4 and "list.declared" in types
    assert client.delete(f"/api/worlds/{world}").status_code == 204
    assert world_lists(tmp_path).lists(world) == []


@pytest.mark.anyio
async def test_the_same_calls_on_mcp(tmp_path: Path) -> None:
    worlds = world_service(tmp_path)
    world = worlds.create("Lists", "").id
    tools = server(worlds, workflow_service(tmp_path), task_board(tmp_path), run_control(tmp_path))
    async with Client(tools) as client:

        async def call(tool: str, **args: object) -> object:
            out = await client.call_tool(tool, {"world_id": world, **args})
            assert not out.is_error, out.content
            return (out.structured_content or {}).get("result", out.structured_content)

        await call("declare_list", name="ideas", **IDEAS)
        assert [w["name"] for w in await call("list_lists")] == ["ideas"]  # type: ignore[union-attr]
        first = await call("add_entry", list="ideas", key="a", title="A", score=2)
        assert first["added"] is True  # type: ignore[index]
        again = await call("add_entry", list="ideas", key="a", title="Again")
        assert again["added"] is False and again["title"] == "A"  # type: ignore[index]
        await call("add_entry", list="ideas", key="b", title="B", score=5, fields={"rank": 1})
        found = await call("read_list", list="ideas", order="-score")
        assert [e["key"] for e in found] == ["b", "a"]  # type: ignore[union-attr]
        moved = await call("set_entry_status", list="ideas", keys=["a", "b"], status="new")
        assert {e["status"] for e in moved} == {"new"}  # type: ignore[union-attr]
        await call("set_entry_fields", list="ideas", key="a", fields={"rank": 2}, note="Why")
        entry = await call("get_entry", list="ideas", key="a")
        assert entry["fields"] == {"rank": 2}  # type: ignore[index]
        assert entry["history"][-1]["note"] == "Why"  # type: ignore[index]

        bad = await client.call_tool(
            "set_entry_status", {"world_id": world, "list": "ideas", "keys": ["a"], "status": "x"}
        )
        assert bad.is_error
        # A token scoped to another world reaches none of these.
        reset = CALLER.set(Token(id="t", name="scoped", created_at="t", world_id="world_x"))
        try:
            assert (await client.call_tool("list_lists", {"world_id": world})).is_error
        finally:
            CALLER.reset(reset)
    assert world_lists(tmp_path).get(world, "ideas", "a").status == "new"


LISTS_STAGE = """name: Pitch
stages:
  - swarm: pitch
    goal: Pitch ideas into the ideas list.
    done_when: pitched
    tools: [lists]
"""


def test_a_scripted_swarm_agent_adds_and_reads_entries_through_the_lists_tool(
    script: Script, app: FastAPI
) -> None:
    add = Call(
        tool_id("add_entry"),
        {"list": "ideas", "key": "cat-naps", "title": "Cat naps", "score": 6, "fields": {"f": 1}},
    )
    read = Call(tool_id("read_list"), {"list": "ideas", "status": ["pitched"]})
    move = Call(
        tool_id("set_entry_status"),
        {"list": "ideas", "keys": ["cat-naps"], "status": "new", "note": "Fits."},
    )
    script.scripts = [
        [Answer({"tasks": [{"title": "Pitch"}]})],
        [
            Call(tool_id("query_graph"), {"question": "Ideas?"}),
            Call(tool_id("claim_task")),
            add,
            read,
            move,
            Call(tool_id("finish_task"), {"task_id": LAST, "note": "Done."}),
            Say("Pitched."),
        ],
        [Answer({"passed": True, "reasons": ["Pitched."]})],
    ]
    with TestClient(app) as client:
        world, url = _world(client)
        wf = client.post(f"/api/worlds/{world}/workflows", json={"yaml": LISTS_STAGE}).json()
        run = client.post(f"/api/workflows/{wf['workflow_id']}/runs", json={}).json()
        deadline = time.monotonic() + 15
        while client.get(f"/api/runs/{run['id']}").json()["status"] not in ("done", "failed"):
            assert time.monotonic() < deadline, "timed out"
            time.sleep(0.05)
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "done"

        assert not any(o.is_error for o in script.outcomes), script.outcomes
        entry = client.get(f"{url}/entries/cat-naps").json()
        assert (entry["status"], entry["score"], entry["fields"]) == ("new", 6, {"f": 1})
        added, moved = entry["history"]
        assert added["by"] == moved["by"] == "worker-1"
        assert added["run_id"] == moved["run_id"] == run["id"] and moved["note"] == "Fits."
