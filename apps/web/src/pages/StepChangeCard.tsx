import { useEffect, useId, useState } from "react";

import { api } from "../api/client";
import { refusal, type Run } from "../api/runs";
import type { components } from "../api/schema";
import { ChangeFrame, type DiffRow, What } from "../Chat";
import { type AppliedChange, AppliedLine, costLine } from "./ChangeCard";

type Stage = Run["stages"][number];
type Proposal = components["schemas"]["ChangeProposal"];
export type StepKind = "rerun" | "skip";

/** What an applied change did, as the Timeline and the change list say it. */
export const changed = (kind: string) => ({ rerun: "Re-run", follow_up: "Changed", skip: "Skipped", message: "Messaged" })[kind] ?? "Edited";
export const changedStage = (c: AppliedChange) =>
  c.kind === "rerun"
    ? `Re-run from ${c.stage_name}`
    : c.kind === "follow_up"
      ? `Asked ${c.stage_name} for a change`
    : c.kind === "artifact_edit"
      ? `Edited ${String(c.payload.path)} of ${c.stage_name}`
      : `${changed(c.kind)} ${c.stage_name}`;

const names = new Intl.ListFormat("en-GB", { type: "conjunction" });

/** A Rerun or a Skip as a change card (ADR 0027): the steps it resets or passes over, what a
 * Rerun costs again against the budget left, what the check would refuse, and Apply only once it
 * passes. On a run that has ended it continues as a new run, a Fork. Nothing changes until Apply. `proposed` is one made already (by the Run Assistant):
 * shown as it was, with its note, so it goes stale when the run moved. */
export function StepChangeCard({
  run,
  stage,
  kind,
  onClose,
  proposed,
  onApplied,
}: {
  run: Run;
  stage: Stage;
  kind: StepKind;
  onClose: () => void;
  proposed?: Proposal;
  onApplied?: (change: AppliedChange) => void;
}) {
  const [note, setNote] = useState(() => String(proposed?.change.payload?.note ?? ""));
  const noteId = useId();
  const [editing, setEditing] = useState(kind === "rerun" && !proposed);
  const [proposal, setProposal] = useState<Proposal | null>(proposed ?? null);
  const [problem, setProblem] = useState<string | null>(null);
  const [applied, setApplied] = useState<AppliedChange | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const [asked, setAsked] = useState(0);

  useEffect(() => {
    if (proposed && asked === 0) return; // as proposed, until asked again
    let stale = false; // a slower answer for an earlier ask must not land
    (async () => {
      const { data, error, response } = await api.POST("/api/runs/{run_id}/changes/propose", {
        params: { path: { run_id: run.id } },
        body: { kind, stage: stage.id },
      });
      if (stale) return;
      setRefused(null);
      setProposal(data ?? null);
      setProblem(refusal(error, response, "The change could not be checked"));
    })();
    return () => {
      stale = true;
    };
  }, [run.id, stage.id, kind, asked, proposed]);

  async function apply() {
    if (!proposal) return;
    const { data, error, response } = await api.POST("/api/runs/{run_id}/changes", {
      params: { path: { run_id: run.id } },
      body: { kind, stage: stage.id, payload: kind === "rerun" ? { note: note.trim() } : {}, revision: proposal.revision },
    });
    if (data) {
      onApplied?.(data);
      return setApplied(data);
    }
    setRefused(refusal(error, response, "The change was refused"));
  }

  const title = kind === "rerun" ? `Re-run from ${stage.name}` : `Skip ${stage.name}`;
  const before = proposal?.before ?? {};
  const after = proposal?.after ?? {};
  const stale = !applied && !!proposal && (run.revision ?? 0) > proposal.revision;
  const problems = [...(problem ? [problem] : []), ...(proposal?.violations ?? [])];
  const ready = !!proposal && problems.length === 0 && !stale;
  const actions = stale ? (
    <>
      <button type="button" className="primary" onClick={() => setAsked((n) => n + 1)}>
        Ask again
      </button>
      <button type="button" className="quiet" onClick={onClose}>
        Not now
      </button>
    </>
  ) : (
    <>
      <button type="button" className="primary" disabled={!ready} onClick={apply}>
        Apply
      </button>
      {kind === "rerun" && (
        <button type="button" aria-pressed={editing} onClick={() => setEditing(!editing)}>
          Edit note
        </button>
      )}
      <button type="button" className="quiet" onClick={onClose}>
        Not now
      </button>
    </>
  );
  if (applied) return <AppliedLine run={run} title={title} applied={applied} />;
  const shownNote = note.trim();
  const diff: DiffRow[] = proposal
    ? [
        ...Object.keys(before).flatMap((k) => [
          { sign: "-" as const, key: k, text: String(before[k]) },
          { sign: "+" as const, key: k, text: String(after[k]) },
        ]),
        ...(kind === "rerun" && shownNote ? [{ sign: "+" as const, key: "note", text: shownNote }] : []),
      ]
    : [];
  return (
    <ChangeFrame
      title={title}
      status={stale ? "Out of date" : problems.length > 0 ? "Would not pass" : "Proposed"}
      diff={diff}
      what={[
        ...(stale ? [<What key="stale" tone="warn" role="status">The run moved on since this was proposed.</What>] : []),
        ...problems.map((p) => (
          <What key={p} tone="bad">
            This would not pass: {p.replace(/\.$/, "")}.
          </What>
        )),
        ...(ready ? [<What key="ok" tone="ok">Check passed.{proposal?.in_place ? " Runs in place." : ""}</What>] : []),
        ...(proposal && !proposal.in_place && !stale ? [<What key="fork" tone="warn">Continues as a new run from {proposal.fork_from}.</What>] : []),
        ...(proposal && kind === "rerun" && Object.keys(before).length > 0 ? [<What key="resets">Resets {names.format(Object.keys(before))}.</What>] : []),
        ...(refused
          ? [
              <What key="refused" tone="bad" role="alert">
                {refused}
              </What>,
            ]
          : []),
      ]}
      actions={actions}
      cost={proposal && kind === "rerun" ? costLine(proposal) : null}
    >
      {editing && kind === "rerun" && !stale && (
        <>
          <label htmlFor={noteId}>Note for {stage.name}</label>
          <textarea id={noteId} value={note} rows={3} placeholder="What to do differently" onChange={(e) => setNote(e.target.value)} />
        </>
      )}
    </ChangeFrame>
  );
}
