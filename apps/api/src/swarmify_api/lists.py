"""HTTP face of World Lists (issue 539), and the `lists` Agent Tool's answers for the swarm.

One `WorldLists` serves HTTP, MCP and agents alike: KeyError is a 404, ValueError a 422.
"""

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from swarmify_runs import Entry, WorldList, WorldLists
from swarmify_swarm import OPERATOR, ToolError

WorldExists = Callable[[str], bool]


class Declared(BaseModel):
    statuses: list[str] = Field(min_length=1, max_length=40)
    """In order: the first is where an added Entry starts."""
    done: list[str] = []
    rejected: list[str] = []
    published: str | None = None
    """Where an Entry goes when the run holding it publishes; left where it is when None."""


class NewEntry(BaseModel):
    key: str
    title: str
    status: str | None = None
    score: float | None = Field(default=None, allow_inf_nan=False)
    fields: dict[str, Any] = {}
    file: str | None = None


class StatusChange(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=200)
    status: str
    note: str = Field(default="", max_length=2000)


class FieldsChange(BaseModel):
    fields: dict[str, Any] = {}
    """Merged into the Entry's; a null removes that field."""
    score: float | None = Field(default=None, allow_inf_nan=False)
    note: str = Field(default="", max_length=2000)


def entry_view(lists: WorldLists, world_id: str, name: str, key: str) -> dict[str, Any]:
    """An Entry with its history: every add and change, who made it and why."""
    entry = lists.get(world_id, name, key)
    history = [
        {"type": e.type, "ts": e.ts, "run_id": e.run_id, **e.payload}
        for e in lists.history(world_id, name, key)
    ]
    return {**asdict(entry), "history": history}


def parse_where(where: list[str]) -> dict[str, Any]:
    """`name=value` filters; a value that reads as JSON (a number, true) is taken as such."""
    out: dict[str, Any] = {}
    for item in where:
        name, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"field filter {item!r}: write name=value")
        try:
            out[name] = json.loads(value)
        except ValueError:
            out[name] = value
    return out


def router(lists: WorldLists, world_exists: WorldExists) -> APIRouter:
    api = APIRouter(prefix="/api/worlds/{world_id}/lists", tags=["lists"])

    def act[T](world_id: str, fn: Callable[[], T]) -> T:
        if not world_exists(world_id):
            raise HTTPException(404, "world not found")
        try:
            return fn()
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.get("")
    def list_lists(world_id: str) -> list[WorldList]:
        return act(world_id, lambda: lists.lists(world_id))

    @api.put("/{name}")
    def declare(world_id: str, name: str, body: Declared) -> WorldList:
        return act(
            world_id,
            lambda: lists.declare(
                world_id,
                name,
                body.statuses,
                body.done,
                body.rejected,
                published=body.published,
            ),  # fmt: skip
        )

    @api.get("/{name}/entries")
    def read(
        world_id: str,
        name: str,
        status: Annotated[list[str] | None, Query()] = None,
        where: Annotated[list[str] | None, Query(description="name=value")] = None,
        order: str = "-score",
        limit: int | None = Query(default=None, ge=0),
    ) -> list[Entry]:
        return act(
            world_id,
            lambda: lists.read(
                world_id,
                name,
                status=status,
                where=parse_where(where or []),
                order=order,
                limit=limit,
            ),  # fmt: skip
        )

    @api.post("/{name}/entries")
    def add(world_id: str, name: str, body: NewEntry, response: Response) -> Entry:
        entry, added = act(
            world_id,
            lambda: lists.add(
                world_id,
                name,
                body.key,
                body.title,
                status=body.status,
                score=body.score,
                fields=body.fields,
                file=body.file,
                by=OPERATOR,
            ),  # fmt: skip
        )
        response.status_code = 201 if added else 200
        return entry

    @api.post("/{name}/status")
    def set_status(world_id: str, name: str, body: StatusChange) -> list[Entry]:
        return act(
            world_id,
            lambda: lists.set_status(
                world_id, name, body.keys, body.status, note=body.note, by=OPERATOR
            ),
        )

    @api.get("/{name}/entries/{key:path}")
    def get(world_id: str, name: str, key: str) -> dict[str, Any]:
        return act(world_id, lambda: entry_view(lists, world_id, name, key))

    @api.patch("/{name}/entries/{key:path}")
    def set_fields(world_id: str, name: str, key: str, body: FieldsChange) -> Entry:
        return act(
            world_id,
            lambda: lists.set_fields(
                world_id, name, key, body.fields, score=body.score, note=body.note, by=OPERATOR
            ),
        )

    return api


class AgentLists:
    """The swarm's `Lists`: each LIST_TOOLS call of an agent, on the world it works in."""

    def __init__(self, lists: WorldLists) -> None:
        self.lists = lists

    def call(self, world_id: str, run_id: str, agent: str, tool: str, args: dict[str, Any]) -> str:
        lists, who = self.lists, {"by": agent, "run_id": run_id}
        name = str(args.get("list") or "")
        try:
            if tool == "read_list":
                if not name:
                    return json.dumps([asdict(w) for w in lists.lists(world_id)])
                entries = lists.read(
                    world_id,
                    name,
                    status=[str(s) for s in args.get("status") or []] or None,
                    where=dict(args.get("fields") or {}),
                    order=str(args.get("order") or "-score"),
                    limit=int(args["limit"]) if args.get("limit") is not None else None,
                )
                declared = asdict(lists.get_list(world_id, name))
                return json.dumps({"list": declared, "entries": [asdict(e) for e in entries]})
            if tool == "add_entry":
                entry, added = lists.add(
                    world_id, name, str(args.get("key", "")), str(args.get("title", "")),
                    status=args.get("status"), score=args.get("score"),
                    fields=dict(args.get("fields") or {}), file=args.get("file"), **who,
                )  # fmt: skip
                return json.dumps({**asdict(entry), "added": added})
            if tool == "set_entry_status":
                keys = [str(k) for k in args.get("keys") or []]
                status, note = str(args.get("status", "")), str(args.get("note") or "")
                moved = lists.set_status(world_id, name, keys, status, note=note, **who)
                return json.dumps([asdict(e) for e in moved])
            if tool == "set_entry_fields":
                entry = lists.set_fields(
                    world_id, name, str(args.get("key", "")), dict(args.get("fields") or {}),
                    score=args.get("score"), note=str(args.get("note") or ""), **who,
                )  # fmt: skip
                return json.dumps(asdict(entry))
        except KeyError as exc:
            raise ToolError(str(exc).strip("'\"")) from exc
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        raise ToolError(f"no list tool {tool}")
