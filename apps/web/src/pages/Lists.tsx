import { Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";

import { api } from "../api/client";
import { useWorldEvents } from "../api/events";
import { took } from "../api/runs";
import type { components } from "../api/schema";
import { Empty } from "../Empty";
import { PullToRefresh } from "../PullToRefresh";
import { listsRoute } from "../router";
import { Sheet } from "../Sheet";
import { Loading, Skeleton } from "../Skeleton";
import { notify } from "../Toast";
import "../styles/worldlists.css";

type WorldList = components["schemas"]["WorldList"];
type Entry = components["schemas"]["Entry"];
type Change = { type: string; ts: string; by?: string; note?: string; from?: string; to?: string; status?: string; fields?: Record<string, unknown>; score?: number };

const ago = (ts: string) => `${took(Math.max(0, (Date.now() - Date.parse(ts)) / 1000))} ago`;
const entries = (n: number) => `${n} ${n === 1 ? "Entry" : "Entries"}`;
/** A field's value as typed in its box: text stays text, anything else is JSON. */
const shown = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));
/** What was typed back as a value: JSON when it reads as JSON (a number, true), else the text. */
const typed = (s: string): unknown => {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
};

/** One line of an Entry's history: what happened, who did it, and why. */
function said(c: Change): string {
  const what =
    c.type === "entry.added"
      ? `Added in ${c.status}`
      : c.to
        ? `${c.from} to ${c.to}`
        : `Changed ${[...Object.keys(c.fields ?? {}), ...(c.score !== undefined ? ["score"] : [])].join(", ") || "nothing"}`;
  return `${what}${c.by ? ` by ${c.by}` : ""}${c.note ? `: ${c.note}` : ""}`;
}

/** A world's World Lists, one at a time: status chips over cards, an Entry sheet with its file,
 * runs and history, and a batch move of the Entries ticked, each with one shared reason. */
export function Lists() {
  const { worldId } = listsRoute.useParams();
  const search = listsRoute.useSearch();
  const navigate = useNavigate({ from: listsRoute.fullPath });
  const [lists, setLists] = useState<WorldList[] | null>(null);
  const [items, setItems] = useState<Entry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [moving, setMoving] = useState<string[] | null>(null);
  const [seen, setSeen] = useState(0);

  // The list the address names, else the first that asks for review, else the first.
  const list = lists?.find((l) => l.name === search.list) ?? lists?.find((l) => l.review.length) ?? lists?.[0];
  const reload = (e: { list: string }) => e.list === list?.name && setSeen((n) => n + 1);
  const saw = useWorldEvents(worldId, { "entry.added": reload, "entry.changed": reload });

  useEffect(() => {
    api
      .GET("/api/worlds/{world_id}/lists", { params: { path: { world_id: worldId } } })
      .then(({ data }) => {
        if (!data) throw new Error("load failed");
        setLists(data);
      })
      .catch(() => setError("Could not load the lists. Is the API running?"));
  }, [worldId, saw]);

  useEffect(() => {
    if (!list) return;
    api
      .GET("/api/worlds/{world_id}/lists/{name}/entries", { params: { path: { world_id: worldId, name: list.name } } })
      .then(({ data, response }) => {
        if (!data) throw new Error("load failed");
        setItems(data);
        saw(response);
      })
      .catch(() => setError("Could not load the Entries."));
  }, [worldId, list?.name, seen, saw]);

  const all = items ?? [];
  const count = (s: string) => all.filter((e) => e.status === s).length;
  // A status the address names, else the first review status with Entries in it, else all.
  const status =
    search.status !== undefined && (search.status === "" || list?.statuses.includes(search.status))
      ? search.status
      : (list?.review.find((s) => count(s) > 0) ?? "");
  const cards = status ? all.filter((e) => e.status === status) : all;
  // The chip chosen on arrival stays put once its last Entry moves on.
  useEffect(() => {
    if (list && items && search.status === undefined) void navigate({ search: { list: list.name, status }, replace: true });
  }, [list, items, search.status, status, navigate]);
  const ticked = picked.filter((k) => cards.some((e) => e.key === k));
  const go = (s: { list?: string; status?: string }) => {
    setPicked([]);
    void navigate({ search: { list: s.list ?? list?.name, status: s.status }, replace: true });
  };

  return (
    <main className="lists-page">
      <PullToRefresh />
      <header className="page">
        <div>
          <h1>Lists</h1>
          <p className="sub">
            {list ? `${entries(all.length)} in ${list.name}.` : "Work every workflow of this world shares."}
          </p>
        </div>
        {lists && lists.length > 1 && (
          <label className="list-pick">
            <span className="visually-hidden">List</span>
            <select value={list?.name} onChange={(e) => go({ list: e.target.value, status: undefined })}>
              {lists.map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>
      {error && <p role="alert">{error}</p>}
      {lists?.length === 0 && (
        <Empty
          icon="steps"
          testId="no-lists"
          action={
            <Link to="/worlds/$worldId/workflows" params={{ worldId }} className="button">
              Open workflows
            </Link>
          }
        >
          A world declares its lists; its workflows then add Entries here for you to curate.
        </Empty>
      )}
      {list && items === null && !error && (
        <Loading className="entry-cards">
          <Skeleton h={72} />
          <Skeleton h={72} />
        </Loading>
      )}
      {list && items && (
        <>
          <nav className="tabs chip-row" aria-label="Statuses" data-testid="statuses">
            {["", ...list.statuses].map((s) => (
              <Link
                key={s || "all"}
                className="tab"
                to="/worlds/$worldId/lists"
                params={{ worldId }}
                search={{ list: list.name, status: s }}
                replace
                onClick={() => setPicked([])}
                aria-current={s === status ? "page" : undefined}
              >
                {s || "All"} <span className="num">{s ? count(s) : all.length}</span>
              </Link>
            ))}
          </nav>
          {cards.length === 0 ? (
            <p className="faint" data-testid="no-entries">
              No Entries {status ? `in ${status}` : "yet"}.
            </p>
          ) : (
            <ul className="entry-cards" aria-label="Entries">
              {cards.map((e) => (
                <li key={e.key} className="entry-card card" data-testid="entry">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={ticked.includes(e.key)}
                      onChange={(ev) => setPicked((p) => (ev.target.checked ? [...p, e.key] : p.filter((k) => k !== e.key)))}
                    />
                    <span className="visually-hidden">Select {e.title}</span>
                  </label>
                  <button type="button" className="entry-open" aria-haspopup="dialog" aria-expanded={open === e.key} onClick={() => setOpen(e.key)}>
                    <b>{e.title}</b>
                    <span className="entry-meta">
                      <span className="pill">{e.status}</span>
                      {e.score !== null && e.score !== undefined && <span>score {e.score}</span>}
                      {Object.entries(e.fields)
                        .slice(0, 3)
                        .map(([k, v]) => (
                          <span key={k}>
                            {k} {shown(v)}
                          </span>
                        ))}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {ticked.length > 0 && (
            <div className="action-bar batch-bar" data-testid="batch">
              <span className="batch-count">{ticked.length} selected</span>
              <button type="button" className="link" onClick={() => setPicked([])}>
                Clear
              </button>
              <button type="button" className="primary" aria-haspopup="dialog" aria-expanded={moving !== null} onClick={() => setMoving(ticked)}>
                Move {ticked.length}
              </button>
            </div>
          )}
          <EntrySheet worldId={worldId} list={list} entryKey={open} seen={seen} onClose={() => setOpen(null)} onMove={(k) => setMoving([k])} />
          <MoveSheet
            worldId={worldId}
            list={list}
            keys={moving}
            title={moving?.length === 1 ? (all.find((e) => e.key === moving[0])?.title ?? moving[0]!) : entries(moving?.length ?? 0)}
            onClose={() => setMoving(null)}
            onMoved={() => {
              setPicked([]);
              setSeen((n) => n + 1);
            }}
          />
        </>
      )}
    </main>
  );
}

/** Move one or several Entries to another status with one optional reason. A rejected status
 * is styled as the step back it is; moving out of one restores the Entry. */
function MoveSheet({
  worldId,
  list,
  keys,
  title,
  onClose,
  onMoved,
}: {
  worldId: string;
  list: WorldList;
  keys: string[] | null;
  title: string;
  onClose: () => void;
  onMoved: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const move = async (status: string) => {
    if (!keys) return;
    setBusy(true);
    const { data, error } = await api
      .POST("/api/worlds/{world_id}/lists/{name}/status", {
        params: { path: { world_id: worldId, name: list.name } },
        body: { keys, status, note: note.trim() },
      })
      .finally(() => setBusy(false));
    if (error || !data) {
      notify(`Could not move: ${(error as { detail?: string } | undefined)?.detail ?? "the API refused"}`, { tone: "error" });
      return;
    }
    notify(`Moved ${keys.length === 1 ? title : entries(keys.length)} to ${status}.`, { tone: "ok" });
    setNote("");
    onMoved();
    onClose();
  };
  return (
    <Sheet open={keys !== null} onClose={onClose} label={`Move ${title}`} className="move-sheet">
      <label className="move-note">
        Reason (optional)
        <textarea rows={2} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="move-to" role="group" aria-label="Move to">
        {list.statuses.map((s) => (
          <button
            key={s}
            type="button"
            className={list.rejected.includes(s) ? "sheet-row danger" : "sheet-row"}
            disabled={busy}
            onClick={() => void move(s)}
          >
            {s}
            <small>{list.rejected.includes(s) ? "rejected" : list.done.includes(s) ? "done" : list.review.includes(s) ? "waits for you" : ""}</small>
          </button>
        ))}
      </div>
    </Sheet>
  );
}

type Viewed = Entry & { history: Change[] };

/** An Entry in full: its fields and score to edit, its file, the runs that held it, its history. */
function EntrySheet({
  worldId,
  list,
  entryKey,
  seen,
  onClose,
  onMove,
}: {
  worldId: string;
  list: WorldList;
  entryKey: string | null;
  seen: number;
  onClose: () => void;
  onMove: (key: string) => void;
}) {
  const [entry, setEntry] = useState<Viewed | null>(null);
  const [runs, setRuns] = useState<Record<string, string>>({});
  const [score, setScore] = useState("");
  const [fields, setFields] = useState<[string, string][]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (entryKey === null) return;
    let live = true;
    api
      .GET("/api/worlds/{world_id}/lists/{name}/entries/{key}", { params: { path: { world_id: worldId, name: list.name, key: entryKey } } })
      .then(({ data }) => {
        if (!live || !data) return;
        const e = data as unknown as Viewed;
        setEntry(e);
        setScore(e.score === null || e.score === undefined ? "" : String(e.score));
        setFields(Object.entries(e.fields).map(([k, v]) => [k, shown(v)]));
        // The runs by name; a run gone since keeps its id.
        for (const id of e.runs)
          api
            .GET("/api/runs/{run_id}", { params: { path: { run_id: id } } })
            .then(({ data: run }) => live && run && setRuns((r) => ({ ...r, [id]: run.name })))
            .catch(() => undefined);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [worldId, list.name, entryKey, seen]);

  const save = async (ev: FormEvent) => {
    ev.preventDefault();
    if (!entry) return;
    const n = score.trim() === "" ? null : Number(score);
    if (n !== null && !Number.isFinite(n)) return notify("A score is a number.", { tone: "error" });
    setBusy(true);
    const { data, error } = await api
      .PATCH("/api/worlds/{world_id}/lists/{name}/entries/{key}", {
        params: { path: { world_id: worldId, name: list.name, key: entry.key } },
        body: { fields: Object.fromEntries(fields.map(([k, v]) => [k, typed(v)])), score: n, note: "" },
      })
      .finally(() => setBusy(false));
    if (error || !data) return notify("Could not save the Entry.", { tone: "error" });
    notify("Saved.", { tone: "ok" });
  };

  const e = entry?.key === entryKey ? entry : null;
  return (
    <Sheet open={entryKey !== null} onClose={onClose} label={e?.title ?? "Entry"} className="entry-sheet">
      {!e ? (
        <Loading>
          <Skeleton lines={3} />
        </Loading>
      ) : (
        <div className="entry-body" data-testid="entry-sheet">
          <p className="entry-status">
            <span className="pill">{e.status}</span>
            <span className="faint">
              {e.key} · added {ago(e.created_at)}
              {e.claimed_by ? " · held by a run" : ""}
            </span>
          </p>
          <div className="actions">
            <button type="button" className="primary" aria-haspopup="dialog" onClick={() => onMove(e.key)}>
              Move
            </button>
            {e.file && (
              <Link className="button" to="/worlds/$worldId/read" params={{ worldId }} search={{ path: e.file }}>
                Open file
              </Link>
            )}
          </div>
          <form className="entry-edit" onSubmit={(ev) => void save(ev)}>
            <label>
              Score
              <input type="number" step="any" inputMode="decimal" value={score} onChange={(ev) => setScore(ev.target.value)} />
            </label>
            {fields.map(([k, v], i) => (
              <label key={k}>
                {k}
                <input value={v} onChange={(ev) => setFields((f) => f.map((x, j) => (j === i ? [k, ev.target.value] : x)))} />
              </label>
            ))}
            <button type="submit" disabled={busy}>
              Save
            </button>
          </form>
          <h3>Runs</h3>
          {e.runs.length === 0 ? (
            <p className="faint">No run has held it yet.</p>
          ) : (
            <nav aria-label="Runs">
              {e.runs.map((id) => (
                <Link key={id} className="sheet-row" to="/worlds/$worldId/runs/$runId" params={{ worldId, runId: id }}>
                  {runs[id] ?? id}
                  <small>{id === e.claimed_by ? "holds it now" : ""}</small>
                </Link>
              ))}
            </nav>
          )}
          <h3>History</h3>
          <ol className="entry-history">
            {[...e.history].reverse().map((c, i) => (
              <li key={i}>
                <span>{said(c)}</span>
                <small className="faint">{ago(c.ts)}</small>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Sheet>
  );
}
