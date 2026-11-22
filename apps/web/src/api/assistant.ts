/** Asking a run's Run Assistant from a gate, shared by the Needs you page and a skin's own gate
 * screen (Talk), so both ask, apply and dismiss alike. */
import { useState } from "react";
import YAML from "yaml";

import { api } from "./client";
import { type Gate, money, refusal } from "./runs";
import type { components } from "./schema";

export type Card = components["schemas"]["Card"];
export type Said = components["schemas"]["Message-Output"];
type Conversation = components["schemas"]["Conversation"];
type Kind = components["schemas"]["ApplyChange"]["kind"];

/** One turn: the assistant's reply, or why it did not answer. */
export async function askRun(runId: string, message: string): Promise<{ said?: Said; refused?: string }> {
  const { data, error, response } = await api.POST("/api/runs/{run_id}/assistant", {
    params: { path: { run_id: runId } },
    body: { message },
  });
  return data ? { said: data } : { refused: refusal(error, response, "The assistant did not answer") ?? undefined };
}

/** One exchange at a gate: what you asked, and the reply, or why it did not come. */
export type Exchange = { id: string; asked: string; at: string; said?: Said; failed?: string };

/** A gate's turns with its run's assistant, for each skin to show alike: the exchanges so far,
 * when the unanswered one went (`since`), `ask` (again, with its id, to retry), `drop` (a failed
 * one taken back to edit). A reply's fields go to `onFill`, and `filled` says which ones it
 * filled; `onSaid` runs after each answer. */
export function useGateAsk(gate: Gate, onFill: (fields: Record<string, string>) => void, onSaid?: () => Promise<unknown>) {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [since, setSince] = useState<number | null>(null);

  async function ask(asked: string, id = `ask-${Date.now()}`) {
    if (since !== null) return;
    setExchanges((x) => [...x.filter((e) => e.id !== id), { id, asked, at: new Date().toISOString() }]);
    setSince(Date.now());
    const { said, refused } = await askRun(gate.run_id, asked).catch(() => ({ said: undefined, refused: "the API could not be reached" }));
    setExchanges((x) => x.map((e) => (e.id === id ? { ...e, said, failed: said ? undefined : (refused ?? "the assistant did not answer") } : e)));
    setSince(null);
    if (!said) return;
    if (Object.keys(said.fields ?? {}).length > 0) onFill(said.fields ?? {});
    await onSaid?.();
  }
  const drop = (id: string) => setExchanges((x) => x.filter((e) => e.id !== id));

  const labelOf = (key: string) => gate.publish?.fields?.find((f) => f.key === key)?.label ?? key;
  const filled = (said: Said) => {
    const keys = Object.keys(said.fields ?? {});
    return keys.length > 0 ? `Filled ${keys.map(labelOf).join(", ")} in the form above. Check it, then answer.` : null;
  };
  return { exchanges, since, ask, drop, filled };
}

/** Apply a proposal as proposed, through the Run Change seam, and record it on the card. Resolves
 * to the refusal (the run moved on, the check failed), or null once applied. */
export async function applyCard(runId: string, card: Card): Promise<string | null> {
  const { change, revision } = card.proposal;
  const { data, error, response } = await api.POST("/api/runs/{run_id}/changes", {
    params: { path: { run_id: runId } },
    body: { kind: change.kind as Kind, stage: change.stage, payload: change.payload, revision },
  });
  if (!data) return refusal(error, response, "The change was refused");
  await markCard(runId, card, "applied", data.id);
  return null;
}

/** Record what became of a proposal: applied (naming the change) or dismissed with Not now. */
export async function markCard(runId: string, card: Card, state: "applied" | "dismissed", changeId?: string) {
  await api.POST("/api/runs/{run_id}/assistant/proposals/{proposal_id}", {
    params: { path: { run_id: runId, proposal_id: card.id } },
    body: { state, change_id: changeId ?? null },
  });
}

/** What a question costs: the world's real average so far, against what its month has left. */
export function askingCosts(c?: Pick<Conversation, "average_usd" | "month_left_usd"> | null): string {
  const figure = c?.average_usd != null ? `Asking costs about ${money(c.average_usd)} a question` : "The first question sets what asking costs";
  const from = c?.month_left_usd != null ? `from the ${money(c.month_left_usd)} the world has left this month` : "from the world's budget";
  return `${figure}, ${from}.`;
}

/** The composer's cost line: what a question costs, then what the month has left, each beside
 * the other (Chat v1: a figure is never alone). */
export function costMeta(c?: Pick<Conversation, "average_usd" | "month_left_usd"> | null): [string, string] {
  return [
    c?.average_usd != null ? `About ${money(c.average_usd)} a question` : "The first question sets the cost",
    c?.month_left_usd != null ? `${money(c.month_left_usd)} left this month` : "from the world's budget",
  ];
}

/** A proposal's diff as -/+ rows: a file's lines, or its fields as YAML. */
export function proposalRows(p: Card["proposal"]): { sign: "-" | "+"; text: string }[] {
  const text = (v: Record<string, unknown>) => (p.change.kind === "artifact_edit" ? String(v.text ?? "") : YAML.stringify(v));
  return lines(text(p.before), text(p.after)).map((l) => ({ sign: l.startsWith("-") ? "-" : "+", text: l.slice(1) }));
}

/** A change card's title by kind, in the spec's words: "Edit metadata.md", "Re-run from Script". */
export function cardTitle(card: Card): string {
  const { change, stage_name } = card.proposal;
  if (change.kind === "artifact_edit") return `Edit ${String(change.payload?.path ?? stage_name)}`;
  if (change.kind === "rerun") return `Re-run from ${stage_name}`;
  if (change.kind === "follow_up") return `Ask ${stage_name} for a change`;
  if (change.kind === "skip") return `Skip ${stage_name}`;
  if (change.kind === "restructure") return `Change the stages from ${stage_name} on`;
  return `Edit ${stage_name}`;
}

/** The lines that differ, `-` before and `+` after, around the lines both share at the top and
 * bottom: enough for the one place a person edits a file. */
export function lines(before: string, after: string): string[] {
  const a = before.split("\n");
  const b = after.split("\n");
  let top = 0;
  while (top < a.length && top < b.length && a[top] === b[top]) top++;
  let end = 0;
  while (end < a.length - top && end < b.length - top && a[a.length - 1 - end] === b[b.length - 1 - end]) end++;
  return [...a.slice(top, a.length - end).map((l) => `-${l}`), ...b.slice(top, b.length - end).map((l) => `+${l}`)];
}
