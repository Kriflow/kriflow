"""SQLite adapter of the Connectors Storage port. One short connection per call; never migrates."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from swarmify_connectors.model import Call, Connector
from swarmify_kernel import Event
from swarmify_kernel.events import append
from swarmify_kernel.sqlite import connect

MIGRATIONS = Path(__file__).parent / "migrations"
_JSON = ("request", "response", "template")


def _connector(row: sqlite3.Row) -> Connector:
    return Connector(
        row["id"],
        row["world_id"],
        row["name"],
        row["kind"],
        json.loads(row["command"]),
        row["url"],
        json.loads(row["credentials"]),
        json.loads(row["stage_types"]),
        created_at=row["created_at"],
        types=json.loads(row["types"]),
        input_types=json.loads(row["input_types"]),
        world_types=row["world_types"],
    )


def _call(row: sqlite3.Row) -> Call:
    fields = dict(row)
    for key in _JSON:
        fields[key] = json.loads(fields[key]) if fields[key] is not None else None
    return Call(**fields)


class SqliteStorage:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _conn(self) -> closing[sqlite3.Connection]:
        return closing(connect(self.path))

    def world_connectors(self) -> list[Connector]:
        with self._conn() as conn:
            return [
                _connector(r)
                for r in conn.execute("SELECT * FROM connectors ORDER BY created_at, id")
            ]

    def delete_connector(self, connector_id: str) -> bool:
        with self._conn() as conn:
            return conn.execute("DELETE FROM connectors WHERE id = ?", (connector_id,)).rowcount > 0

    def get_call(self, idempotency_key: str) -> Call | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM connector_calls WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            return _call(row) if row else None

    def save_call(self, call: Call) -> Call:
        fields = asdict(call)
        for key in _JSON:
            fields[key] = json.dumps(fields[key]) if fields[key] is not None else None
        names = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"INSERT OR REPLACE INTO connector_calls ({names}) VALUES ({marks})",
                tuple(fields.values()),
            )
            event = Event.new(
                "connector_call.updated",
                world_id=call.world_id,
                run_id=call.run_id,
                payload=asdict(call),
            )
            append(conn, event)
            conn.execute("COMMIT")
        return call

    def enabled_packages(self, world_id: str) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT package, version FROM world_packages WHERE world_id = ? ORDER BY package",
                (world_id,),
            )
            return {r["package"]: r["version"] for r in rows}

    def enable_package(self, world_id: str, package: str, version: str, at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO world_packages (world_id, package, version, enabled_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (world_id, package)"
                " DO UPDATE SET version = excluded.version, enabled_at = excluded.enabled_at",
                (world_id, package, version, at),
            )

    def disable_package(self, world_id: str, package: str) -> bool:
        with self._conn() as conn:
            return (
                conn.execute(
                    "DELETE FROM world_packages WHERE world_id = ? AND package = ?",
                    (world_id, package),
                ).rowcount
                > 0
            )

    def package_pinned_by(self, package: str, version: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT world_id FROM world_packages WHERE package = ? AND version = ?"
                " ORDER BY world_id",
                (package, version),
            )
            return [r["world_id"] for r in rows]

    def offered_defaults(self, world_id: str) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT package FROM world_default_packages WHERE world_id = ?", (world_id,)
            )
            return {r["package"] for r in rows}

    def offer_default(self, world_id: str, package: str, at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO world_default_packages (world_id, package, offered_at)"
                " VALUES (?, ?, ?)",
                (world_id, package, at),
            )

    def delete_world(self, world_id: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM connectors WHERE world_id = ?", (world_id,))
            conn.execute("DELETE FROM world_packages WHERE world_id = ?", (world_id,))
            conn.execute("DELETE FROM world_default_packages WHERE world_id = ?", (world_id,))
            conn.execute("DELETE FROM connector_calls WHERE world_id = ?", (world_id,))
            conn.execute("DELETE FROM world_logins WHERE world_id = ?", (world_id,))
            conn.execute("COMMIT")

    # World Logins (logins.py): their state and events, never a secret

    def logins(self, world_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM world_logins WHERE world_id = ? ORDER BY name", (world_id,)
            )
            return [{**dict(r), "domains": json.loads(r["domains"])} for r in rows]

    def add_login(self, row: dict[str, Any], event: Event) -> bool:
        """Insert the login and its event; False when its name is taken in the world."""
        fields = {**row, "domains": json.dumps(row["domains"])}
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"INSERT INTO world_logins ({', '.join(fields)})"
                    f" VALUES ({', '.join('?' for _ in fields)})",
                    tuple(fields.values()),
                )
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                return False
            append(conn, event)
            conn.execute("COMMIT")
        return True

    def update_login(self, world_id: str, name: str, fields: dict[str, Any], event: Event) -> bool:
        values = {k: json.dumps(v) if k == "domains" else v for k, v in fields.items()}
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            found = conn.execute(
                f"UPDATE world_logins SET {', '.join(f'{k} = ?' for k in values)}"
                " WHERE world_id = ? AND name = ?",
                (*values.values(), world_id, name),
            ).rowcount
            if found:
                append(conn, event)
            conn.execute("COMMIT")
        return found > 0

    def delete_login(self, world_id: str, name: str, event: Event) -> bool:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            found = conn.execute(
                "DELETE FROM world_logins WHERE world_id = ? AND name = ?", (world_id, name)
            ).rowcount
            if found:
                append(conn, event)
            conn.execute("COMMIT")
        return found > 0

    def append_event(self, event: Event) -> None:
        with self._conn() as conn:
            append(conn, event)

    def login_uses(self, world_id: str, name: str | None, limit: int) -> list[Event]:
        """The newest `login.used` events of the world, of one login when `name` is given."""
        where = " AND json_extract(payload, '$.login') = ?" if name else ""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE world_id = ? AND type = 'login.used'"
                f"{where} ORDER BY seq DESC LIMIT ?",
                (world_id, *([name] if name else []), limit),
            ).fetchall()
        return [
            Event(r["id"], r["world_id"], r["type"], json.loads(r["payload"]), r["ts"],
                  r["run_id"], r["seq"])
            for r in rows
        ]  # fmt: skip
