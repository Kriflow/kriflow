import { useEffect, useState } from "react";

import { type Card, cardTitle, costMeta, type Exchange, markCard, useGateAsk } from "../api/assistant";
import { api } from "../api/client";
import { reloadGates } from "../api/gates";
import type { Gate, Run } from "../api/runs";
import type { components } from "../api/schema";
import { Chat, DoneLine, exchangeTurns } from "../Chat";
import { RUN_ASSISTANT, ProposedCard } from "./RunAssistant";

type Conversation = components["schemas"]["Conversation"];

/** Prompts at a gate, the same for any workflow: what changed, the links, a second look. */
export const GATE_STARTERS = ["What changed since the last run?", "Check every link before I answer", "What would you change before I approve?"];

/** Ask before you answer (spec Run Assistant), in the shared chat under the gate's answer: a turn
 * with the run's assistant. Its proposals land here as change cards; at a publish gate the fields
 * it fills go into the gate's form through `onFill`, marked there for the person to check. An
 * applied change reloads the gates, so the preview shows what will really go out. */
export function GateAsk({ gate, onFill }: { gate: Gate; onFill: (fields: Record<string, string>) => void }) {
  const [run, setRun] = useState<Run | null>(null);
  const [costs, setCosts] = useState<Conversation | null>(null);
  const [dismissed, setDismissed] = useState<string[]>([]);

  const path = { params: { path: { run_id: gate.run_id } } };
  // The run the cards apply to, and what asking costs, read again after each turn.
  const load = () =>
    Promise.all([
      api.GET("/api/runs/{run_id}/assistant", path).then(({ data }) => data && setCosts(data)),
      api.GET("/api/runs/{run_id}", path).then(({ data }) => data && setRun(data)),
    ]).catch(() => undefined);
  useEffect(() => void load(), [gate.run_id]); // eslint-disable-line react-hooks/exhaustive-deps
  const { exchanges, since, ask, drop, filled } = useGateAsk(gate, onFill, load);

  const cards = (said: NonNullable<Exchange["said"]>) =>
    run &&
    (said.proposals ?? []).map((card: Card) => {
      const stage = run.stages.find((s) => s.id === card.proposal.change.stage);
      if (!stage) return null;
      if (dismissed.includes(card.id)) return <DoneLine key={card.id} dismissed>Not now: {cardTitle(card)}</DoneLine>;
      return (
        <ProposedCard
          key={card.id}
          run={run}
          stage={stage}
          card={card}
          onClose={() => {
            setDismissed((d) => [...d, card.id]);
            void markCard(gate.run_id, card, "dismissed");
          }}
          onApplied={(change) => {
            void markCard(gate.run_id, card, "applied", change.id);
            reloadGates();
            void load();
          }}
        />
      );
    });

  return (
    <section className="gate-ask" aria-label="Ask before you answer">
      <header className="sect-h">
        <h3>Ask before you answer</h3>
        <p>Run assistant reads this run. Nothing changes until you apply.</p>
      </header>
      <Chat
        label="Ask or change"
        speaker={RUN_ASSISTANT}
        turns={exchangeTurns(exchanges, filled, cards)}
        waiting={since}
        chips={exchanges.length === 0 ? GATE_STARTERS : []}
        placeholder="Ask or change before you answer"
        meta={["Your note to the gate stays separate, under Add a note.", costMeta(costs).join(", ")]}
        refs={run?.stages.map((s) => s.name) ?? []}
        onSend={(text) => void ask(text)}
        onRetry={(t) => void ask(t.text, t.id)}
        onEdit={(t) => drop(t.id)}
      />
    </section>
  );
}

