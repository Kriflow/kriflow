"""World Lists (issue 539): named, world-wide lists of Entries that any workflow of a world adds
to, reads and moves through the statuses the world declares.

Core knows no meaning beyond the rules here: a key is unique per list (adding it again returns
the existing Entry unchanged), a status must be declared, fields stay small. Every add and change
is an event in the world stream, which is also the Entry's history. One class serves the HTTP
API, MCP and the `lists` Agent Tool alike.
"""

import builtins
import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from swarmify_kernel import Event
from swarmify_kernel.events import append
from swarmify_kernel.sqlite import connect

MAX_FIELDS = 4000
"""The most an Entry's fields may take as JSON, in characters: small facts, not content (the
content is the Entry's file)."""
MAX_TITLE, MAX_KEY = 300, 500
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
"""A list's or a status's name."""
FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
"""A field name a filter or an order may use."""
COST = "cost_usd"
"""The field holding what the runs that worked an Entry spent, added as each one ends."""
ORDERS = ("score", "time")
"""What an order may name besides a numeric field; a leading `-` makes it descending."""


class Held(ValueError):
    """Refused: a run that has not ended holds the Entry."""


@dataclass(frozen=True)
class WorldList:
    """A list as the world declared it. `statuses` keeps its order: the first is where an added
    Entry starts unless the adder names another."""

    world_id: str
    name: str
    statuses: list[str]
    done: list[str]
    rejected: list[str]
    review: list[str]
    """Statuses whose Entries wait for a person: while any do, Needs you says so."""
    created_at: str
    updated_at: str
    published: str | None = None
    """Where an Entry goes when the run holding it publishes; None leaves it where it is."""


@dataclass(frozen=True)
class Entry:
    list: str
    key: str
    title: str
    status: str
    score: float | None
    fields: dict[str, Any]
    file: str | None
    """The Entry's content, a path in the world folder."""
    claimed_by: str | None
    """The run holding it now, None when free."""
    runs: builtins.list[str]  # `list` is the field above
    """Every run that held it, oldest first."""
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _names(kind: str, values: list[str]) -> list[str]:
    for v in values:
        if not NAME.match(v):
            raise ValueError(f"{kind} {v!r}: use lowercase letters, digits, - and _ (up to 64)")
    if len(set(values)) != len(values):
        raise ValueError(f"{kind}s repeat a name")
    return values


def _field(name: str) -> str:
    if not FIELD.match(name):
        raise ValueError(f"field {name!r}: use letters, digits and _")
    return name


def _fields(fields: dict[str, Any]) -> str:
    for name in fields:
        _field(name)
    text = json.dumps(fields, sort_keys=True)
    if len(text) > MAX_FIELDS:
        raise ValueError(
            f"fields take {len(text)} characters as JSON, over {MAX_FIELDS}: keep them small"
            " and put the rest in the Entry's file"
        )
    return text


def _score(score: Any) -> float | None:
    """A score as a finite number, or None. Refuses text and bools, which SQLite would store
    as they are and rank above every number."""
    if score is None:
        return None
    if isinstance(score, bool):
        raise ValueError("a score is a number")
    try:
        value = float(score)
    except (TypeError, ValueError):
        raise ValueError(f"a score is a number, not {score!r}") from None
    if not math.isfinite(value):
        raise ValueError("a score is a finite number")
    return value


def _file(file: str | None) -> str | None:
    if not file:
        return None
    path = PurePosixPath(file)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"file {file!r} must be a path inside the world folder")
    return str(path)


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(
        row["list"],
        row["key"],
        row["title"],
        row["status"],
        row["score"],
        json.loads(row["fields"]),
        row["file"],
        row["claimed_by"],
        json.loads(row["runs"]),
        row["created_at"],
        row["updated_at"],
    )


def _list(row: sqlite3.Row) -> WorldList:
    return WorldList(
        row["world_id"],
        row["name"],
        json.loads(row["statuses"]),
        json.loads(row["done"]),
        json.loads(row["rejected"]),
        json.loads(row["review"]),
        row["created_at"],
        row["updated_at"],
        row["published"],
    )


def order_by(order: str) -> tuple[str, tuple[Any, ...]]:
    """The SQL ORDER BY of `order`: score, time (when added) or a numeric field, descending with
    a leading `-`. Entries without the value, or whose field is not a number, come last; ties go
    oldest first."""
    desc = order.startswith("-")
    name = order.removeprefix("-")
    if name == "score":
        column, args = "score", ()
    elif name == "time":
        column, args = "created_at", ()
    elif FIELD.match(name):
        column = (
            "CASE WHEN json_type(fields, ?) IN ('integer', 'real') THEN json_extract(fields, ?) END"
        )
        args = (f"$.{name}",) * 2
    else:
        raise ValueError(f"order {order!r}: name score, time or a field, with - for descending")
    way = "DESC" if desc else "ASC"
    return f"({column}) IS NULL, ({column}) {way}, created_at, key", args * 2


class WorldLists:
    """The World Lists of every world, on SQLite. Raises KeyError for an unknown list or Entry
    and ValueError for anything the rules refuse. `by` is who acted (a person, an agent or a
    run) and `run_id` the run an agent acted in, both recorded on the event."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _conn(self) -> closing[sqlite3.Connection]:
        return closing(connect(self.path))

    def declare(
        self,
        world_id: str,
        name: str,
        statuses: list[str],
        done: list[str] | None = None,
        rejected: list[str] | None = None,
        by: str = "operator",
        review: list[str] | None = None,
        published: str | None = None,
    ) -> WorldList:
        """Declare a list, or declare it again with new statuses. Refused when `done`,
        `rejected`, `review` or `published` names an undeclared status, or a status some Entry
        is in would go."""
        _names("list", [name])
        statuses, done, rejected = _names("status", statuses), done or [], rejected or []
        review = review or []
        if not statuses:
            raise ValueError("a list declares at least one status")
        if stray := [
            s for s in [*done, *rejected, *review, *filter(None, [published])] if s not in statuses
        ]:
            raise ValueError(
                "done, rejected, review and published must be declared statuses: "
                + ", ".join(stray)
            )
        now = _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            used = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT status FROM list_entries WHERE world_id = ? AND list = ?",
                    (world_id, name),
                )
            }
            if gone := sorted(used - set(statuses)):
                conn.execute("ROLLBACK")
                raise ValueError(f"Entries of {name} are in {', '.join(gone)}: keep those statuses")
            conn.execute(
                "INSERT INTO world_lists (world_id, name, statuses, done, rejected, review,"
                " created_at, updated_at, published) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (world_id, name) DO UPDATE SET statuses = excluded.statuses,"
                " done = excluded.done, rejected = excluded.rejected, review = excluded.review,"
                " published = excluded.published, updated_at = excluded.updated_at",
                (
                    world_id,
                    name,
                    *map(json.dumps, (statuses, done, rejected, review)),
                    now,
                    now,
                    published,
                ),
            )
            payload = {
                "list": name,
                "statuses": statuses,
                "done": done,
                "rejected": rejected,
                "review": review,
                "published": published,
            }
            append(
                conn, Event.new("list.declared", world_id=world_id, payload={**payload, "by": by})
            )
            conn.execute("COMMIT")
        return self.get_list(world_id, name)

    def lists(self, world_id: str) -> list[WorldList]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM world_lists WHERE world_id = ? ORDER BY name", (world_id,)
            ).fetchall()
        return [_list(r) for r in rows]

    def get_list(self, world_id: str, name: str) -> WorldList:
        with self._conn() as conn:
            return self._declared(conn, world_id, name)

    @staticmethod
    def _declared(conn: sqlite3.Connection, world_id: str, name: str) -> WorldList:
        row = conn.execute(
            "SELECT * FROM world_lists WHERE world_id = ? AND name = ?", (world_id, name)
        ).fetchone()
        if row is None:
            raise KeyError(f"no list {name!r} in this world")
        return _list(row)

    @staticmethod
    def _status(declared: WorldList, status: str) -> str:
        if status not in declared.statuses:
            raise ValueError(
                f"{status!r} is not a status of {declared.name}: {', '.join(declared.statuses)}"
            )
        return status

    def add(
        self,
        world_id: str,
        name: str,
        key: str,
        title: str,
        *,
        status: str | None = None,
        score: float | None = None,
        fields: dict[str, Any] | None = None,
        file: str | None = None,
        by: str = "operator",
        run_id: str | None = None,
    ) -> tuple[Entry, bool]:
        """Add an Entry, in `status` or else the list's first. A key already in the list returns
        that Entry unchanged and False: re-running a stage is safe."""
        key, title = key.strip(), " ".join(title.split())
        if not key or len(key) > MAX_KEY:
            raise ValueError(f"a key is 1 to {MAX_KEY} characters")
        if not title or len(title) > MAX_TITLE:
            raise ValueError(f"a title is 1 to {MAX_TITLE} characters")
        text, file, score = _fields(fields or {}), _file(file), _score(score)
        now = _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                declared = self._declared(conn, world_id, name)
                status = self._status(declared, status or declared.statuses[0])
            except (KeyError, ValueError):
                conn.execute("ROLLBACK")
                raise
            if (found := self._row(conn, world_id, name, key)) is not None:
                conn.execute("ROLLBACK")
                return found, False
            conn.execute(
                "INSERT INTO list_entries (world_id, list, key, title, status, score, fields,"
                " file, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (world_id, name, key, title, status, score, text, file, now, now),
            )
            payload = {"list": name, "key": key, "title": title, "status": status, "by": by}
            append(
                conn, Event.new("entry.added", world_id=world_id, payload=payload, run_id=run_id)
            )
            entry = self._row(conn, world_id, name, key)
            conn.execute("COMMIT")
        assert entry is not None
        return entry, True

    @staticmethod
    def _row(conn: sqlite3.Connection, world_id: str, name: str, key: str) -> Entry | None:
        row = conn.execute(
            "SELECT * FROM list_entries WHERE world_id = ? AND list = ? AND key = ?",
            (world_id, name, key),
        ).fetchone()
        return _entry(row) if row else None

    def get(self, world_id: str, name: str, key: str) -> Entry:
        with self._conn() as conn:
            self._declared(conn, world_id, name)
            entry = self._row(conn, world_id, name, key)
        if entry is None:
            raise KeyError(f"no Entry {key!r} in {name}")
        return entry

    def read(
        self,
        world_id: str,
        name: str,
        *,
        status: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order: str = "-score",
        limit: int | None = None,
    ) -> list[Entry]:
        """The list's Entries in any of `status` (all when None) whose fields equal `where`, by
        `order` (best score first by default), at most `limit`."""
        with self._conn() as conn:
            return self._query(conn, world_id, name, status, where, order, limit)

    def _query(
        self,
        conn: sqlite3.Connection,
        world_id: str,
        name: str,
        status: list[str] | None,
        where: dict[str, Any] | None,
        order: str,
        limit: int | None,
        free_for: str | None = None,
    ) -> list[Entry]:
        """`read` on an open connection. With `free_for`, only the Entries no run holds or that
        run does, the ones it holds first."""
        sql, args = "world_id = ? AND list = ?", [world_id, name]
        declared = self._declared(conn, world_id, name)
        if status:
            for s in status:
                self._status(declared, s)
            sql += f" AND status IN ({', '.join('?' * len(status))})"
            args += status
        for field, value in (where or {}).items():
            _field(field)
            if value is not None and not isinstance(value, str | int | float):
                raise ValueError(f"field {field!r}: filter on a text, number or true/false")
            sql += " AND json_extract(fields, ?) = ?"
            args += [f"$.{field}", value]
        ordering, more = order_by(order)
        if free_for is not None:
            sql += " AND (claimed_by IS NULL OR claimed_by = ?)"
            args.append(free_for)
            ordering, more = f"claimed_by IS NULL, {ordering}", more
        limited = " LIMIT ?" if limit is not None else ""
        rows = conn.execute(
            f"SELECT * FROM list_entries WHERE {sql} ORDER BY {ordering}{limited}",
            [*args, *more, *([max(0, limit)] if limit is not None else [])],
        ).fetchall()
        return [_entry(r) for r in rows]

    def claim(
        self,
        world_id: str,
        name: str,
        run_id: str,
        *,
        status: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order: str = "-score",
        take: int = 1,
    ) -> list[Entry]:
        """Hold for `run_id` the first `take` Entries of the query that no other run holds, in
        one transaction: two runs claiming at once never get the same Entry. Entries the run
        already holds there come first, so a claim made again takes the same ones."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                found = self._query(conn, world_id, name, status, where, order, take, run_id)
            except (KeyError, ValueError):
                conn.execute("ROLLBACK")
                raise
            for entry in found:
                if entry.claimed_by is None:
                    self._hold(conn, world_id, name, entry.key, run_id, None)
            out = [self._row(conn, world_id, name, e.key) for e in found]
            conn.execute("COMMIT")
        return [e for e in out if e is not None]

    def hand(
        self,
        world_id: str,
        name: str,
        key: str,
        holder: str,
        run_id: str,
        status: str | None = None,
    ) -> Entry:
        """Pass the Entry `holder` holds (or nobody does) to `run_id`, moving it to `status`
        when given. Refused when another run holds it."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if status is not None:
                    self._status(self._declared(conn, world_id, name), status)
                entry = self._row(conn, world_id, name, key)
                if entry is None:
                    raise KeyError(f"no Entry {key!r} in {name}")
                if entry.claimed_by not in (None, holder, run_id):
                    raise ValueError(f"Entry {key!r} of {name} is held by run {entry.claimed_by}")
            except (KeyError, ValueError):
                conn.execute("ROLLBACK")
                raise
            if entry.claimed_by != run_id:
                self._hold(conn, world_id, name, key, run_id, holder)
            if status is not None and entry.status != status:
                conn.execute(
                    "UPDATE list_entries SET status = ?"
                    " WHERE world_id = ? AND list = ? AND key = ?",
                    (status, world_id, name, key),
                )
                change = {"from": entry.status, "to": status}
                self._changed(conn, world_id, name, key, change, "", "run", run_id)
            handed = self._row(conn, world_id, name, key)
            conn.execute("COMMIT")
        assert handed is not None
        return handed

    def _hold(
        self,
        conn: sqlite3.Connection,
        world_id: str,
        name: str,
        key: str,
        run_id: str,
        holder: str | None,
    ) -> None:
        conn.execute(
            "UPDATE list_entries SET claimed_by = ?, runs = json_insert(runs, '$[#]', ?),"
            " updated_at = ? WHERE world_id = ? AND list = ? AND key = ?",
            (run_id, run_id, _now(), world_id, name, key),
        )
        note = f"from run {holder}" if holder else ""
        self._changed(conn, world_id, name, key, {"claimed_by": run_id}, note, "run", run_id)

    def held(self, world_id: str, run_id: str) -> list[Entry]:
        """The Entries `run_id` holds, in every list of the world."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM list_entries WHERE claimed_by = ? AND world_id = ?"
                " ORDER BY list, key",
                (run_id, world_id),
            ).fetchall()
        return [_entry(r) for r in rows]

    def move_held(
        self,
        world_id: str,
        run_id: str,
        status: str,
        *,
        by: str,
        note: str = "",
        check: bool = False,
    ) -> None:
        """Move every Entry `run_id` holds to `status`. Refused, with nothing moved, when one's
        list does not declare it; with `check` nothing moves either way."""
        held = self.held(world_id, run_id)
        for entry in held:
            self._status(self.get_list(world_id, entry.list), status)
        if check:
            return
        for entry in held:
            self.set_status(
                world_id, entry.list, [entry.key], status, note=note, by=by, run_id=run_id
            )

    def published(self, world_id: str, run_id: str, links: dict[str, str]) -> list[Entry]:
        """The run posted: each Entry it holds whose list declares a published status moves
        there, with the post's `links` (platform to URL) added to its `links` field."""
        out = []
        for entry in self.held(world_id, run_id):
            declared = self.get_list(world_id, entry.list)
            if declared.published is None:
                continue
            had = entry.fields.get("links")
            merged = {**(had if isinstance(had, dict) else {}), **links}
            self.set_fields(
                world_id, entry.list, entry.key, {"links": merged}, by="run", run_id=run_id
            )
            out += self.set_status(
                world_id, entry.list, [entry.key], declared.published, by="run", run_id=run_id
            )
        return out

    def set_status(
        self,
        world_id: str,
        name: str,
        keys: list[str],
        status: str,
        *,
        note: str = "",
        by: str = "operator",
        run_id: str | None = None,
    ) -> list[Entry]:
        """Move every Entry of `keys` to `status` at once, or none when one is unknown. An Entry
        already there is left as it is, with no event."""
        if not keys:
            raise ValueError("name at least one Entry")
        now, out = _now(), []
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._status(self._declared(conn, world_id, name), status)
                for key in dict.fromkeys(keys):
                    entry = self._row(conn, world_id, name, key)
                    if entry is None:
                        raise KeyError(f"no Entry {key!r} in {name}")
                    if entry.status != status:
                        conn.execute(
                            "UPDATE list_entries SET status = ?, updated_at = ?"
                            " WHERE world_id = ? AND list = ? AND key = ?",
                            (status, now, world_id, name, key),
                        )
                        payload = {"from": entry.status, "to": status}
                        self._changed(conn, world_id, name, key, payload, note, by, run_id)
                    out.append(self._row(conn, world_id, name, key))
            except (KeyError, ValueError):
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        return [e for e in out if e is not None]

    def set_fields(
        self,
        world_id: str,
        name: str,
        key: str,
        fields: dict[str, Any],
        *,
        score: float | None = None,
        note: str = "",
        by: str = "operator",
        run_id: str | None = None,
    ) -> Entry:
        """Merge `fields` into the Entry's (a None value removes that field) and set `score`
        when given."""
        score = _score(score)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._declared(conn, world_id, name)
                entry = self._row(conn, world_id, name, key)
                if entry is None:
                    raise KeyError(f"no Entry {key!r} in {name}")
                merged = {**entry.fields, **fields}
                merged = {k: v for k, v in merged.items() if v is not None}
                text = _fields(merged)
            except (KeyError, ValueError):
                conn.execute("ROLLBACK")
                raise
            conn.execute(
                "UPDATE list_entries SET fields = ?, score = COALESCE(?, score), updated_at = ?"
                " WHERE world_id = ? AND list = ? AND key = ?",
                (text, score, _now(), world_id, name, key),
            )
            payload: dict[str, Any] = {"fields": fields}
            if score is not None:
                payload["score"] = score
            self._changed(conn, world_id, name, key, payload, note, by, run_id)
            changed = self._row(conn, world_id, name, key)
            conn.execute("COMMIT")
        assert changed is not None
        return changed

    @staticmethod
    def _changed(
        conn: sqlite3.Connection,
        world_id: str,
        name: str,
        key: str,
        change: dict[str, Any],
        note: str,
        by: str,
        run_id: str | None,
    ) -> None:
        payload = {"list": name, "key": key, **change, "by": by, "note": note}
        append(conn, Event.new("entry.changed", world_id=world_id, payload=payload, run_id=run_id))

    def history(self, world_id: str, name: str, key: str) -> list[Event]:
        """The Entry's events, oldest first: added, then every change with who and why."""
        self.get(world_id, name, key)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE world_id = ? AND type IN ('entry.added',"
                " 'entry.changed') AND json_extract(payload, '$.list') = ?"
                " AND json_extract(payload, '$.key') = ? ORDER BY seq",
                (world_id, name, key),
            ).fetchall()
        return [
            Event(
                r["id"],
                r["world_id"],
                r["type"],
                json.loads(r["payload"]),
                r["ts"],
                r["run_id"],
                r["seq"],
            )  # fmt: skip
            for r in rows
        ]

    def waiting(self, world_id: str) -> list[tuple[WorldList, int]]:
        """Each list of the world with Entries in one of its `review` statuses, and how many."""
        out = []
        with self._conn() as conn:
            for row in conn.execute(
                "SELECT * FROM world_lists WHERE world_id = ? ORDER BY name", (world_id,)
            ).fetchall():
                declared = _list(row)
                if not declared.review:
                    continue
                (n,) = conn.execute(
                    "SELECT COUNT(*) FROM list_entries WHERE world_id = ? AND list = ?"
                    f" AND status IN ({', '.join('?' * len(declared.review))})",
                    (world_id, declared.name, *declared.review),
                ).fetchone()
                if n:
                    out.append((declared, n))
        return out

    @staticmethod
    def _unheld(conn: sqlite3.Connection, world_id: str, name: str, key: str | None) -> None:
        """Raise Held when a run that has not ended holds the Entry `key`, or any of the list's
        when None. A holder with no run row, or one that ended, holds nothing."""
        row = conn.execute(
            "SELECT e.key, r.id FROM list_entries e JOIN runs r ON r.id = e.claimed_by"
            " WHERE e.world_id = ? AND e.list = ? AND (? IS NULL OR e.key = ?)"
            " AND r.status NOT IN ('done', 'failed', 'aborted') LIMIT 1",
            (world_id, name, key, key),
        ).fetchone()
        if row is not None:
            raise Held(f"Entry {row[0]!r} of {name} is held by run {row[1]}, which has not ended")

    def delete_entry(self, world_id: str, name: str, key: str, *, by: str = "operator") -> None:
        """Delete an Entry. Refused (Held) while a run that has not ended holds it."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._declared(conn, world_id, name)
                entry = self._row(conn, world_id, name, key)
                if entry is None:
                    raise KeyError(f"no Entry {key!r} in {name}")
                self._unheld(conn, world_id, name, key)
            except (KeyError, ValueError):
                conn.execute("ROLLBACK")
                raise
            conn.execute(
                "DELETE FROM list_entries WHERE world_id = ? AND list = ? AND key = ?",
                (world_id, name, key),
            )
            payload = {"list": name, "key": key, "title": entry.title, "by": by}
            append(conn, Event.new("entry.deleted", world_id=world_id, payload=payload))
            conn.execute("COMMIT")

    def delete_list(self, world_id: str, name: str, *, by: str = "operator") -> int:
        """Delete a list and its Entries; how many Entries went. Refused (Held) while a run
        that has not ended holds one of them."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._declared(conn, world_id, name)
                self._unheld(conn, world_id, name, None)
            except (KeyError, ValueError):
                conn.execute("ROLLBACK")
                raise
            gone = conn.execute(
                "DELETE FROM list_entries WHERE world_id = ? AND list = ?", (world_id, name)
            ).rowcount
            conn.execute("DELETE FROM world_lists WHERE world_id = ? AND name = ?", (world_id, name))
            payload = {"list": name, "entries": gone, "by": by}
            append(conn, Event.new("list.deleted", world_id=world_id, payload=payload))
            conn.execute("COMMIT")
        return gone

    def delete_world(self, world_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM world_lists WHERE world_id = ?", (world_id,))


def release(conn: sqlite3.Connection, run_id: str, done: bool = False) -> None:
    """Free every Entry `run_id` holds, on the connection of the transaction that ends it. A
    fan-out child that ended `done` first moves its Entry to the query's `on_finish`, so no
    other run can claim it in the status it worked in (the parent's settling then finds it
    there already). Each Entry adds the run's spend (its family's, ADR 0013) to its `cost_usd`
    field: the cost of an Entry comes from the runs that worked it."""
    if done:
        _finish(conn, run_id)
    rows = conn.execute(
        "SELECT world_id, list, key, fields FROM list_entries WHERE claimed_by = ?", (run_id,)
    ).fetchall()
    spent = conn.execute("SELECT spent_usd FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.execute(
        "UPDATE list_entries SET claimed_by = NULL, updated_at = ? WHERE claimed_by = ?",
        (_now(), run_id),
    )
    for r in rows:
        change: dict[str, Any] = {"claimed_by": None}
        if spent is not None:
            fields = json.loads(r[3])
            had = fields.get(COST)
            had = had if isinstance(had, int | float) and not isinstance(had, bool) else 0
            fields[COST] = round(had + float(spent[0] or 0), 4)
            try:
                text = _fields(fields)
            except ValueError:  # fields at their cap: the run still ends
                text = None
            if text is not None:
                conn.execute(
                    "UPDATE list_entries SET fields = ?"
                    " WHERE world_id = ? AND list = ? AND key = ?",
                    (text, r[0], r[1], r[2]),
                )
                change["fields"] = {COST: fields[COST]}
        WorldLists._changed(conn, r[0], r[1], r[2], change, "the run ended", "run", run_id)


def _finish(conn: sqlite3.Connection, run_id: str) -> None:
    row = conn.execute(
        "SELECT r.world_id, f.run_id, json_extract(f.source, '$.entry'), s.config"
        " FROM fanout_items f JOIN runs r ON r.id = f.run_id"
        " JOIN run_stages s ON s.run_id = f.run_id AND s.idx = f.stage_index"
        " WHERE f.child_run_id = ? ORDER BY f.visit DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None or row[2] is None:
        return
    world_id, parent, key = row[0], row[1], row[2]
    query = json.loads(row[3]).get("from_list") or {}
    status, name = query.get("on_finish"), str(query.get("list") or "")
    declared = conn.execute(
        "SELECT statuses FROM world_lists WHERE world_id = ? AND name = ?", (world_id, name)
    ).fetchone()
    entry = conn.execute(
        "SELECT status, claimed_by FROM list_entries WHERE world_id = ? AND list = ? AND key = ?",
        (world_id, name, key),
    ).fetchone()
    if not status or declared is None or status not in json.loads(declared[0]):
        return  # an undeclared on_finish fails the Item when the parent settles it
    if entry is None or entry[1] != run_id or entry[0] == status:
        return
    conn.execute(
        "UPDATE list_entries SET status = ?, updated_at = ? WHERE world_id = ? AND list = ?"
        " AND key = ?",
        (status, _now(), world_id, name, key),
    )
    change = {"from": entry[0], "to": status}
    WorldLists._changed(conn, world_id, name, key, change, "", "run", parent)
