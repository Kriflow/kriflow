import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { ArtifactFile, LogEntry } from "../api/events";
import { clock, money, refusal, type Run } from "../api/runs";
import type { components } from "../api/schema";
import { who } from "../api/tasks";
import { Icon } from "../Icon";
import { usePhone } from "../usePhone";
import { hhmm } from "./ChangeCard";
import { RunRef } from "./Runs";

type Stage = Run["stages"][number];
type Proposal = components["schemas"]["ChangeProposal"];
type Applied = components["schemas"]["AppliedChange"];
export type Attempt = components["schemas"]["Attempt"];
type Cause = { kind?: string; note?: string; by?: string | null; at?: string };
export type Mode = "follow_up" | "rerun";

const names = new Intl.ListFormat("en-GB", { type: "conjunction" });
export const causeOf = (a: Attempt) => (a.cause ?? null) as Cause | null;

/** Seconds an Attempt took, or has taken so far. */
export function attemptSeconds(a: Attempt, now = Date.now()): number {
  return Math.max(0, ((a.ended_at ? Date.parse(a.ended_at) : now) - Date.parse(a.started_at)) / 1000);
}

/** An Attempt tab's second line: whose it was, then "running" or its time and spend. */
export function attemptLine(a: Attempt, running: boolean, now = Date.now()): string {
  const kind = causeOf(a)?.kind;
  const head = kind === "follow_up" || kind === "rerun" ? "your change" : kind === "send_back" ? "sent back" : "";
  const tail = running ? "running" : `${clock(attemptSeconds(a, now))}, ${money(a.spent_usd)}`;
  return head ? `${head}, ${tail}` : tail;
}

/** The log lines of Attempt `n`: from its start to the next one's. */
export function attemptLog(log: LogEntry[], attempts: Attempt[], n: number): LogEntry[] {
  const at = attempts.find((a) => a.number === n);
  if (!at || attempts.length < 2) return log;
  const next = attempts.find((a) => a.number === n + 1);
  const [from, to] = [Date.parse(at.started_at), next ? Date.parse(next.started_at) : Infinity];
  return log.filter((e) => {
    const t = Date.parse(e.ts);
    return t >= from && t < to;
  });
}

/** What a change to a finished stage sets going again, from its proposal: the later stages it
 * runs again, the gates that wait for you again, and a Fork when the run has ended. */
export function rerunWarning(run: Run, stage: Stage, p: Proposal): string {
  const again = Object.keys(p.before ?? {}).filter((n) => n !== stage.name);
  const gates = again.filter((n) => run.stages.find((s) => s.name === n)?.type === "gate");
  const out = [again.length ? `A change here reruns ${names.format(again)}.` : "Nothing after it runs again."];
  if (gates.length) out.push(`${names.format(gates)} ${gates.length === 1 ? "waits" : "wait"} for you again.`);
  if (!p.in_place && p.fork_from) out.push(`The run has ended, so this continues as a new run from ${p.fork_from}.`);
  return out.join(" ");
}

/** A swarm stage's Attempts, read again whenever the stage or the run moves. */
export function useAttempts(run: Run, stage: Stage): Attempt[] {
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const swarm = stage.type === "swarm";
  useEffect(() => {
    if (!swarm) return setAttempts([]);
    let stale = false;
    void api.GET("/api/runs/{run_id}/stages/{stage_id}/attempts", { params: { path: { run_id: run.id, stage_id: stage.id } } }).then(({ data }) => {
      if (!stale && data) setAttempts(data);
    });
    return () => {
      stale = true;
    };
  }, [swarm, run.id, run.revision, stage.id, stage.status, stage.started_at]);
  return attempts;
}

/** The files an earlier Attempt left, kept when the next one started; null for the latest. */
export function useAttemptFiles(run: Run, stage: Stage, n: number | null): ArtifactFile[] | null {
  const [files, setFiles] = useState<ArtifactFile[] | null>(null);
  useEffect(() => {
    if (n === null) return setFiles(null);
    let stale = false;
    void api
      .GET("/api/runs/{run_id}/stages/{stage_id}/artifacts", { params: { path: { run_id: run.id, stage_id: stage.id }, query: { attempt: n } } })
      .then(({ data }) => !stale && setFiles(data ?? []));
    return () => {
      stale = true;
    };
  }, [run.id, stage.id, n]);
  return files;
}

/** Attempts as tabs: each one's number, then whose it was with its time and spend. */
export function AttemptTabs({ attempts, on, onPick, running }: { attempts: Attempt[]; on: number; onPick: (n: number) => void; running: boolean }) {
  const last = attempts.at(-1)?.number;
  return (
    <div className="attempts" role="group" aria-label="Attempts" data-testid="attempts">
      {attempts.map((a) => {
        const live = running && a.number === last;
        return (
          <button key={a.number} type="button" className="att" aria-pressed={a.number === on} onClick={() => onPick(a.number)}>
            <b>
              {live && <span className="dot live" aria-hidden="true" />}
              Attempt {a.number}
            </b>
            <span>{attemptLine(a, live)}</span>
          </button>
        );
      })}
    </div>
  );
}

/** The words that started an Attempt, pinned at its top: a Follow-up, a Rerun's note or a
 * send-back's, with what it means for the agents' sessions. */
export function AttemptPin({ attempt }: { attempt: Attempt }) {
  const c = causeOf(attempt);
  if (!c) return null;
  const said = c.note?.trim();
  const head = c.kind === "send_back" ? "Sent back" : c.kind === "rerun" ? "Your rerun" : "Your change";
  const chips =
    c.kind === "follow_up"
      ? [
          // A Fork's stage starts at attempt 1 with new sessions, so there is no session to continue.
          attempt.number > 1 ? `Continues attempt ${attempt.number - 1}'s session` : "New session, your words first",
          "Saved as a run change",
          "Retro will see it",
        ]
      : c.kind === "rerun"
        ? ["Started clean", "Saved as a run change", "Retro will see it"]
        : ["Continues the session"];
  return (
    <div className="attempt-pin" data-testid="attempt-pin">
      <span className="pin-icon" aria-hidden="true">
        <Icon name={c.kind === "rerun" ? "retry" : "edit"} />
      </span>
      <div>
        <p>
          <b>
            {head}
            {c.by ? ` by ${who(c.by)}` : ""}
            {c.at ? `, ${hhmm(c.at)}` : ""}
          </b>
          {said ? `: “${said}”` : ", with no note."}
        </p>
        <div className="pin-chips">
          {chips.map((t) => (
            <span key={t} className="chip">
              {t}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Whether a stage takes a Follow-up or a Rerun from here: a swarm stage that has finished. */
export const followable = (stage: Stage) => stage.type === "swarm" && (stage.status === "done" || stage.status === "failed");

/** Ask for a change on a finished agent stage (a Follow-up, which continues its agents'
 * sessions as a new Attempt) or Rerun from scratch, with the stages that run again and the gates
 * that wait again named before you send. Docked above the tab bar on a phone, at the foot of the
 * stage card on a desktop. */
export function FollowUpBox({ run, stage }: { run: Run; stage: Stage }) {
  const phone = usePhone();
  const [mode, setMode] = useState<Mode>("follow_up");
  const [text, setText] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState<Applied | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  useEffect(() => {
    let stale = false; // a slower answer for an earlier ask must not land
    void api
      .POST("/api/runs/{run_id}/changes/propose", { params: { path: { run_id: run.id } }, body: { kind: mode, stage: stage.id } })
      .then(({ data, error, response }) => {
        if (stale) return;
        setProposal(data ?? null);
        setProblem(data ? null : refusal(error, response, "The change could not be checked"));
      });
    return () => {
      stale = true;
    };
  }, [run.id, run.revision, stage.id, mode]);

  const blocked = problem ?? proposal?.violations?.[0] ?? null;
  const ready = !!proposal && !blocked && !busy && (mode === "rerun" || !!text.trim());

  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (!ready || !proposal) return;
    setBusy(true);
    const { data, error, response } = await api.POST("/api/runs/{run_id}/changes", {
      params: { path: { run_id: run.id } },
      body: { kind: mode, stage: stage.id, payload: { note: text.trim() }, revision: proposal.revision },
    });
    setBusy(false);
    if (data) {
      setApplied(data);
      setText("");
    } else setProblem(refusal(error, response, "The change was refused"));
  }

  const rerun = mode === "rerun";
  const warn = blocked ?? (proposal ? rerunWarning(run, stage, proposal) : null);
  const what = stage.name.toLowerCase();
  return (
    <form className="composer msg-box follow-box action-bar" aria-label={`Ask ${stage.name} for a change`} onSubmit={(e) => void send(e)} data-testid="follow-up-box">
      {!phone && (
        <div className="msg-row">
          <div className="seg" role="group" aria-label="How">
            <button type="button" aria-pressed={!rerun} onClick={() => setMode("follow_up")}>
              Ask for a change
            </button>
            <button type="button" aria-pressed={rerun} onClick={() => setMode("rerun")}>
              Rerun from scratch
            </button>
          </div>
          <span className="faint">Keeps the session and what it read. Rerun starts clean.</span>
        </div>
      )}
      {warn && (
        <p className={blocked ? "warnline bad" : "warnline"} role={blocked ? "alert" : "status"} data-testid="rerun-warning">
          <Icon name="warning" size={16} />
          <span>{warn}</span>
        </p>
      )}
      {applied && (
        <p className="msg-done" role="status" data-testid="follow-up-sent">
          <Icon name="check" size={16} />
          {applied.fork ? <RunRef label="Saved as a run change. It continues in" run={applied.fork} /> : "Saved as a run change."}
        </p>
      )}
      <div className="field">
        <label htmlFor={`follow-${stage.id}`} className="visually-hidden">
          {rerun ? `Note for the rerun of ${stage.name}` : `What should change in ${stage.name}`}
        </label>
        <textarea
          ref={field}
          id={`follow-${stage.id}`}
          rows={1}
          value={text}
          placeholder={rerun ? "Rerun from scratch, with a note if you like..." : phone ? `Ask for a change to ${what}...` : `What should change in ${what}?`}
          enterKeyHint={phone ? "enter" : "send"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !phone) void send(e);
          }}
        />
        {phone && (
          <button type="button" className="icon" aria-label="Rerun from scratch" aria-pressed={rerun} onClick={() => setMode(rerun ? "follow_up" : "rerun")}>
            <Icon name="retry" />
          </button>
        )}
        <button type="submit" className="icon send" aria-label={rerun ? "Rerun" : "Send"} disabled={!ready}>
          <Icon name="send" />
        </button>
      </div>
    </form>
  );
}
