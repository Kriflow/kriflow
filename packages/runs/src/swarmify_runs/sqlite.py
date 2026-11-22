"""SQLite adapter of the Runs Storage port. One short connection per call; never migrates."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swarmify_kernel import Event, local_time_problem, new_id, next_local
from swarmify_kernel.events import append
from swarmify_kernel.sqlite import connect
from swarmify_runs.lists import release
from swarmify_runs.machine import MAX_SEND_BACKS, Illegal, RunState
from swarmify_runs.model import (
    RERUNS,
    AppliedChange,
    Decision,
    Gate,
    Item,
    ItemView,
    Run,
    RunLink,
    RunStageView,
    StageDef,
    items_gate,
)

MIGRATIONS = Path(__file__).parent / "migrations"
ENDED = ("done", "failed", "aborted")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _runs_at(config: dict[str, Any], status: str, started_at: str | None) -> str | None:
    """When a running stage with `not_before` runs: the next such time from its visit's start."""
    rule = config.get("not_before")
    if status != "running" or not started_at or local_time_problem(rule) is not None:
        return None
    return next_local(str(rule), datetime.fromisoformat(started_at)).isoformat()


class SqliteStorage:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _conn(self) -> closing[sqlite3.Connection]:
        return closing(connect(self.path))

    def add_run(self, run: Run, stages: list[StageDef], skipped: int = 0) -> Run:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_run(conn, run)
            for i, stage in enumerate(stages):
                conn.execute(
                    "INSERT INTO run_stages (id, run_id, idx, name, type, config, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("stage"),
                        run.id,
                        i,
                        stage.name,
                        stage.type,
                        json.dumps(stage.config),
                        "skipped" if i < skipped else "pending",
                    ),
                )
            saved = self._read(conn, run.id)
            assert saved is not None
            append(conn, _updated(saved))
            conn.execute("COMMIT")
            return saved

    def fork(self, run: Run, source: Run, index: int) -> Run:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._copy(conn, run, source, index)
            saved = self._read(conn, run.id)
            assert saved is not None
            append(conn, _updated(saved))
            conn.execute("COMMIT")
            return saved

    def fork_change(
        self, run: Run, source: Run, index: int, stages: list[StageDef], change: AppliedChange
    ) -> Run | None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "UPDATE runs SET revision = ?, updated_at = ? WHERE id = ? AND revision = ?",
                (change.revision, change.at, source.id, change.revision - 1),
            ).rowcount:
                conn.execute("ROLLBACK")
                return None
            self._copy(conn, run, source, index, stages, change.kind == "skip")
            _insert_change(conn, change, run.id)
            saved, changed = self._read(conn, run.id), self._read(conn, source.id)
            assert saved is not None and changed is not None
            event = Event.new(
                "run.changed",
                world_id=source.world_id,
                run_id=source.id,
                payload=asdict(changed.changes[-1]),
            )
            append(conn, event)
            append(conn, _updated(changed))
            append(conn, _updated(saved))
            conn.execute("COMMIT")
            return saved

    def _copy(
        self,
        conn: sqlite3.Connection,
        run: Run,
        source: Run,
        index: int,
        stages: list[StageDef] | None = None,
        skip: bool = False,
    ) -> None:
        """Insert `run` as a fork of `source` at `index`, with `stages` from `index` on when
        given (a Run Change), else the source's own; with `skip` the stage at `index` is
        skipped."""
        self._insert_run(conn, run, source.id, index)
        rows = conn.execute(
            "SELECT * FROM run_stages WHERE run_id = ? ORDER BY idx", (source.id,)
        ).fetchall()
        renamed = {}
        for s in rows if stages is None else rows[:index]:
            copied = s["idx"] < index
            renamed[s["id"]] = new_id("stage")
            conn.execute(
                "INSERT INTO run_stages (id, run_id, idx, name, type, config, status,"
                " started_at, ended_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    renamed[s["id"]],
                    run.id,
                    s["idx"],
                    s["name"],
                    s["type"],
                    s["config"],
                    s["status"] if copied else "pending",
                    s["started_at"] if copied else None,
                    s["ended_at"] if copied else None,
                    s["error"] if copied else None,
                ),
            )
        for i, stage in enumerate(stages or []):
            if i >= index:
                status = "skipped" if skip and i == index else "pending"
                conn.execute(
                    "INSERT INTO run_stages (id, run_id, idx, name, type, config, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id("stage"), run.id, i, stage.name, stage.type,
                     json.dumps(stage.config), status),
                )  # fmt: skip
        decisions = conn.execute(
            "SELECT * FROM gate_decisions WHERE run_id = ? AND stage_index < ?"
            " ORDER BY decided_at, id",
            (source.id, index),
        ).fetchall()
        for d in decisions:
            # The file was copied along with its stage folder, renamed to the fork's stage
            # id; a copied visit is numbered below zero so it never collides with a visit
            # the fork makes itself.
            old_stage = rows[d["stage_index"]]["id"]
            path = d["path"].replace(source.id, run.id).replace(old_stage, renamed[old_stage])
            artifact = d["artifact"] and d["artifact"].replace(source.id, run.id)
            cards = d["cards"] and d["cards"].replace(source.id, run.id)
            for old, new in renamed.items():  # the artifact may be from any earlier stage
                artifact = artifact and artifact.replace(old, new)
                cards = cards and cards.replace(old, new)
            conn.execute(
                "INSERT INTO gate_decisions (id, run_id, stage_index, stage_name, kind, choice,"
                " note, send_back_to, path, decided_at, visit, artifact, publish, answered_by,"
                " cards) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("dec"),
                    run.id,
                    d["stage_index"],
                    d["stage_name"],
                    d["kind"],
                    d["choice"],
                    d["note"],
                    d["send_back_to"],
                    path,
                    d["decided_at"],
                    -1 - d["visit"],
                    artifact,
                    d["publish"],
                    d["answered_by"],
                    cards,
                ),
            )

    @staticmethod
    def _insert_run(
        conn: sqlite3.Connection, run: Run, forked_from: str | None = None, index: int | None = None
    ) -> None:
        conn.execute(
            "INSERT INTO runs (id, world_id, workflow_id, workflow_version, name, status,"
            " created_at, updated_at, parent_run_id, forked_from_run_id, fork_stage_index,"
            " promoted_from, budget_usd, baseline, budget_warn_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.world_id,
                run.workflow_id or "",  # an ad hoc run has no workflow; the column is NOT NULL
                run.workflow_version or 0,
                run.name,
                run.status,
                run.created_at,
                run.updated_at,
                run.parent_run_id,
                forked_from,
                index,
                run.promoted_from,
                run.budget_usd,
                run.baseline,
                run.budget_warn_usd,
            ),
        )

    def save_state(self, run_id: str, state: RunState, error: str | None = None) -> Run | None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = _now()
            found = conn.execute(
                "UPDATE runs SET status = ?, send_backs = ?, over_budget = ?, updated_at = ?,"
                " revision = revision + 1 WHERE id = ?",
                (state.status, state.send_backs, state.over_budget, now, run_id),
            ).rowcount
            if not found:
                conn.execute("ROLLBACK")
                return None
            for i, status in enumerate(state.stages):
                # A stage sent back forgets its timestamps and its child run, so it reads as
                # fresh. A send-back usually reruns the stage at once, so it may never be saved
                # as pending: an ended stage turning active again is a fresh visit too, and its
                # old error goes (issue 527).
                conn.execute(
                    "UPDATE run_stages SET status = :s,"
                    " started_at = CASE WHEN :s = 'pending' THEN NULL"
                    "   WHEN :s IN ('running', 'waiting') AND ended_at IS NOT NULL THEN :now"
                    "   WHEN :s IN ('running', 'waiting') THEN coalesce(started_at, :now)"
                    "   ELSE started_at END,"
                    " ended_at = CASE WHEN :s IN ('pending', 'running', 'waiting') THEN NULL"
                    "   ELSE coalesce(ended_at, :now) END,"
                    " error = CASE WHEN :s = 'failed' THEN coalesce(:error, error)"
                    "   WHEN :s IN ('pending', 'running', 'waiting') THEN NULL ELSE error END,"
                    " child_run_id = CASE WHEN :s IN ('pending', 'running') THEN NULL"
                    "   ELSE child_run_id END"
                    " WHERE run_id = :run AND idx = :i AND status != :s",
                    {"s": status, "now": now, "error": error, "run": run_id, "i": i},
                )
            if state.status in ENDED:  # a run that ended holds no Entry (issue 541)
                release(conn, run_id, done=state.status == "done")
            saved = self._read(conn, run_id)
            assert saved is not None
            append(conn, _updated(saved))
            conn.execute("COMMIT")
            return saved

    def set_spent(self, run_id: str, usd: float) -> Run | None:
        return self._set(run_id, "spent_usd", usd)

    def set_budget(self, run_id: str, usd: float) -> Run | None:
        return self._set(run_id, "budget_usd", usd)

    def set_stop(self, run_id: str, stop: str | None) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE runs SET stop = ? WHERE id = ?", (stop, run_id))

    def stop_of(self, run_id: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT stop FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row["stop"] if row else None

    def _set(self, run_id: str, column: str, usd: float) -> Run | None:
        """Write one money column; a change appends `run.updated`. None for an unknown run."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                f"UPDATE runs SET {column} = ? WHERE id = ? AND {column} IS NOT ?",
                (usd, run_id, usd),
            ).rowcount
            saved = self._read(conn, run_id)
            if changed and saved is not None:
                append(conn, _updated(saved))
            conn.execute("COMMIT")
            return saved

    def mark_warned(self, run_id: str, budget_usd: float) -> bool:
        with self._conn() as conn:
            return bool(
                conn.execute(
                    "UPDATE runs SET budget_warned_usd = ? WHERE id = ?"
                    " AND budget_warned_usd IS NOT ?",
                    (budget_usd, run_id, budget_usd),
                ).rowcount
            )

    def latest_baseline(self, workflow_id: str | None) -> Run | None:
        if workflow_id is None:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE workflow_id = ? AND baseline = 1 AND status = 'done'"
                " ORDER BY created_at DESC, id DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
            return self._read(conn, row[0]) if row else None

    def last_run_seconds(self, run: Run) -> float | None:
        if run.workflow_id is None:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT min(s.started_at), max(s.ended_at) FROM run_stages s WHERE s.run_id = ("
                " SELECT id FROM runs WHERE workflow_id = ? AND baseline = 0 AND status = 'done'"
                " AND (created_at, id) < (?, ?) ORDER BY created_at DESC, id DESC LIMIT 1)",
                (run.workflow_id, run.created_at, run.id),
            ).fetchone()
        if not row or not row[0] or not row[1]:
            return None
        took = datetime.fromisoformat(row[1]) - datetime.fromisoformat(row[0])
        return max(0.0, took.total_seconds())

    def spent_in_month(self, world_id: str, month: str) -> float:
        with self._conn() as conn:
            (total,) = conn.execute(
                "SELECT coalesce(sum(spent_usd), 0) FROM runs WHERE world_id = ?"
                " AND parent_run_id IS NULL AND created_at LIKE ?",
                (world_id, f"{month}-%"),
            ).fetchone()
            return float(total)

    def add_decision(self, run_id: str, decision: Decision, visit: int, revision: int) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "UPDATE runs SET revision = revision + 1 WHERE id = ? AND revision = ?",
                (run_id, revision),
            ).rowcount:
                conn.execute("ROLLBACK")
                raise Illegal("the run moved since the gate was read: look again")
            try:
                conn.execute(
                    "INSERT INTO gate_decisions (id, run_id, stage_index, stage_name, kind, choice,"
                    " note, send_back_to, path, decided_at, visit, artifact, publish, answered_by,"
                    " cards) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        decision.id,
                        run_id,
                        decision.stage_index,
                        decision.stage_name,
                        decision.kind,
                        decision.choice,
                        decision.note,
                        decision.send_back_to,
                        decision.path,
                        decision.decided_at,
                        visit,
                        decision.artifact,
                        json.dumps(decision.publish) if decision.publish else None,
                        decision.by,
                        json.dumps(decision.cards()) if any(decision.cards().values()) else None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise Illegal("the gate was already decided") from exc
            conn.execute("COMMIT")

    def add_change(self, change: AppliedChange, revision: int) -> bool:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            moved = (
                not conn.execute(
                    "UPDATE runs SET revision = ?, updated_at = ? WHERE id = ? AND revision = ?",
                    (change.revision, change.at, change.run_id, revision),
                ).rowcount
                or change.kind == "stage_edit"
                and not conn.execute(
                    "UPDATE run_stages SET config = ? WHERE id = ? AND status = 'pending'",
                    (json.dumps(change.payload), change.stage_id),
                ).rowcount
            )
            if moved:
                conn.execute("ROLLBACK")
                return False
            _insert_change(conn, change)
            saved = self._read(conn, change.run_id)
            assert saved is not None
            append(
                conn,
                Event.new(
                    "run.changed", world_id=saved.world_id, run_id=saved.id, payload=asdict(change)
                ),
            )
            append(conn, _updated(saved))
            conn.execute("COMMIT")
            return True

    def set_child(self, run_id: str, index: int, child_run_id: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE run_stages SET child_run_id = ? WHERE run_id = ? AND idx = ?",
                (child_run_id, run_id, index),
            )
            saved = self._read(conn, run_id)
            if saved is not None:
                append(conn, _updated(saved))
            conn.execute("COMMIT")

    def add_items(
        self, run_id: str, index: int, visit: int, items: list[tuple[Item, ItemView]]
    ) -> None:
        """Record the Items of a fan-out stage's visit, in order, each as it starts out: pending,
        or as an earlier visit left it when a revisit keeps it (ADR 0031)."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for position, (item, view) in enumerate(items):
                source = {"file": item.file} if item.file else {"data": item.data}
                source |= {"entry": item.entry} if item.entry else {}
                conn.execute(
                    "INSERT OR IGNORE INTO fanout_items (run_id, stage_index, visit, item_key,"
                    " item_title, position, source, child_run_id, status, error, approved)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, index, visit, item.key, item.title, position,
                        json.dumps(source), view.child.id if view.child else None, view.status,
                        view.error, view.approved,
                    ),
                )  # fmt: skip
            saved = self._read(conn, run_id)
            if saved is not None:
                append(conn, _updated(saved))
            conn.execute("COMMIT")

    def items(
        self, run_id: str, index: int, visit: int | None = None
    ) -> list[tuple[Item, ItemView]]:
        """The Items of a fan-out stage's visit (its latest when `visit` is None), in order."""
        with self._conn() as conn:
            return _items(conn, run_id, index, visit)

    def set_item(
        self,
        run_id: str,
        index: int,
        visit: int,
        key: str,
        status: str,
        child_run_id: str | None,
        error: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE fanout_items SET status = ?, child_run_id = ?, error = ?, approved = 0"
                " WHERE run_id = ? AND stage_index = ? AND visit = ? AND item_key = ?",
                (status, child_run_id, error, run_id, index, visit, key),
            )
            saved = self._read(conn, run_id)
            if saved is not None:
                append(conn, _updated(saved))
            conn.execute("COMMIT")

    def approve_items(self, run_id: str, index: int, visit: int, keys: list[str]) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for key in keys:
                conn.execute(
                    "UPDATE fanout_items SET approved = 1"
                    " WHERE run_id = ? AND stage_index = ? AND visit = ? AND item_key = ?",
                    (run_id, index, visit, key),
                )
            saved = self._read(conn, run_id)
            if saved is not None:
                append(conn, _updated(saved))
            conn.execute("COMMIT")

    def item_of(self, child_run_id: str) -> tuple[str, int, int, str] | None:
        """(run, stage index, visit, item key) of the Item a child run works on, if any."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT run_id, stage_index, visit, item_key FROM fanout_items"
                " WHERE child_run_id = ? ORDER BY visit DESC LIMIT 1",
                (child_run_id,),
            ).fetchone()
            return tuple(row) if row else None  # type: ignore[return-value]

    def remove_change(self, change_id: str, reason: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT run_id FROM run_changes WHERE id = ?", (change_id,)
            ).fetchone()
            conn.execute("DELETE FROM run_changes WHERE id = ?", (change_id,))
            saved = row and self._read(conn, row["run_id"])
            if saved:
                append(
                    conn,
                    Event.new(
                        "run.changed",
                        world_id=saved.world_id,
                        run_id=saved.id,
                        payload={"id": change_id, "dropped": reason},
                    ),
                )
                append(conn, _updated(saved))
            conn.execute("COMMIT")

    def remove_decision(self, decision_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM gate_decisions WHERE id = ?", (decision_id,))

    def record(self, event: Event) -> None:
        with self._conn() as conn:
            append(conn, event)

    def get_run(self, run_id: str) -> Run | None:
        with self._conn() as conn:
            return self._read(conn, run_id)

    def open_gates(self) -> list[Gate]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT s.id, s.idx, s.name, s.type, s.config, s.started_at, s.run_id,"
                " r.world_id, r.name AS run_name, r.parent_run_id, r.over_budget"
                " FROM run_stages s JOIN runs r ON r.id = s.run_id"
                " WHERE s.status = 'waiting'"
                " AND (s.type IN ('gate', 'retro', 'workflow') OR r.over_budget = 1)"
                " ORDER BY s.started_at DESC, s.id DESC"
            ).fetchall()
            gates: list[Gate] = []
            families: set[str] = set()
            for g in rows:
                earlier = conn.execute(
                    "SELECT name FROM run_stages WHERE run_id = ? AND idx < ? ORDER BY idx",
                    (g["run_id"], g["idx"]),
                ).fetchall()
                config = json.loads(g["config"])
                limit = int(config.get("max_send_backs") or MAX_SEND_BACKS)
                kind = config.get("kind") or "approve"
                ask = config.get("ask") or ""
                if g["type"] == "workflow":  # a fan-out stage raised its items gate
                    views = [v for _, v in _items(conn, g["run_id"], g["idx"], None)]
                    kind, ask = items_gate(config, views)
                (sent,) = conn.execute(
                    "SELECT count(*) FROM gate_decisions WHERE run_id = ? AND stage_index = ?"
                    " AND send_back_to IS NOT NULL",
                    (g["run_id"], g["idx"]),
                ).fetchone()
                gate = Gate(
                    g["run_id"],
                    g["world_id"],
                    g["run_name"],
                    g["id"],
                    g["idx"],
                    g["name"],
                    kind,
                    ask,
                    g["started_at"] or "",
                    max(limit - sent, 0),
                    [e["name"] for e in earlier],
                    g["parent_run_id"],
                    _link(conn, g["parent_run_id"]),
                    max_send_backs=limit,
                    item=_item_title(conn, g["run_id"]),
                    picks=int(config.get("picks") or 1),
                )
                if g["over_budget"]:  # RunControl.gates adds the money from the top run
                    gate = replace(gate, kind="budget", send_backs_left=0, earlier_stages=[])
                    if g["parent_run_id"] is not None:
                        # A family waits at one budget gate, on its top run, which adding money
                        # to releases every member (ADR 0013, 0031).
                        top = _top(conn, g["run_id"])
                        if top in families:
                            continue
                        gate = _on_top(conn, gate, top)
                    families.add(gate.run_id)
                gates.append(gate)
            return gates

    def child_run_ids(self, run_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id FROM runs WHERE parent_run_id = ?", (run_id,))
            return [r[0] for r in rows]

    def stage_def(self, run_id: str, index: int) -> tuple[str, StageDef]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, type, config FROM run_stages WHERE run_id = ? AND idx = ?",
                (run_id, index),
            ).fetchone()
            if row is None:
                raise KeyError(f"{run_id} stage {index}")
            return row["id"], StageDef(row["name"], row["type"], json.loads(row["config"]))

    def list_runs(
        self,
        world_id: str,
        limit: int | None = None,
        before: str | None = None,
        archived: bool | None = None,
    ) -> list[Run]:
        sql, args = "SELECT id FROM runs WHERE world_id = ?", [world_id]
        if archived is not None:
            sql += f" AND archived_at IS {'NOT ' if archived else ''}NULL"
        if before:  # keyset on the sort key: runs started meanwhile never shift later pages
            sql += " AND (created_at, id) < (SELECT created_at, id FROM runs WHERE id = ?)"
            args.append(before)
        sql += " ORDER BY created_at DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            ids = conn.execute(sql, args).fetchall()
            return [r for (run_id,) in ids if (r := self._read(conn, run_id))]

    def set_archived(self, run_ids: list[str], archived_at: str | None) -> list[Run]:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = []
            for run_id in run_ids:
                conn.execute("UPDATE runs SET archived_at = ? WHERE id = ?", (archived_at, run_id))
                if (run := self._read(conn, run_id)) is not None:
                    append(conn, _updated(run))
                    saved.append(run)
            conn.execute("COMMIT")
            return saved

    def queued_run_ids(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at")
            return [r[0] for r in rows]

    def active_run_ids(self, world_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM runs WHERE world_id = ? AND status NOT IN (?, ?, ?)",
                (world_id, *ENDED),
            )
            return [r[0] for r in rows]

    def delete_world(self, world_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM runs WHERE world_id = ?", (world_id,))

    @staticmethod
    def _read(conn: sqlite3.Connection, run_id: str) -> Run | None:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        stages = conn.execute(
            "SELECT * FROM run_stages WHERE run_id = ? ORDER BY idx", (run_id,)
        ).fetchall()
        decisions = [
            Decision(
                d["id"],
                d["stage_index"],
                d["stage_name"],
                d["kind"],
                d["choice"],
                d["note"],
                d["send_back_to"],
                d["path"],
                d["decided_at"],
                d["artifact"],
                json.loads(d["publish"]) if d["publish"] else None,
                d["answered_by"],
                **(json.loads(d["cards"]) if d["cards"] else {}),
            )
            for d in conn.execute(
                "SELECT * FROM gate_decisions WHERE run_id = ? ORDER BY decided_at, id", (run_id,)
            ).fetchall()
        ]
        changes = [
            _change(conn, c)
            for c in conn.execute(
                "SELECT * FROM run_changes WHERE run_id = ? ORDER BY revision", (run_id,)
            )
        ]
        forked_by = next(
            (
                _change(conn, c)
                for c in conn.execute("SELECT * FROM run_changes WHERE fork_run_id = ?", (run_id,))
            ),
            None,
        )
        # A Fork a Rerun made carries its note to the stage it re-runs.
        made = [forked_by] if forked_by else []
        return Run(
            row["id"],
            row["world_id"],
            row["workflow_id"] or None,
            row["workflow_version"] or None,
            row["name"],
            row["status"],
            row["created_at"],
            row["updated_at"],
            [
                RunStageView(
                    s["id"],
                    s["idx"],
                    s["name"],
                    s["type"],
                    s["status"],
                    s["started_at"],
                    s["ended_at"],
                    s["error"],
                    notes(decisions, [*changes, *made], s["name"]),
                    _link(conn, s["child_run_id"]),
                    json.loads(s["config"]).get("calls") if s["type"] == "connector" else None,
                    json.loads(s["config"]).get("ask") if s["type"] == "gate" else None,
                    _inputs(json.loads(s["config"])) if s["type"] == "connector" else [],
                    json.loads(s["config"]),
                    items=[v for _, v in _items(conn, run_id, s["idx"], None)]
                    if s["type"] == "workflow"
                    else [],
                    runs_at=_runs_at(json.loads(s["config"]), s["status"], s["started_at"]),
                )
                for s in stages
            ],
            row["send_backs"],
            row["parent_run_id"],
            decisions,
            _link(conn, row["parent_run_id"]),
            _link(conn, row["forked_from_run_id"]),
            row["fork_stage_index"],
            # ponytail: one query per run read, so list_runs is N+1 (like _link); join if it shows
            [
                RunLink(f["id"], f["world_id"], f["name"])
                for f in conn.execute(
                    "SELECT id, world_id, name FROM runs WHERE forked_from_run_id = ?"
                    " ORDER BY created_at, id",
                    (run_id,),
                )
            ],
            row["promoted_from"],
            row["budget_usd"],
            row["spent_usd"],
            bool(row["over_budget"]),
            bool(row["baseline"]),
            row["revision"],
            changes,
            forked_by,
            row["budget_warn_usd"],
            archived_at=row["archived_at"],
        )


def _insert_change(
    conn: sqlite3.Connection, change: AppliedChange, fork_run_id: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO run_changes (id, run_id, kind, stage_id, stage_index, stage_name, payload,"
        " before_json, applied_by, applied_at, revision, fork_run_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            change.id,
            change.run_id,
            change.kind,
            change.stage_id,
            change.stage_index,
            change.stage_name,
            json.dumps(change.payload),
            json.dumps(change.before),
            change.by,
            change.at,
            change.revision,
            fork_run_id,
        ),
    )


def _change(conn: sqlite3.Connection, c: sqlite3.Row) -> AppliedChange:
    return AppliedChange(
        c["id"],
        c["run_id"],
        c["kind"],
        c["stage_id"],
        c["stage_index"],
        c["stage_name"],
        json.loads(c["payload"]),
        json.loads(c["before_json"]),
        c["applied_by"],
        c["applied_at"],
        c["revision"],
        _link(conn, c["fork_run_id"]),
    )


def _items(
    conn: sqlite3.Connection, run_id: str, index: int, visit: int | None
) -> list[tuple[Item, ItemView]]:
    if visit is None:
        (visit,) = conn.execute(
            "SELECT max(visit) FROM fanout_items WHERE run_id = ? AND stage_index = ?",
            (run_id, index),
        ).fetchone()
    rows = conn.execute(
        "SELECT * FROM fanout_items WHERE run_id = ? AND stage_index = ? AND visit = ?"
        " ORDER BY position",
        (run_id, index, visit),
    ).fetchall()
    out = []
    for r in rows:
        source = json.loads(r["source"])
        item = Item(
            r["item_key"], r["item_title"], source.get("file"), source.get("data"),
            source.get("entry"),
        )  # fmt: skip
        view = ItemView(
            r["item_key"], r["item_title"], r["status"], _link(conn, r["child_run_id"]),
            r["error"], bool(r["approved"]),
        )  # fmt: skip
        out.append((item, view))
    return out


def _item_title(conn: sqlite3.Connection, child_run_id: str) -> str | None:
    row = conn.execute(
        "SELECT item_title FROM fanout_items WHERE child_run_id = ? LIMIT 1", (child_run_id,)
    ).fetchone()
    return row[0] if row else None


def _top(conn: sqlite3.Connection, run_id: str) -> str:
    while (
        row := conn.execute("SELECT parent_run_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    ) and row[0]:
        run_id = row[0]
    return run_id


def _on_top(conn: sqlite3.Connection, gate: Gate, top: str) -> Gate:
    """A child's budget gate shown as its family's one gate, on the top run's active stage."""
    run = conn.execute("SELECT name, world_id FROM runs WHERE id = ?", (top,)).fetchone()
    stage = conn.execute(
        "SELECT id, idx, name FROM run_stages WHERE run_id = ? AND status IN ('running', 'waiting')"
        " ORDER BY idx LIMIT 1",
        (top,),
    ).fetchone()
    if run is None or stage is None:
        return gate
    return replace(
        gate, run_id=top, world_id=run["world_id"], run_name=run["name"], stage_id=stage["id"],
        stage_index=stage["idx"], stage_name=stage["name"], parent_run_id=None, parent=None,
        item=None,
    )  # fmt: skip


def _link(conn: sqlite3.Connection, run_id: str | None) -> RunLink | None:
    if run_id is None:
        return None
    row = conn.execute("SELECT id, world_id, name FROM runs WHERE id = ?", (run_id,)).fetchone()
    return RunLink(row["id"], row["world_id"], row["name"]) if row else None


def _updated(run: Run) -> Event:
    """One event type carrying the whole run, so any skin renders it without a refetch."""
    return Event.new("run.updated", world_id=run.world_id, run_id=run.id, payload=asdict(run))


def _inputs(config: dict[str, Any]) -> list[str]:
    """A connector stage's input names: its `inputs`, or its one `input`."""
    return list(config.get("inputs") or []) or ([config["input"]] if config.get("input") else [])


def notes(decisions: list[Decision], changes: list[AppliedChange], stage_name: str) -> list[str]:
    """The notes of the send-backs, Reruns and Follow-ups addressed to the stage, oldest first."""
    sent = [(d.decided_at, d.note) for d in decisions if d.send_back_to == stage_name]
    sent += [
        (c.at, c.payload["note"])
        for c in changes
        if c.kind in RERUNS and c.stage_name == stage_name
    ]
    return [note for _, note in sorted(sent) if note]
