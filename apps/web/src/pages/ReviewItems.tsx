import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { ArtifactFile } from "../api/events";
import type { Gate } from "../api/runs";
import type { components } from "../api/schema";
import { Loading, Skeleton } from "../Skeleton";
import { ItemError, Made } from "./StagePanel";

type Item = components["schemas"]["ItemView"];

/** The verdict picked on each Item not yet approved, by key. */
export type Verdicts = Record<string, "approve" | "back">;

/** An `approve_items` gate's page (issue 502): every Item of the fan-out stage with what its
 * child made, an approve or send back on each one not yet approved, and the note a send-back
 * goes with. The radios and notes belong to the gate's form (`formId`), which posts them. */
export function ReviewItems({ gate, formId, verdicts, onVerdict }: { gate: Gate; formId: string; verdicts: Verdicts; onVerdict: (key: string, verdict: "approve" | "back") => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [files, setFiles] = useState<Record<string, ArtifactFile[]>>({});
  useEffect(() => {
    let live = true;
    const path = { run_id: gate.run_id, stage_id: gate.stage_id };
    api
      .GET("/api/runs/{run_id}", { params: { path: { run_id: gate.run_id } } })
      .then(({ data }) => {
        const found = data?.stages[gate.stage_index]?.items ?? [];
        if (!live) return;
        setItems(found);
        for (const item of found.filter((i) => i.status === "done")) {
          void api
            .GET("/api/runs/{run_id}/stages/{stage_id}/artifacts", { params: { path, query: { folder: item.key } } })
            .then(({ data: made }) => live && setFiles((f) => ({ ...f, [item.key]: made ?? [] })));
        }
      })
      .catch(() => live && setItems([]));
    return () => {
      live = false;
    };
  }, [gate.run_id, gate.stage_id, gate.stage_index, gate.opened_at]);

  if (items === null) {
    return (
      <Loading>
        <Skeleton h={240} />
      </Loading>
    );
  }
  return (
    <ul className="review-items" aria-label="Items" data-testid="review-items">
      {items.map((item) => (
        <li key={item.key} className="review-item" data-approved={item.approved || undefined}>
          <p className="review-item-head">
            <b>{item.title}</b>{" "}
            <span className={item.approved ? "chip ok" : item.status === "done" ? "chip" : "chip warn"}>{item.approved ? "approved" : item.status}</span>
            {item.child && (
              <Link to="/worlds/$worldId/runs/$runId" params={{ worldId: item.child.world_id, runId: item.child.id }}>
                Its run
              </Link>
            )}
          </p>
          {item.error && <ItemError error={item.error} />}
          {(files[item.key] ?? []).map((a) => (
            <Made key={a.path} a={a} worldId={gate.world_id} />
          ))}
          {!item.approved && (
            <>
              <fieldset className="segments review-verdict">
                <legend className="visually-hidden">Your verdict on {item.title}</legend>
                {item.status === "done" && (
                  <label>
                    <input type="radio" name={`verdict:${item.key}`} value="approve" form={formId} aria-label={`Approve ${item.title}`} onChange={() => onVerdict(item.key, "approve")} />
                    <span>Approve</span>
                  </label>
                )}
                <label>
                  <input type="radio" name={`verdict:${item.key}`} value="back" form={formId} aria-label={`Send back ${item.title}`} onChange={() => onVerdict(item.key, "back")} />
                  <span>Send back</span>
                </label>
              </fieldset>
              {verdicts[item.key] === "back" && (
                <label>
                  What to change; its run makes it again with this note
                  <textarea name={`back:${item.key}`} rows={2} required form={formId} aria-label={`What to change in ${item.title}`} />
                </label>
              )}
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
