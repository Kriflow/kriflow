import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { cardTitle, costMeta, proposalRows } from "../api/assistant";
import { api } from "../api/client";
import { refusal, type Run, took } from "../api/runs";
import type { components } from "../api/schema";
import { Chat, ChangeFrame, type ChatTurn, DoneLine, refIcon } from "../Chat";
import { Icon } from "../Icon";
import { usePhone } from "../usePhone";
import { type AppliedChange, AppliedLine, ChangeCard } from "./ChangeCard";
import { StepChangeCard } from "./StepChangeCard";

type Conversation = components["schemas"]["Conversation"];
type Card = components["schemas"]["Card"];
type Said = components["schemas"]["Message-Output"];

/** A message of yours not yet answered: on its way, or `failed` with the reason. */
type Pending = { id: string; text: string; at: string; failed?: string };

export const RUN_ASSISTANT = { name: "Run assistant", mark: "RA" };

/** The Run Assistant (spec Run Assistant) in the shared chat: ask the run in plain words; its
 * answers name what they read and its proposed changes come as change cards, applied through the
 * Run Change seam. Nothing changes until you apply. `onSpent` hears what asking has cost; `files`
 * are the run's files `@` can point at. */
export function RunAssistant({ run, onSpent, onClose, files = [] }: { run: Run; onSpent: (usd: number) => void; onClose?: () => void; files?: string[] }) {
  const phone = usePhone();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [since, setSince] = useState<number | null>(null);

  async function load() {
    const { data } = await api.GET("/api/runs/{run_id}/assistant", { params: { path: { run_id: run.id } } });
    if (!data) return;
    setConversation(data);
    onSpent(data.spent_usd);
  }
  useEffect(() => {
    load().catch(() => undefined);
  }, [run.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function ask(message: string, id = `local-${Date.now()}`) {
    if (since !== null) return;
    setPending((p) => [...p.filter((m) => m.id !== id), { id, text: message, at: new Date().toISOString() }]);
    setSince(Date.now());
    const { error, response } = await api
      .POST("/api/runs/{run_id}/assistant", { params: { path: { run_id: run.id } }, body: { message } })
      .catch(() => ({ error: undefined, response: { status: 0, ok: false } as Response }));
    if (error || !response.ok) {
      const why = response.status === 0 ? "the API could not be reached" : (refusal(error, response, "The assistant did not answer") ?? "the assistant did not answer");
      setPending((p) => p.map((m) => (m.id === id ? { ...m, failed: why } : m)));
    } else {
      await load().catch(() => undefined);
      setPending((p) => p.filter((m) => m.id !== id));
    }
    setSince(null);
  }

  async function mark(card: Card, state: "applied" | "dismissed", changeId?: string) {
    const { data } = await api.POST("/api/runs/{run_id}/assistant/proposals/{proposal_id}", {
      params: { path: { run_id: run.id, proposal_id: card.id } },
      body: { state, change_id: changeId ?? null },
    });
    // An applied card stays as it is, saying who applied it; the record is for a reload.
    if (!data || state === "applied") return;
    setConversation((c) => c && { ...c, messages: c.messages.map((m) => ({ ...m, proposals: m.proposals?.map((p) => (p.id === data.id ? data : p)) })) });
  }

  const turns: ChatTurn[] = [
    ...(conversation?.messages ?? []).map((m) => turnOf(m, run, (card, state, id) => void mark(card, state, id))),
    ...pending.map((p) => ({ id: p.id, mine: true, text: p.text, at: p.at, failed: p.failed })),
  ];
  const at = run.stages.find((s) => s.status === "running" || s.status === "waiting") ?? [...run.stages].reverse().find((s) => s.status === "done");
  const costs = costMeta(conversation);
  return (
    <Chat
      label="Ask this run"
      fit={!phone}
      className="assistant"
      testId="assistant"
      title="Ask this run"
      context={
        phone
          ? `${run.name}${at ? ` · stage ${at.index + 1} of ${run.stages.length}, ${at.name}` : ""}`
          : "Reads the run, proposes changes. Nothing changes until you apply."
      }
      onClose={onClose}
      speaker={RUN_ASSISTANT}
      turns={turns}
      waiting={since}
      empty={{
        line: "Ask anything about this run, or say what to change. It reads every stage, file and log line, and proposes changes you apply.",
        starters: starters(run),
      }}
      placeholder={phone ? "Ask, or say what to change" : "Ask, or say what to change. @ for a stage or file"}
      meta={phone ? costs : ["Enter sends", costs.join(", ")]}
      refs={[...new Set([...run.stages.map((s) => s.name), ...files])]}
      onSend={(text) => void ask(text)}
      onRetry={(t) => void ask(t.text, t.id)}
      onEdit={(t) => setPending((p) => p.filter((m) => m.id !== t.id))}
    />
  );
}

/** One message of the conversation as a chat turn: yours, or the assistant's with what it read
 * (each opening its stage) and its proposals as cards. */
function turnOf(m: Said, run: Run, onMark: (card: Card, state: "applied" | "dismissed", changeId?: string) => void): ChatTurn {
  if (m.role === "operator") return { id: m.id, mine: true, text: m.text, at: m.at };
  return {
    id: m.id,
    text: m.text,
    at: m.at,
    read: (m.sources ?? []).map((s, n) => (
      <Link key={n} className="ref" to="/worlds/$worldId/runs/$runId" params={{ worldId: run.world_id, runId: run.id }} search={{ stage: s.stage }}>
        <Icon name={refIcon(s.label)} />
        {s.label}
      </Link>
    )),
    after: (m.proposals ?? []).map((card) => <Proposed key={card.id} run={run} card={card} onMark={(state, id) => onMark(card, state, id)} />),
  };
}

/** Prompts drawn from the run's own state: its slowest stage, what goes out next, the budget. */
export function starters(run: Run, now = Date.now()): string[] {
  const spans = run.stages
    .filter((s) => s.started_at && s.type !== "gate")
    .map((s) => ({ s, sec: ((s.ended_at ? Date.parse(s.ended_at) : now) - Date.parse(s.started_at ?? "")) / 1000 }))
    .filter((x) => x.sec >= 1)
    .sort((a, b) => b.sec - a.sec);
  const slow = spans[0];
  const out = run.stages.find((s) => s.type === "connector" && (s.status === "pending" || s.status === "waiting"));
  return [
    ...(slow ? [`Why did ${slow.s.name} take ${took(slow.sec)}?`] : []),
    ...(out ? [`What will ${out.name} send?`] : []),
    ...(run.budget_usd != null ? ["How much of the budget is left?"] : []),
    "Re-run a stage with a note",
  ];
}

/** One proposal in the conversation: the change card while open or stale, one line once applied
 * or dismissed with Not now (Show opens a dismissed one, read only). */
function Proposed({ run, card, onMark }: { run: Run; card: Card; onMark: (state: "applied" | "dismissed", changeId?: string) => void }) {
  const [shown, setShown] = useState(false);
  const stage = run.stages.find((s) => s.id === card.proposal.change.stage);
  if (!stage) return null;
  if (card.state === "dismissed")
    return shown ? (
      <ChangeFrame title={cardTitle(card)} status="Not now" diff={proposalRows(card.proposal)} testId="dismissed-card" />
    ) : (
      <DoneLine
        dismissed
        action={
          <button type="button" className="link" onClick={() => setShown(true)}>
            Show
          </button>
        }
      >
        Not now: {cardTitle(card)}
      </DoneLine>
    );
  if (card.state === "applied") {
    const change = (run.changes ?? []).find((c) => c.id === card.change_id);
    return change ? <AppliedLine run={run} title={cardTitle(card)} applied={change} /> : <DoneLine>{cardTitle(card)} applied</DoneLine>;
  }
  return <ProposedCard run={run} stage={stage} card={card} onClose={() => onMark("dismissed")} onApplied={(c) => onMark("applied", c.id)} />;
}

/** The change card for a proposal of any kind, as proposed: a Rerun or a Skip as a step change,
 * the others as a change card. Shared by the run page and the gate panel. */
export function ProposedCard({
  run,
  stage,
  card,
  onClose,
  onApplied,
}: {
  run: Run;
  stage: Run["stages"][number];
  card: Card;
  onClose: () => void;
  onApplied: (change: AppliedChange) => void;
}) {
  const { kind } = card.proposal.change;
  if (kind === "rerun" || kind === "skip")
    return <StepChangeCard run={run} stage={stage} kind={kind} proposed={card.proposal} onClose={onClose} onApplied={onApplied} />;
  const edit = kind === "restructure" ? "restructure" : "stage_edit";
  return <ChangeCard run={run} stage={stage} kind={edit} proposed={card.proposal} onClose={onClose} onApplied={onApplied} />;
}
