import { type ReactNode, useState } from "react";

import type { LogEntry } from "../api/events";
import { who } from "../api/tasks";
import { usePhone } from "../usePhone";
import { describe, path, payload, short } from "./AgentLog";
import { MessageLine } from "./Message";
import { faceOf } from "./RunHero";

const lines = (v: unknown) => (typeof v === "string" && v ? v.replace(/\n$/, "").split("\n") : []);

/** The lines one replacement changed: what was removed and what was added, without the lines
 * both share at the start and end. */
function changed(before: string[], after: string[]): { del: string[]; add: string[] } {
  let head = 0;
  while (head < before.length && head < after.length && before[head] === after[head]) head++;
  let tail = 0;
  while (tail < before.length - head && tail < after.length - head && before.at(-1 - tail) === after.at(-1 - tail)) tail++;
  return { del: before.slice(head, before.length - tail), add: after.slice(head, after.length - tail) };
}

/** The lines a file-writing call changed, read from its own input; null for any other call. */
export function diffOf(e: LogEntry): { del: string[]; add: string[] } | null {
  const input = (e.input ?? {}) as Record<string, unknown>;
  switch (short(e.tool)) {
    case "Edit":
      return changed(lines(input.old_string), lines(input.new_string));
    case "MultiEdit": {
      const edits = (Array.isArray(input.edits) ? input.edits : []) as Record<string, unknown>[];
      const each = edits.map((x) => changed(lines(x.old_string), lines(x.new_string)));
      return { del: each.flatMap((x) => x.del), add: each.flatMap((x) => x.add) };
    }
    case "Write":
    case "write_artifact":
      return { del: [], add: lines(input.content) };
    case "write_note":
      return { del: [], add: lines(input.body) };
    default:
      return null;
  }
}

const ARGS = ["file_path", "path", "url", "query", "pattern", "command", "name", "title", "question", "task_id"];

/** The one argument that says what a call was about: its file, address, query or command. */
export function argOf(e: LogEntry): string {
  const input = (e.input ?? {}) as Record<string, unknown>;
  const key = ARGS.find((k) => typeof input[k] === "string" && input[k]);
  if (!key) return "";
  const v = String(input[key]);
  if (key === "file_path" || key === "path") return path(v);
  return key === "url" ? v.replace(/^https?:\/\//, "") : v;
}

/** "1.2 s", "14 s": how long a call took from its log line to its result's. */
function took(call: LogEntry, out: LogEntry): string {
  const s = Math.max(0, (Date.parse(out.ts) - Date.parse(call.ts)) / 1000);
  return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
}

const size = (n: number) => (n < 1000 ? `${n} chars` : `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K chars`);

const MOST_LINES = 40;

function Diff({ del, add }: { del: string[]; add: string[] }) {
  const shown = [...del.map((l) => ["del", l] as const), ...add.map((l) => ["add", l] as const)];
  return (
    <div className="diff" data-testid="diff">
      {shown.slice(0, MOST_LINES).map(([k, l], i) => (
        <div key={i} className={k}>
          {k === "del" ? "- " : "+ "}
          {l}
        </div>
      ))}
      {shown.length > MOST_LINES && <div className="faint">{shown.length - MOST_LINES} more lines</div>}
    </div>
  );
}

/** One tool call as a collapsed row: the tool, what it was about, and how long it took or how
 * much it changed. Open, it shows the input (or the diff, for an edit) and the result. */
function ToolRow({ call, out }: { call: LogEntry; out?: LogEntry }) {
  const diff = diffOf(call);
  const chars = Number((out?.input as { chars?: number } | null)?.chars ?? 0);
  const failed = call.kind === "denied" || !!(out?.input as { error?: boolean } | null)?.error;
  const meta =
    call.kind === "denied"
      ? "refused"
      : diff
        ? [diff.del.length && `-${diff.del.length}`, diff.add.length && `+${diff.add.length}`].filter(Boolean).join(" ") + " lines"
        : out
          ? took(call, out)
          : "running";
  const input = JSON.stringify(call.input ?? {}, null, 2);
  return (
    <details className={failed ? "toolrow failed" : "toolrow"} data-testid="tool-row">
      <summary>
        <code>{short(call.tool)}</code>
        <span className="arg">{argOf(call)}</span>
        <span className="ms">{meta}</span>
      </summary>
      {diff ? <Diff {...diff} /> : input !== "{}" && <pre className="tool-in">{input.length > 1500 ? `${input.slice(0, 1500)}…` : input}</pre>}
      {call.kind === "denied" && <pre>{call.detail}</pre>}
      {out && (
        <pre className="tool-out">
          {out.detail || "(no output)"}
          {chars > out.detail.length && `… (${size(chars)})`}
        </pre>
      )}
    </details>
  );
}

/** "0:41": time since the log's first line. */
function at(e: LogEntry, start: number): string {
  const s = Math.max(0, Math.round((Date.parse(e.ts) - start) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A stage's log as a transcript, like Claude Code's: each agent's words beside its face, tool
 * calls as rows that open, edits as diffs, filterable to one agent. */
export function Transcript({ log }: { log: LogEntry[] }) {
  const [only, setOnly] = useState("");
  const agents = [...new Set(log.map((e) => e.agent))];
  const outputs = new Map(log.filter((e) => e.kind === "output" && e.ref).map((e) => [e.ref!, e]));
  const start = log.length ? Date.parse(log[0]!.ts) : 0;
  // A Message shows once, at its first line (the filtered agent's), with every agent's lines
  // for its receipt.
  const messages = new Map<string, LogEntry[]>();
  for (const e of log) if (e.kind === "message") messages.set(e.ref ?? "", [...(messages.get(e.ref ?? "") ?? []), e]);
  const firsts = new Set([...messages.values()].map((lines) => lines.find((m) => !only || m.agent === only)?.seq));
  const shown = log.filter((e) => e.kind !== "output" && (e.kind === "message" ? firsts.has(e.seq) : !only || e.agent === only));
  return (
    <section className="transcript" aria-label="Transcript">
      {agents.length > 1 && (
        <label className="tx-who">
          Show
          <select value={only} onChange={(ev) => setOnly(ev.target.value)} data-testid="transcript-agent">
            <option value="">Every agent</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {who(a)}
              </option>
            ))}
          </select>
        </label>
      )}
      {shown.length === 0 ? (
        <p className="faint">Nothing yet. What each agent says and does appears here as it happens.</p>
      ) : (
        <ol className="tx" data-testid="transcript">
          {shown.map((e) => {
            let line: ReactNode;
            if (e.kind === "message") {
              line = <MessageLine lines={messages.get(e.ref ?? "")!} />;
            } else if (e.kind === "text") {
              const f = faceOf(e.agent);
              line = (
                <div className="say">
                  <span className="face" style={{ background: f.colour }} aria-hidden="true">
                    {f.label}
                  </span>
                  <div>
                    <div className="who">
                      <b>{who(e.agent)}</b>
                      <time dateTime={e.ts}>{at(e, start)}</time>
                    </div>
                    <div className="txt">{e.detail}</div>
                  </div>
                </div>
              );
            } else if (e.kind === "tool" || (e.kind === "denied" && e.tool)) {
              line = <ToolRow call={e} out={e.ref ? outputs.get(e.ref) : undefined} />;
            } else {
              const text = e.kind === "result" ? payload(e.detail).text || e.detail : describe(e);
              line = (
                <p className={e.kind === "error" ? "tx-note warn" : "tx-note"}>
                  {who(e.agent)}: {text}
                </p>
              );
            }
            return <li key={e.seq}>{line}</li>;
          })}
        </ol>
      )}
    </section>
  );
}

/** An agent stage's Activity: the Summary (agents and the live log in words) or the Transcript.
 * Summary first on a phone, Transcript on a desktop. Both stay mounted, so a live log keeps its
 * place when you look at the other. */
export function ActivityViews({ summary, log }: { summary: ReactNode; log: LogEntry[] }) {
  const phone = usePhone();
  const [picked, setPicked] = useState<"summary" | "transcript" | null>(null);
  const view = picked ?? (phone ? "summary" : "transcript");
  return (
    <>
      <div className="seg" role="group" aria-label="Activity view">
        {(["summary", "transcript"] as const).map((v) => (
          <button key={v} type="button" aria-pressed={view === v} onClick={() => setPicked(v)}>
            {v === "summary" ? "Summary" : "Transcript"}
          </button>
        ))}
      </div>
      <div className="activity-view" hidden={view !== "summary"}>
        {summary}
      </div>
      <div className="activity-view" hidden={view !== "transcript"}>
        <Transcript log={log} />
      </div>
    </>
  );
}
