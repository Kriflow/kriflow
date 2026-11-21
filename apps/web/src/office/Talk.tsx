import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { type CSSProperties, useEffect, useId, useRef, useState } from "react";

import {
  blocked,
  choosable,
  type Choice,
  decision,
  editable,
  gateArtifacts,
  gateProposal,
  type Proposal,
  sendBackTargets,
  staysOpen,
  topUpSuggestion,
  underReview,
  useGateForm,
  REJECT,
} from "../api/answer";
import { applyCard, type Card, cardTitle, costMeta, markCard, proposalRows, type Said, useGateAsk } from "../api/assistant";
import { api } from "../api/client";
import { labelOf, useConnectors } from "../api/connectors";
import type { Agent, ArtifactFile } from "../api/events";
import { dropGate, reloadGates, useOpenGates } from "../api/gates";
import { abortAsk, type Gate, money, refusal, type Run, useRunUpdates } from "../api/runs";
import { ChangeFrame, Chat, DoneLine, exchangeTurns, What } from "../Chat";
import { usePhone } from "../usePhone";
import { type Ask as AskFor, useConfirm } from "../Dialog";
import type { components } from "../api/schema";
import type { Skin } from "../skin";
import { Actor, type Look, stillFrame, useCast } from "./cast";
import { appClass, FullScreen, Tabs, useNight } from "./chrome";
import "./office.css";
import { asker, line } from "./scene";

type PublishPreview = components["schemas"]["PublishPreview"];
type Conversation = components["schemas"]["Conversation"];
type Kind = components["schemas"]["ApplyChange"]["kind"];

/** One answer at a gate: its button, whether the asker cheers (yes) or slumps (stop, send back),
 * and the choice it makes from the form, or null when it should not go (a pick left empty). */
type Option = { label: string; cheer: boolean; disabled?: boolean; choose: (form: FormData) => Choice | null | Promise<Choice | null> };
type Reaction = { said: string; name: string; look: Look | null; cheer: boolean; n: number };

/** Talk: the askers at Your office, oldest first, one at a time. The asker stands large and says
 * the gate's ask; the choices are the gate's real ones, posted by the shared answering code, so
 * answering here is the same as on the plain Gates page. Number keys answer. */
export function Talk() {
  const { worldId } = useParams({ from: "/worlds/$worldId/office/talk" });
  const search = useSearch({ from: "/worlds/$worldId/office/talk" });
  const navigate = useNavigate();
  const open = useOpenGates();
  const [runs, setRuns] = useState<Run[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [refused, setRefused] = useState<string | null>(null);
  const [last, setLast] = useState<Reaction | null>(null);
  // Answered this visit: a gate opened again after a send back is a new decision, so match the opening too.
  const [answered, setAnswered] = useState<Gate[]>([]);
  const [skin, setSkin] = useState<Skin | undefined>(undefined);
  const { cast, look, nameOf } = useCast(worldId, undefined, skin);
  const asked = useRef(new Set<string>());

  const saw = useRunUpdates(worldId, (run) => setRuns((prev) => [run, ...prev.filter((r) => r.id !== run.id)]));
  useEffect(() => {
    // The world's skin says which pack and casting dress the askers.
    api
      .GET("/api/worlds/{world_id}", { params: { path: { world_id: worldId } } })
      .then(({ data }) => setSkin(data?.skin ?? undefined))
      .catch(() => undefined);
    api
      .GET("/api/worlds/{world_id}/runs", { params: { path: { world_id: worldId } } })
      .then(({ data, response }) => {
        saw(response);
        if (data) setRuns(data);
      })
      .catch(() => undefined);
  }, [worldId, saw]);

  const waiting = line(open.gates, worldId).filter(
    (g) => !answered.some((a) => a.stage_id === g.stage_id && a.opened_at === g.opened_at),
  );
  // Who asks needs the run's agents; read each waiting run's once.
  useEffect(() => {
    for (const runId of new Set(waiting.map((g) => g.run_id))) {
      if (asked.current.has(runId)) continue;
      asked.current.add(runId);
      api
        .GET("/api/runs/{run_id}/agents", { params: { path: { run_id: runId } } })
        .then(({ data }) => data && setAgents((prev) => [...prev, ...data]))
        .catch(() => undefined);
    }
  });

  const planner = cast.find((m) => m.role === "planner")?.name;
  const who = (gate: Gate) => {
    const name = asker(gate, runs, agents, planner);
    return { name: name ? nameOf(name) : "Someone", look: name ? look(name) : null };
  };
  const gate = waiting.find((g) => g.stage_id === search.gate) ?? waiting[0];

  async function answer(g: Gate, send: () => Promise<string | null>, said: string, cheer: boolean) {
    setRefused(null);
    const why = await send();
    if (why) {
      setRefused(why);
      reloadGates();
      return;
    }
    setLast((prev) => ({ said, cheer, ...who(g), n: (prev?.n ?? 0) + 1 }));
    // A budget gate stays until the run resumes, so its asker stays up.
    if (staysOpen(g)) return reloadGates();
    dropGate(g.stage_id);
    setAnswered((a) => [...a, g]);
    const next = waiting.find((w) => w.stage_id !== g.stage_id);
    void navigate({ to: "/worlds/$worldId/office/talk", params: { worldId }, search: next ? { gate: next.stage_id } : {} });
  }

  const [night] = useNight();
  const crew = cast.flatMap((m) => look(m.name) ?? []).slice(0, 3);
  const moving = runs.filter((r) => !r.parent_run_id && r.status === "running").length;
  return (
    <div className={appClass(night, "talk-app")}>
      <header className="office-bar" aria-label="Talk">
        <Link className="office-btn office-back office-wide" to="/worlds/$worldId/office" params={{ worldId }} activeOptions={{ exact: true }}>
          Back to the office
        </Link>
        {gate && (
          <div className="office-chip" data-testid="talk-place">
            {waiting.indexOf(gate) + 1} <span>of {waiting.length} waiting at Your office</span>
          </div>
        )}
        <Link className="office-btn office-wide" to="/worlds/$worldId/office/crew" params={{ worldId }}>
          Crew
        </Link>
        <FullScreen />
        <Tabs worldId={worldId} />
      </header>
      {(refused ?? (open.failed && "Could not load the gates. Is the API running?")) && (
        <p className="office-note" role="alert">
          {refused ?? "Could not load the gates. Is the API running?"}
        </p>
      )}
      {last && (
        <p key={last.n} className="office-note talk-said" role="status">
          {last.look && <img src={stillFrame(last.look, last.cheer ? "cheer" : "stuck")} alt={`${last.name} ${last.cheer ? "cheers" : "slumps"}`} />}
          {last.said}.
        </p>
      )}
      <main className="talk-room">
        {gate ? (
          <Ask
            key={`${gate.stage_id}/${gate.opened_at}`}
            gate={gate}
            run={runs.find((r) => r.id === gate.run_id)}
            asker={who(gate)}
            onAnswer={answer}
          />
        ) : open.gates === null ? (
          !open.failed && <p role="status">Loading...</p>
        ) : (
          <section className="talk-clear" data-testid="talk-clear">
            {/* The crew stands about with nothing to ask; the first waves. Decoration: the words say it. */}
            <ul className="office-cast talk-crew" aria-hidden="true">
              {crew.map((l, i) => (
                <Actor key={l.agent} look={l} pose={i === 0 ? "cheer" : "idle"} />
              ))}
            </ul>
            <h2>All clear</h2>
            <p>
              Nobody needs you.{" "}
              {moving === 0 ? "No run is moving" : moving === 1 ? "One run is moving on its own" : `${moving} runs are moving on their own`}; the
              next question lands here.
            </p>
            <Link className="office-btn" to="/worlds/$worldId/office" params={{ worldId }}>
              Back to the office
            </Link>
          </section>
        )}
      </main>
    </div>
  );
}

/** One asker and their gate: the ask, the run beside it, the publish preview before any choice,
 * and the numbered choices. */
function Ask({
  gate,
  run,
  asker,
  onAnswer,
}: {
  gate: Gate;
  run?: Run;
  asker: { name: string; look: Look | null };
  onAnswer: (gate: Gate, send: () => Promise<string | null>, said: string, cheer: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [confirm, dialog] = useConfirm();
  const formId = useId();
  // The gate's form, and whether an editable field of the post differs from what the gate shows.
  const { form, changed, edited, fill } = useGateForm(gate);
  // What the earlier stages made (a pick's cards, what an approve reviews), and a skill gate's proposal.
  const [artifacts, setArtifacts] = useState<ArtifactFile[] | null>(null);
  const [proposal, setProposal] = useState<Proposal | null | undefined>(undefined);
  useEffect(() => {
    if (gate.kind === "pick" || gate.kind === "approve") {
      gateArtifacts(gate)
        .then(setArtifacts)
        .catch(() => setArtifacts([]));
    }
    if (gate.kind === "skill") {
      gateProposal(gate)
        .then(setProposal)
        .catch(() => setProposal(null));
    }
  }, [gate.run_id, gate.stage_index, gate.kind, gate.send_backs_left]);

  const cards = gate.kind === "pick" && artifacts ? choosable(artifacts) : [];
  const reviewed = gate.kind === "approve" && artifacts ? underReview(artifacts) : [];
  const options = choices(gate, artifacts, cards, proposal, changed, confirm);

  async function choose(option: Option) {
    if (busy || option.disabled || !form.current) return;
    const fields = new FormData(form.current);
    const choice = await option.choose(fields);
    if (!choice) return;
    const edits = Object.fromEntries([...fields].flatMap(([k, v]) => (k.startsWith("edit:") ? [[k.slice(5), String(v)]] : [])));
    const { send, said } = decision(gate, choice, { note: String(fields.get("note") ?? "").trim(), edits, reviewed });
    setBusy(true);
    await onAnswer(gate, send, said, option.cheer);
    setBusy(false);
  }

  // Number keys answer, unless the operator is typing.
  const latest = useRef(choose);
  latest.current = choose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.metaKey || e.ctrlKey || e.altKey) return;
      if (document.querySelector("dialog[open]")) return; // a confirm is asking
      const option = options[Number(e.key) - 1];
      if (/^[1-9]$/.test(e.key) && option) void latest.current(option);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const spent = gate.spent_usd ?? run?.spent_usd ?? 0;
  const budget = gate.budget_usd ?? run?.budget_usd;
  const label = asker.look?.label;
  return (
    <div className="talk-scene" style={{ "--accent": asker.look?.colour ?? undefined } as CSSProperties}>
      {dialog}
      {asker.look && (
        <div className="talk-hero">
          <img src={stillFrame(asker.look, "talk")} alt={[asker.name, label === asker.name ? "" : label, "asking"].filter(Boolean).join(", ")} />
        </div>
      )}
      <section className="talk-box" aria-label={`${asker.name} asks`}>
        <h1 className="talk-name">
          {asker.name}
          {label && label !== asker.name && <> <small>{label}</small></>}
        </h1>
        <p className="talk-line">{gate.ask}</p>
        <ul className="talk-ctx" aria-label="The run">
          <li>
            <Link to="/worlds/$worldId/runs/$runId" params={{ worldId: gate.world_id, runId: gate.run_id }} search={{ stage: gate.stage_id }}>
              {gate.run_name}
            </Link>
          </li>
          <li>
            stage {gate.stage_index + 1}
            {run ? ` of ${run.stages.length}` : ""}, {gate.stage_name}
          </li>
          <li>Spent {money(spent)}{budget == null ? ", no budget" : ` of ${money(budget)}`}</li>
        </ul>
        <form
          id={formId}
          ref={form}
          className="talk-form"
          aria-label={`Answer ${gate.stage_name}`}
          onSubmit={(e) => e.preventDefault()}
          onChange={edited}
        >
          {gate.publish && <WhatGoesOut key={JSON.stringify(gate.publish)} preview={gate.publish} worldId={gate.world_id} />}
          {reviewed.length > 0 && <p className="talk-review">Under review: {reviewed.map((a) => a.name).join(", ")}</p>}
          {gate.kind === "skill" && proposal?.skill && (
            <section className="talk-review" aria-label="Skill change">
              <p>
                {proposal.skill.action} skill {proposal.skill.name}: {proposal.skill.summary}
              </p>
              {(proposal.skill.evidence ?? []).length > 0 && <p>Evidence: {(proposal.skill.evidence ?? []).join("; ")}</p>}
              {proposal.skill.action !== "retired" && (
                <details className="talk-more">
                  <summary>Read or edit SKILL.md before approving</summary>
                  <label>
                    <span className="visually-hidden">SKILL.md (edit before approving)</span>
                    <textarea name="skill" rows={4} defaultValue={proposal.skill.content ?? ""} />
                  </label>
                </details>
              )}
            </section>
          )}
          {gate.kind === "skill" && proposal?.workflow && (
            <section className="talk-review" aria-label="Workflow change">
              <p>Workflow change: {proposal.workflow.summary}</p>
              {(proposal.workflow.evidence ?? []).length > 0 && <p>Evidence: {(proposal.workflow.evidence ?? []).join("; ")}</p>}
              <pre>{proposal.workflow.yaml}</pre>
              <label>
                <input type="checkbox" name="apply" defaultChecked /> Save it as the workflow's next version when approving
              </label>
            </section>
          )}
          {gate.kind === "skill" && proposal === null && <p className="talk-review">The proposal could not be loaded; the run keeps waiting.</p>}
          {gate.kind === "pick" && artifacts && cards.length === 0 && (
            <label>
              Your pick
              <input name="choice" autoComplete="off" placeholder="idea 1" />
            </label>
          )}
          {gate.kind === "budget" ? (
            <label>
              Add (US dollars)
              <input name="add" type="number" inputMode="decimal" min="0.01" step="0.01" defaultValue={topUpSuggestion(gate).toFixed(2)} />
            </label>
          ) : null}
          <ol className="talk-choices" aria-label="Choices">
            {options.map((o, i) => (
              <li key={o.label}>
                <button type="button" className={`office-btn${o.cheer ? " talk-yes" : ""}`} disabled={busy || o.disabled} onClick={() => void choose(o)}>
                  <kbd>{i + 1}</kbd> {o.label}
                </button>
              </li>
            ))}
          </ol>
        </form>
        {gate.kind !== "budget" && (
          <label className="talk-note">
            Note (optional)
            <textarea name="note" rows={1} form={formId} />
          </label>
        )}
      </section>
      <AskRun gate={gate} run={run} asker={asker.name} onFill={fill} />
    </div>
  );
}

/** Ask the asker before you answer (spec Run Assistant): the shared chat, in the Office's
 * colours, beside the gate on a wide screen and under it on a phone. The asker answers for the
 * run's assistant, so the skin's fiction holds. Its proposals come as change cards to apply or
 * dismiss; at a publish gate the fields it fills go into the form, for the operator to confirm. */
function AskRun({ gate, run, asker, onFill }: { gate: Gate; run?: Run; asker: string; onFill: (fields: Record<string, string>) => void }) {
  const phone = usePhone();
  const { exchanges, since, ask, drop, filled } = useGateAsk(gate, onFill);
  const [costs, setCosts] = useState<Conversation | null>(null);
  useEffect(() => {
    api
      .GET("/api/runs/{run_id}/assistant", { params: { path: { run_id: gate.run_id } } })
      .then(({ data }) => data && setCosts(data))
      .catch(() => undefined);
  }, [gate.run_id, exchanges.length]);
  const cards = (said: Said) => (said.proposals ?? []).map((card) => <TalkCard key={card.id} runId={gate.run_id} card={card} revision={run?.revision ?? 0} />);
  return (
    <Chat
      label="Ask or change"
      className="talk-chat"
      title={phone ? undefined : `Ask ${asker}`}
      context="Answers for the run assistant"
      lead={<span className="mark" aria-hidden="true">{asker[0]}</span>}
      speaker={{ name: asker, role: "for the run assistant", mark: asker[0] }}
      turns={exchangeTurns(exchanges, filled, cards)}
      waiting={since}
      placeholder={`Ask ${asker} before you answer`}
      meta={costMeta(costs)}
      refs={run?.stages.map((s) => s.name) ?? []}
      onSend={(text) => void ask(text)}
      onRetry={(t) => void ask(t.text, t.id)}
      onEdit={(t) => drop(t.id)}
    />
  );
}

/** A Run Change the assistant proposed, in the shared change card: what changes, what the check
 * refuses, Apply and Not now. Applying reloads the gates, so the preview shows what will really go
 * out. Once the run is past the `revision` it was proposed at, it is out of date: Ask again checks
 * the same change against the run as it is now. */
function TalkCard({ runId, card, revision }: { runId: string; card: Card; revision: number }) {
  const [state, setState] = useState<"open" | "applied" | "dismissed">("open");
  const [proposal, setProposal] = useState(card.proposal);
  const [refused, setRefused] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const title = cardTitle(card);
  if (state === "dismissed") return <DoneLine dismissed>Not now: {title}</DoneLine>;
  if (state === "applied")
    return (
      <div data-testid="talk-card">
        <DoneLine>
          <b>{title}</b> Applied by you.
        </DoneLine>
      </div>
    );
  const { violations, read_by, in_place, fork_from } = proposal;
  const stale = revision > proposal.revision;

  async function apply() {
    setBusy(true);
    const why = await applyCard(runId, { ...card, proposal });
    setBusy(false);
    setRefused(why);
    if (why) return;
    setState("applied");
    reloadGates();
  }

  async function again() {
    const { change } = proposal;
    setBusy(true);
    const { data, error, response } = await api.POST("/api/runs/{run_id}/changes/propose", {
      params: { path: { run_id: runId } },
      body: { kind: change.kind as Kind, stage: change.stage, payload: change.payload },
    });
    setBusy(false);
    if (data) setProposal(data);
    setRefused(data ? null : refusal(error, response, "The change could not be checked"));
  }

  const notNow = (
    <button
      type="button"
      className="office-btn"
      disabled={busy}
      onClick={() => {
        setState("dismissed");
        void markCard(runId, card, "dismissed");
      }}
    >
      Not now
    </button>
  );
  return (
    <ChangeFrame
      testId="talk-card"
      title={title}
      status={stale ? "Out of date" : violations.length > 0 ? "Would not pass" : "Proposed"}
      diff={proposalRows(proposal)}
      what={[
        ...(stale ? [<What key="stale" tone="warn" role="status">The run moved on since this was proposed.</What>] : []),
        ...violations.map((v) => <What key={v} tone="bad">This would not pass: {v.replace(/\.$/, "")}.</What>),
        ...(!stale && violations.length === 0 ? [<What key="ok" tone="ok">Check passed.</What>] : []),
        ...((read_by ?? []).length > 0 ? [<What key="read" tone="warn">{(read_by ?? []).join(", ")} already read the old version.</What>] : []),
        ...(!in_place ? [<What key="fork" tone="warn">Continues as a new run from {fork_from}; this one ends.</What>] : []),
        ...(refused ? [<What key="refused" tone="bad" role="alert">{refused}</What>] : []),
      ]}
      actions={
        stale ? (
          <>
            <button type="button" className="office-btn talk-yes" disabled={busy} onClick={() => void again()}>
              Ask again
            </button>
            {notNow}
          </>
        ) : (
          <>
            <button type="button" className="office-btn talk-yes" disabled={busy || violations.length > 0} onClick={() => void apply()}>
              Apply
            </button>
            {notNow}
          </>
        )
      }
      cost={proposal.cost_usd != null ? `${money(proposal.cost_usd)}${proposal.budget_left_usd != null ? ` of ${money(proposal.budget_left_usd)} left` : ""}` : null}
    />
  );
}

/** The gate's real choices, as the plain Gates page offers them. */
function choices(
  gate: Gate,
  artifacts: ArtifactFile[] | null,
  cards: ArtifactFile[],
  proposal: Proposal | null | undefined,
  changed: boolean,
  confirm: (ask: AskFor) => Promise<boolean>,
): Option[] {
  const stop = blocked(gate, changed);
  switch (gate.kind) {
    case "pick":
      if (!artifacts) return [];
      if (cards.length === 0) {
        return [
          {
            label: "Go with my pick",
            cheer: true,
            choose: (f) => {
              const choice = String(f.get("choice") ?? "").trim();
              return choice ? { kind: "pick", choice } : null;
            },
          },
        ];
      }
      return cards.map((c) => ({ label: `Pick ${c.title}`, cheer: true, choose: () => ({ kind: "pick", choice: c.title, artifact: c.path }) }));
    case "approve":
      return [
        // Waits for what it reviews, so an approval of one piece names it in the record.
        { label: "Approve", cheer: true, disabled: stop || !artifacts, choose: () => ({ kind: "approve" }) },
        ...sendBackTargets(gate).map((to) => ({ label: `Send back to ${to}`, cheer: false, choose: (): Choice => ({ kind: "send_back", to }) })),
      ];
    case "confirm":
      return [{ label: gate.publish ? "Confirm and post" : "Confirm", cheer: true, disabled: stop, choose: () => ({ kind: "confirm" }) }];
    case "budget":
      return [
        {
          label: "Add money and go on",
          cheer: true,
          choose: (f) => {
            const add = Number(f.get("add"));
            return add > 0 ? { kind: "budget", add } : null;
          },
        },
        {
          label: "Abort the run",
          cheer: false,
          choose: async () => ((await confirm(abortAsk(gate.run_name, gate.spent_usd, gate.budget_usd))) ? { kind: "abort" } : null),
        },
      ];
    case "skill": {
      if (!proposal) return [];
      const content = (f: FormData) => String(f.get("skill") ?? proposal.skill?.content ?? "");
      return [
        {
          label: "Approve",
          cheer: true,
          choose: (f) => ({ kind: "skill", proposal, approve: true, content: content(f), applyWorkflow: f.get("apply") !== null }),
        },
        {
          label: "Reject",
          cheer: false,
          choose: async (f) =>
            String(f.get("note") ?? "").trim() || (await confirm(REJECT))
              ? { kind: "skill", proposal, approve: false, content: content(f), applyWorkflow: false }
              : null,
        },
      ];
    }
    default:
      return [];
  }
}

/** What the publish stage after this gate sends out, as its connector describes it (ADR 0007):
 * shown before any choice. Fields it marks editable are fields of the answer. */
function WhatGoesOut({ preview, worldId }: { preview: PublishPreview; worldId: string }) {
  const id = useId();
  const connectors = useConnectors(worldId);
  const problems = preview.problems ?? [];
  return (
    <section className="talk-publish" aria-label="What goes out">
      <dl>
        <dt>Where</dt>
        <dd>{labelOf(connectors, preview.connector)}</dd>
        {(preview.fields ?? []).map((f) => (
          <div key={f.key}>
            <dt>{editable(f) ? <label htmlFor={`${id}-${f.key}`}>{f.label}</label> : f.label}</dt>
            <dd>
              {editable(f) ? (
                <textarea id={`${id}-${f.key}`} name={`edit:${f.key}`} rows={1} defaultValue={String(f.value ?? "")} />
              ) : (
                shown(f.value)
              )}
            </dd>
          </div>
        ))}
      </dl>
      {problems.length > 0 && (
        <div role="alert">
          <p>This will not post:</p>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

const shown = (value: unknown) =>
  value === null || value === undefined || value === ""
    ? "None"
    : typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : Array.isArray(value)
        ? value.join(", ")
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
