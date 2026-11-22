import { type FormEvent, useLayoutEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { Agent, LogEntry } from "../api/events";
import { refusal, type Run } from "../api/runs";
import { who } from "../api/tasks";
import { Icon } from "../Icon";
import { usePhone } from "../usePhone";
import { faceOf } from "./RunHero";

type Stage = Run["stages"][number];
type Said = { to?: string | null; interrupt?: boolean; state?: string; why?: string | null };

const said = (e: LogEntry) => (e.input ?? {}) as Said;
const agents = (n: number) => (n === 1 ? "1 agent" : `${n} agents`);
/** What happened to one Message (its `message` lines, one or two per agent): delivered to every
 * agent, or queued for some and why. */
export function receipt(lines: LogEntry[]): { queued: boolean; text: string } {
  const sent = [...new Set(lines.map((e) => e.agent))];
  const delivered = new Set(lines.filter((e) => said(e).state === "delivered").map((e) => e.agent));
  const dropped = new Set(lines.filter((e) => said(e).state === "dropped").map((e) => e.agent));
  if (dropped.size && !delivered.size) return { queued: true, text: "Not delivered: the stage ended first" };
  const waiting = sent.filter((a) => !delivered.has(a) && !dropped.has(a));
  const to = delivered.size === 1 && sent.length === 1 ? who(sent[0]!) : agents(delivered.size);
  if (!waiting.length) return { queued: false, text: `Delivered to ${to}, saved as a run change` };
  const why = lines.filter((e) => said(e).state === "queued").map((e) => said(e).why).find(Boolean) ?? "gets it at the next turn.";
  const names = new Intl.ListFormat("en-GB", { type: "conjunction" }).format(waiting.map(who));
  const head = delivered.size ? `Delivered to ${agents(delivered.size)}. ` : "";
  const queued = waiting.length === 1 ? `Queued: ${names} is ${why}` : `Queued for ${names}: each is ${why}`;
  return { queued: true, text: head + queued };
}

/** One Message in the Transcript: who it went to, your words, and its receipt. */
export function MessageLine({ lines }: { lines: LogEntry[] }) {
  const first = lines[0]!;
  const { to, interrupt } = said(first);
  const r = receipt(lines);
  return (
    <div className="you" data-testid="message">
      <span className="to">
        You to {to ? who(to) : "everyone still working"}
        {interrupt ? ", stop and do this instead" : ""}
      </span>
      <div className="bubble">{first.detail}</div>
      <span className={r.queued ? "receipt queued" : "receipt"} role="status" data-testid="receipt">
        <Icon name={r.queued ? "clock" : "check"} size={16} />
        {r.text}
      </span>
    </div>
  );
}

/** Whether a stage takes messages now: an agent stage that runs, with an agent still working. */
export const messageable = (stage: Stage, working: Agent[]) =>
  stage.status === "running" && stage.type !== "gate" && stage.type !== "connector" && working.length > 0;

/** The message box of a running agent stage, Chat v1's composer: who it goes to (everyone
 * working or one agent), the text, Send, and Stop and do this instead. Docked above the tab bar
 * on a phone, at the foot of the stage card on a desktop. */
export function MessageBox({ run, stage, working }: { run: Run; stage: Stage; working: Agent[] }) {
  const phone = usePhone();
  const [to, setTo] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);
  const target = to && working.some((a) => a.name === to) ? to : null;

  async function send(interrupt: boolean, e?: FormEvent) {
    e?.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    const { error: err, response } = await api.POST("/api/runs/{run_id}/messages", {
      params: { path: { run_id: run.id } },
      body: { stage: stage.id, text: text.trim(), to: target, interrupt },
    });
    setBusy(false);
    if (err) setError(refusal(err, response, "Could not send the message"));
    else setText("");
  }

  const stop = (
    <button type="button" className={phone ? "icon stop" : "stop"} aria-label="Stop and do this instead" disabled={!text.trim() || busy} onClick={() => void send(true)}>
      <Icon name="stop" />
      {!phone && "Stop and do this instead"}
    </button>
  );
  return (
    <form className="composer msg-box action-bar" aria-label={`Message ${stage.name}`} onSubmit={(e) => void send(false, e)} data-testid="message-box">
      <div className="msg-to" role="group" aria-label="To">
        {!phone && <span className="faint">To</span>}
        <button type="button" className="chipbtn" aria-pressed={!target} onClick={() => setTo(null)}>
          <Icon name="people" size={16} />
          Everyone working ({working.length})
        </button>
        {working.map((a) => {
          const f = faceOf(a.name);
          return (
            <button key={a.name} type="button" className="chipbtn" aria-pressed={target === a.name} onClick={() => setTo(a.name)}>
              <span className="face" style={{ background: f.colour }} aria-hidden="true">
                {f.label}
              </span>
              {who(a.name)}
            </button>
          );
        })}
      </div>
      <div className="field">
        <label htmlFor={`msg-${stage.id}`} className="visually-hidden">
          Message {stage.name}
        </label>
        <textarea
          ref={field}
          id={`msg-${stage.id}`}
          rows={1}
          value={text}
          placeholder={phone ? "Message this step..." : "Message the agents in this step..."}
          enterKeyHint={phone ? "enter" : "send"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !phone) void send(false, e);
          }}
        />
        {phone && stop}
        <button type="submit" className="icon send" aria-label="Send" disabled={!text.trim() || busy}>
          <Icon name="send" />
        </button>
      </div>
      {error && (
        <p className="msg-error" role="alert">
          {error}
        </p>
      )}
      {!phone && (
        <div className="msg-row">
          <span className="faint">They read it at their next turn. Each reply is one more turn of spend.</span>
          {stop}
        </div>
      )}
    </form>
  );
}
