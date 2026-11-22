import { useEffect, useState } from "react";
import { RECORD } from "../api/answer";
import { api } from "../api/client";
import { type Connectors, labelOf } from "../api/connectors";
import type { Agent, ArtifactFile } from "../api/events";
import { money, type Run } from "../api/runs";
import type { components } from "../api/schema";
import { Icon, type IconName } from "../Icon";
import { the } from "../skin";
import { AUDIO, FileLink, IMAGE, rawUrl, VIDEO } from "./Artifacts";
import { StageLook } from "./RunHero";

type Stage = Run["stages"][number];

/** What a file is, by its type or name: the glyph its tile wears and the class that tints it. */
function look(a: ArtifactFile): [IconName, string] {
  if (a.type === "publish") return ["send", ""];
  if (a.type === "video" || VIDEO.test(a.name)) return ["play", "vid"];
  if (AUDIO.test(a.name)) return ["play", "aud"];
  if (/\.(csv|tsv)$/i.test(a.name)) return ["steps", ""];
  if (/\.json$/i.test(a.name)) return ["world", ""];
  if (/\.(md|txt)$/i.test(a.name)) return ["doc", ""];
  return ["assets", ""];
}

/** A file in a few words, from what it holds: "1,240 words", "12 rows, 6 columns", "31 entries". */
export function fileLine(a: ArtifactFile, connectors: Connectors = {}): string {
  if (a.fields.deleted) return "Deleted by retention";
  if (a.type === "publish") return `Posted to ${labelOf(connectors, String(a.fields.connector ?? ""))}`;
  const kind = a.type !== "file" ? a.type : "";
  let detail = "";
  if (a.type === "video" || VIDEO.test(a.name)) detail = "Video";
  else if (AUDIO.test(a.name)) detail = "Audio";
  else if (IMAGE.test(a.name)) detail = "Image";
  else if (/\.(csv|tsv)$/i.test(a.name) && a.body) {
    const rows = a.body.trim().split("\n");
    const cols = rows[0]!.split(/\.tsv$/i.test(a.name) ? "\t" : ",").length;
    detail = `${rows.length - 1} rows, ${cols} columns`;
  } else if (/\.json$/i.test(a.name) && a.body) {
    try {
      const data: unknown = JSON.parse(a.body);
      const n = Array.isArray(data) ? data.length : data && typeof data === "object" ? Object.keys(data).length : null;
      if (n !== null) detail = `${n.toLocaleString()} ${Array.isArray(data) ? "entries" : "fields"}`;
    } catch {
      detail = "";
    }
  } else if (/\.(md|txt)$/i.test(a.name) && a.body) {
    detail = `${a.body.split(/\s+/).filter(Boolean).length.toLocaleString()} words`;
  }
  return [kind.toLowerCase() === detail.toLowerCase() ? "" : kind, detail].filter(Boolean).join(", ") || a.name.split(".").pop()!.toUpperCase();
}

/** Every file the run made, each with a preview by its type and the look of the stage that made
 * it. A tap opens it; a post opens where it went. */
export function MadeInRun(p: { run: Run; artifacts: ArtifactFile[]; worldId: string; connectors: Connectors }) {
  const files = p.artifacts.filter((a) => !RECORD.has(a.type));
  return (
    <section className="panel side-card" aria-label="Made in this run" data-testid="made">
      <div className="side-head">
        <Icon name="assets" />
        <h3>Made in this run</h3>
        <span>
          {files.length} {files.length === 1 ? "file" : "files"}
        </span>
      </div>
      {files.length === 0 ? (
        <p className="faint">Nothing made yet. Each file shows here as soon as a stage makes it.</p>
      ) : (
        <ul className="made-list">
          {files.map((a) => {
            const stage = p.run.stages.find((s) => s.id === a.stage);
            const [glyph, tint] = look(a);
            const inner = (
              <>
                <span className={`fi ${tint}`} aria-hidden="true">
                  {IMAGE.test(a.name) && !a.fields.deleted ? <img src={rawUrl(p.worldId, a)} alt="" loading="lazy" /> : <Icon name={glyph} />}
                </span>
                <span className="made-text">
                  <b>{a.name}</b>
                  <span>{fileLine(a, p.connectors)}</span>
                </span>
                {stage && (
                  <span className="made-by" title={`Made by ${stage.name}`}>
                    <StageLook stage={stage} connector={p.connectors[stage.calls ?? ""]} />
                    <span className="visually-hidden">, made by {stage.name}</span>
                  </span>
                )}
              </>
            );
            const link = a.type === "publish" ? Object.values((a.fields.links as Record<string, string> | undefined) ?? {})[0] : undefined;
            return (
              <li key={a.path} data-testid="made-file">
                {a.fields.deleted ? (
                  <span className="made-file">{inner}</span>
                ) : a.type === "publish" ? (
                  link ? (
                    <a className="made-file" href={link} target="_blank" rel="noreferrer">
                      {inner}
                    </a>
                  ) : (
                    <span className="made-file">{inner}</span>
                  )
                ) : (
                  <FileLink a={a} worldId={p.worldId} className="made-file">
                    {inner}
                  </FileLink>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** What each stage's agents cost: a run's own agents by their stage, a child run's (a workflow
 * stage, a fan-out's items) by the stage that started it. `below` is what runs further down
 * spent, so the rows always add up to every agent the run's family had. */
export function spendByStage(run: Run, agents: Agent[]): { rows: { stage: Stage; usd: number }[]; below: number } {
  const owner = new Map<string, Stage>();
  for (const s of run.stages) {
    if (s.child) owner.set(s.child.id, s);
    for (const i of s.items ?? []) if (i.child) owner.set(i.child.id, s);
  }
  const by = new Map<string, number>();
  let below = 0;
  for (const a of agents) {
    const s = a.run_id === run.id ? run.stages.find((x) => x.id === a.stage_id) : owner.get(a.run_id);
    if (s) by.set(s.id, (by.get(s.id) ?? 0) + a.cost_usd);
    else below += a.cost_usd;
  }
  return { rows: run.stages.filter((s) => by.get(s.id)).map((s) => ({ stage: s, usd: by.get(s.id)! })), below };
}

/** Part 5: what the run spent against its budget, what is left before the pause, and what each
 * stage spent. `compare` is the one-agent comparison, shown once asked for from the run menu. */
export function SpendByStage(p: { run: Run; agents: Agent[]; connectors: Connectors; asked: number; compare?: string }) {
  const { run } = p;
  const budget = run.budget_usd;
  const spent = run.spent_usd ?? 0;
  let note = "";
  if (budget != null && run.over_budget) note = `Every agent stopped at ${money(budget)}. Add money to go on, or abort.`;
  else if (budget != null) note = `${money(Math.max(0, budget - spent))} left before ${the("budget")} pauses the run.`;
  const { rows, below } = spendByStage(run, p.agents);
  const top = Math.max(below, ...rows.map((r) => r.usd));
  const bar = (usd: number) => (
    <span className="bar" aria-hidden="true">
      <i style={{ width: `${top ? (usd / top) * 100 : 0}%` }} />
    </span>
  );
  return (
    <section className="panel side-card spend" aria-label="Spend" data-testid="measure">
      <div className="side-head">
        <Icon name="coin" />
        <h3>Spend by step</h3>
        <span className="num" data-testid="budget">
          {budget != null ? `${money(spent)} of ${money(budget)}` : `${money(spent)} spent, no budget set`}
        </span>
      </div>
      {budget != null && (
        <meter min={0} max={budget} low={0.8 * budget} high={0.8 * budget} optimum={0} value={Math.min(spent, budget)} aria-label="Budget spent" />
      )}
      {note && (
        <span className={run.over_budget ? "stage-error" : budget != null && spent >= 0.8 * budget ? "warn" : "faint"} data-testid="budget-line">
          {note}
        </span>
      )}
      {rows.length > 0 && (
        <ul className="spend-rows" data-testid="spend-rows">
          {rows.map(({ stage, usd }) => (
            <li key={stage.id} data-testid="spend-row">
              <StageLook stage={stage} connector={p.connectors[stage.calls ?? ""]} />
              <span>{stage.name}</span>
              <em>{money(usd)}</em>
              {bar(usd)}
            </li>
          ))}
          {below > 0 && (
            <li data-testid="spend-row">
              <Icon name="workflows" />
              <span>Runs further down</span>
              <em>{money(below)}</em>
              {bar(below)}
            </li>
          )}
        </ul>
      )}
      {p.asked > 0 && (
        <span className="faint" data-testid="assistant-spend">
          Assistant {money(p.asked)}, from the world's budget, not this run's.
        </span>
      )}
      {run.baseline ? (
        <span className="faint" data-testid="baseline">
          This is a one-agent baseline, with no graph, to measure the swarm against.
        </span>
      ) : (
        p.compare && (
          <p className="faint" data-testid="baseline">
            {p.compare}
          </p>
        )
      )}
    </section>
  );
}

/** "2 Tray things from this run need review": what an installed package's own app holds from this
 * run's family (its Review Feed), opening that app. Nothing when there is none. */
export function FeedLine({ worldId, runId }: { worldId: string; runId: string }) {
  const [feeds, setFeeds] = useState<components["schemas"]["RunFeed"][]>([]);
  useEffect(() => {
    api.GET("/api/worlds/{world_id}/runs/{run_id}/review-feed", { params: { path: { world_id: worldId, run_id: runId } } }).then(({ data }) => setFeeds(data ?? []));
  }, [worldId, runId]);
  return feeds.map((f) => (
    <p key={f.label} className="feed-line" data-testid="feed-line">
      {f.count} {f.label} from this run need review.
      {f.open_url && (
        <a href={f.open_url} target="_blank" rel="noreferrer">
          Open
        </a>
      )}
    </p>
  ));
}
