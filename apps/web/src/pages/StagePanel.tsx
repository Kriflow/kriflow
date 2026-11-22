import { Link } from "@tanstack/react-router";
import { type ReactNode, useEffect, useState } from "react";

import { api } from "../api/client";
import { type Agent, type ArtifactFile, type LogEntry, outcomeOf, type Verdict } from "../api/events";
import { type Connector, type Connectors, labelOf } from "../api/connectors";
import { choosable, RECORD, underReview } from "../api/answer";
import { type Call, clock, decisionLine, money, plainError, type Run } from "../api/runs";
import { Icon } from "../Icon";
import { Media } from "../Media";
import type { components } from "../api/schema";
import type { Task } from "../api/tasks";
import { AgentCards, describe, LiveLog, usd } from "./AgentLog";
import { ArtifactView, AUDIO, cardLine, Chips, FileLink, fileUrl, IMAGE, Markdown, mdBlocks, rawUrl, VIDEO } from "./Artifacts";
import { EDITABLE } from "./ChangeCard";
import { PublishValue } from "./Connectors";
import { SaveAsTemplate, StageOutput } from "./StageOutput";
import { Whiteboard } from "./Whiteboard";
import { StageLook } from "./RunHero";
import { ActivityViews } from "./Transcript";
import { MessageBox, messageable } from "./Message";
import { AttemptPin, AttemptTabs, attemptLog, attemptSeconds, FollowUpBox, followable, useAttemptFiles, useAttempts } from "./Attempts";
import { cap } from "../skin";

type Stage = Run["stages"][number];
type Proposal = components["schemas"]["Proposal"];

/** Seconds a stage took, or has taken so far. */
export function stageSeconds(s: Stage, now = Date.now()): number | null {
  if (!s.started_at) return null;
  const end = s.ended_at ? Date.parse(s.ended_at) : now;
  return Math.max(0, (end - Date.parse(s.started_at)) / 1000);
}

/** A step's state in a word or its duration: "2m 14s", "running", "waiting". */
export function stepMeta(s: Stage, queued = false): string {
  const took = stageSeconds(s);
  switch (s.status) {
    case "done":
      return took === null ? "done" : clock(took);
    case "running":
      if (s.waiting) return s.waiting.split(":", 1)[0] ?? s.waiting;
      return s.network ? "waiting for the network" : "running";
    case "waiting":
      return "waiting";
    case "failed":
      return took === null ? "failed" : `failed after ${clock(took)}`;
    case "skipped":
      return "skipped";
    default:
      return queued ? "starting" : "not yet";
  }
}

/** The media a file is, by its type or name: video, audio or image; undefined for anything else. */
const mediaOf = (a: ArtifactFile) =>
  a.type === "video" || VIDEO.test(a.name) ? "video" : AUDIO.test(a.name) ? "audio" : IMAGE.test(a.name) ? "image" : undefined;

/** What kind of step this is, as the panel names it and picks its view by (ADR 0025): a connector
 * step by its connector's Capability or kind, else by the media it made, else by the media its
 * Manifest says it makes. Never by the connector's name. */
export function kindOf(s: Stage, made: ArtifactFile[] = [], c?: Connector): string {
  if (s.type !== "connector") return s.type;
  if (c?.capabilities.includes("build_session")) return "build";
  if (c?.kind === "publish" || made.some((a) => a.type === "publish")) return "publish";
  const media = [...made.map(mediaOf), ...(c?.outputs ?? []).map((o) => o.media_type.split("/")[0])];
  return media.find((m) => m === "video" || m === "audio") ?? "connector";
}

/** The run's work: what its stages made, not what it wrote about itself or what it posted. */
export const work = (artifacts: ArtifactFile[]) =>
  artifacts.filter((a) => !RECORD.has(a.type) && a.type !== "publish" && !a.fields.deleted);

/** The stage whose output is the run's result: the latest stage that made anything. */
export function resultStage(run: Run, artifacts: ArtifactFile[]): Stage | undefined {
  const made = new Set(work(artifacts).map((a) => a.stage));
  return run.stages.filter((s) => made.has(s.id)).at(-1);
}

/** The run's primary output, from the stage that made it: a picture or sound over a list over a
 * document over a plain file. */
function primary(artifacts: ArtifactFile[]): ArtifactFile[] {
  const media = artifacts.filter((a) => a.type === "video" || VIDEO.test(a.name) || IMAGE.test(a.name) || AUDIO.test(a.name));
  if (media.length) return [media.at(-1)!];
  const items = artifacts.filter((a) => a.type !== "file");
  if (items.length > 1) return items;
  return [items[0] ?? artifacts.at(-1)!];
}

/** The one file to open from a finished run: its result when that is a single file, else the
 * last of its result stage's files. */
export function mainArtifact(run: Run, artifacts: ArtifactFile[]): ArtifactFile | undefined {
  const from = resultStage(run, artifacts);
  return from ? primary(work(artifacts).filter((a) => a.stage === from.id)).at(-1) : undefined;
}

/** A file's text without the YAML frontmatter on top. */
const body = (text: string) => text.replace(/^---\n[\s\S]*?\n---\n/, "");

/** The first blocks of a document, enough to recognise it: whole blocks, so a table or a code
 * block is never cut into raw lines. */
function firstLines(text: string, blocks = 5): string {
  return mdBlocks(body(text)).slice(0, blocks).join("\n\n");
}

/** One artifact, shown in place: a player, the picture, the first lines of a document. */
/** A failed Item's error, a Blender traceback full of long paths as often as not: folded to a
 * few wrapped lines, with Show all, so it never widens the page on a phone. */
export function ItemError({ error }: { error: string }) {
  const [all, setAll] = useState(false);
  const long = error.length > 240 || error.split("\n").length > 4;
  return (
    <>
      <pre className={long && !all ? "item-error folded" : "item-error"} data-testid="item-error">
        {error}
      </pre>
      {long && !all && (
        <button type="button" className="link" onClick={() => setAll(true)}>
          Show all
        </button>
      )}
    </>
  );
}

export function Made({ a, worldId }: { a: ArtifactFile; worldId: string }) {
  const url = rawUrl(worldId, a);
  if (a.type === "video" || VIDEO.test(a.name)) {
    return <Media video src={url} alt={a.title} testId="player" />;
  }
  if (AUDIO.test(a.name)) return <audio controls preload="metadata" src={url} data-testid="audio" />;
  if (IMAGE.test(a.name)) return <Media src={url} alt={a.title} />;
  if (a.name === "timings.json") return <Takes timings={a} worldId={worldId} />;
  if (/\.(md|txt)$/i.test(a.name)) {
    return (
      <div className="document" data-testid="document">
        <Markdown text={firstLines(a.body)} />
        <FileLink a={a} worldId={worldId} className="button">
          Open
        </FileLink>
      </div>
    );
  }
  return (
    <FileLink a={a} worldId={worldId}>
      Open {a.name}
    </FileLink>
  );
}

/** Typed items of any type as cards: title, its one line, the fields it was scored on. */
function ItemCards({ items }: { items: ArtifactFile[] }) {
  const sorted = [...items].sort((a, b) => Number(a.fields.rank ?? 1e9) - Number(b.fields.rank ?? 1e9));
  return (
    <ul className="cards" data-testid="items">
      {sorted.map((a) => (
        <li key={a.path} className="card" data-testid="item">
          <span className="faint">
            {a.type}
            {a.fields.rank != null ? ` · #${String(a.fields.rank)} of ${items.length}` : ""}
          </span>
          <b>{a.title}</b>
          <span>{cardLine(a)}</span>
          <Chips a={a} />
        </li>
      ))}
    </ul>
  );
}

/** Part 4: the run's latest primary output, in place, above every control. */
export function Result({ run, artifacts, worldId }: { run: Run; artifacts: ArtifactFile[]; worldId: string }) {
  const from = resultStage(run, artifacts);
  const shown = from ? primary(work(artifacts).filter((a) => a.stage === from.id)) : [];
  const one = shown.length === 1 ? shown[0]! : null;
  return (
    <section className={`result${one ? " result-one" : ""}`} aria-label="Result" data-testid="result">
      {!from ? (
        <p className="faint">Nothing made yet. What the run makes shows here as soon as a stage makes it.</p>
      ) : one ? (
        <>
          <Made a={one} worldId={worldId} />
          <dl className="facts">
            <dt>What</dt>
            <dd>{one.title}</dd>
            <dt>Made by</dt>
            <dd>
              {from.name}, step {from.index + 1} of {run.stages.length}
            </dd>
            {!Number.isNaN(Date.parse(one.created)) && (
              <>
                <dt>When</dt>
                <dd>{new Date(one.created).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</dd>
              </>
            )}
            <dt>File</dt>
            <dd>
              <FileLink a={one} worldId={worldId}>
                {one.name}
              </FileLink>
            </dd>
          </dl>
        </>
      ) : (
        <>
          <p className="faint">
            {shown.length} {shown[0]!.type}s from {from.name}, step {from.index + 1} of {run.stages.length}
          </p>
          <ItemCards items={shown} />
        </>
      )}
    </section>
  );
}

type Take = {
  scene: string;
  file: string;
  duration: number;
  lines?: { text: string; joined_next?: boolean }[];
};

type Timings = {
  engine: string;
  takes: Take[];
  /** The voice that spoke, a catalog name or an engine's own; null for the engine's default. */
  voice: string | null;
  /** The voice the stage or the script asked for, when it differs from the one that spoke. */
  chosen: string | null;
  warnings: string[];
};

/** A timings.json of takes: one per scene, voiced by one engine in one voice. */
function takes(a: ArtifactFile): Timings | null {
  try {
    const t = JSON.parse(a.body) as Partial<Timings>;
    return {
      engine: t.engine ?? "an unnamed engine",
      takes: t.takes ?? [],
      voice: t.voice ?? null,
      chosen: t.chosen ?? null,
      warnings: t.warnings ?? [],
    };
  } catch {
    return null;
  }
}

/** An engine as its address names it, without the scheme. */
const engineName = (engine: string) => engine.replace(/^https?:\/\//, "");

/** One row per scene: its take, its length, the engine, and a warning where a join has no
 * silence to cut at. */
export function Takes({ timings, worldId }: { timings: ArtifactFile; worldId: string }) {
  const t = takes(timings);
  if (!t) return <p className="faint">timings.json could not be read.</p>;
  const total = t.takes.reduce((sum, k) => sum + k.duration, 0);
  const folder = timings.path.split("/").slice(0, -1).join("/");
  return (
    <div className="takes" data-testid="takes">
      <p>
        <b>
          {t.takes.length} {t.takes.length === 1 ? "scene" : "scenes"} · {total.toFixed(1)} s of narration
        </b>{" "}
        · <span data-testid="voice">{t.voice ? `voiced by ${t.voice}` : "the engine's default voice"}</span>
        {t.chosen && t.chosen !== t.voice && (
          <span className="faint"> (chosen {t.chosen}, which {engineName(t.engine)} cannot speak)</span>
        )}
      </p>
      {t.warnings.map((w) => (
        <p key={w} className="warn" role="note">
          {w}
        </p>
      ))}
      <ul className="take-list">
        {t.takes.map((k) => {
          const joined = (k.lines ?? []).flatMap((l, i) => (l.joined_next ? [i + 1] : []));
          return (
            <li key={k.scene} data-testid="take">
              <b>{k.scene}</b>
              <audio controls preload="none" src={fileUrl(worldId, `${folder}/${k.file}`)} />
              <span className="num">{k.duration.toFixed(1)} s</span>
              <span className="faint">{engineName(t.engine)}</span>
              {joined.map((i) => (
                <span key={i} className="warn" role="note">
                  Line {i} runs into line {i + 1} with no silence to cut at.
                </span>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** "Rendered 412 of 870 frames" from the render's progress message. */
function frames(call: Call | null | undefined): { label: string; value: number } | null {
  if (!call) return null;
  const m = /(rendered|encoded) (\d+)\/(\d+) frames/i.exec(call.message ?? "");
  const value = call.status === "done" ? 1 : (call.progress ?? 0);
  if (call.status === "done") return { label: "Rendered every frame", value };
  if (!m) return { label: call.message ? `${call.message[0]!.toUpperCase()}${call.message.slice(1)}` : "Starting the render", value };
  const word = m[1]!.toLowerCase() === "rendered" ? "Rendered" : "Encoded";
  return { label: `${word} ${m[2]} of ${m[3]} frames`, value };
}

function RenderProgress({ call }: { call: Call | null | undefined }) {
  const f = frames(call);
  if (!f) return <p className="faint">The render starts once the stage before it is done.</p>;
  return (
    <div className="progress" data-testid="render-progress">
      <span>{f.label}</span>
      <meter min={0} max={1} value={f.value} aria-label="Render progress" />
    </div>
  );
}

/** The video a render made, if any. */
function Video({ made, worldId, shown }: { made: ArtifactFile[]; worldId: string; shown?: boolean }) {
  const v = made.find((a) => a.type === "video" || VIDEO.test(a.name));
  if (!v) return null;
  if (shown) return <p className="faint">It made {v.name}, the result shown above.</p>;
  return <Made a={v} worldId={worldId} />;
}

function BuildPanel(props: {
  call: Call | null | undefined;
  log: LogEntry[];
  agents: Agent[];
  made: ArtifactFile[];
  worldId: string;
  shown?: boolean;
}) {
  const { call, log, agents, made, worldId, shown } = props;
  const builder = agents.find((a) => a.name === "builder");
  const steps = log.filter((e) => e.agent === "builder" && e.kind !== "session");
  const project = typeof call?.request.project === "string" ? call.request.project : null;
  const [copied, setCopied] = useState(false);
  return (
    <>
      <section className="phase" aria-label="Writing the project" data-testid="build-log">
        <div className="row">
          <h3>Writing the project</h3>
          {builder && <span className="faint">{usd(builder.cost_usd)}</span>}
        </div>
        {call?.template && (
          <p className="faint" data-testid="template">
            Started from the Template <b>{call.template.name}</b>
          </p>
        )}
        {steps.length === 0 ? (
          <p className="faint">The builder has not started. It writes the project, then the render starts.</p>
        ) : (
          <ol className="log">
            {steps.map((e) => (
              <li key={e.seq} className={`log-${e.kind}`}>
                {describe(e)}
              </li>
            ))}
          </ol>
        )}
      </section>
      <section className="phase" aria-label="Rendering">
        <h3>Rendering</h3>
        <RenderProgress call={call} />
      </section>
      <Video made={made} worldId={worldId} shown={shown} />
      {project && (
        <p className="project">
          Project folder <code data-testid="project">{project}</code>{" "}
          <button
            type="button"
            className="link"
            onClick={() => void navigator.clipboard?.writeText(project).then(() => setCopied(true))}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </p>
      )}
    </>
  );
}

function PublishPanel({ made, call, connectors }: { made: ArtifactFile[]; call: Call | null | undefined; connectors: Connectors }) {
  const post = made.find((a) => a.type === "publish");
  if (!post) {
    return <p className="faint">Nothing has gone out from this stage. It posts what the gate before it confirmed.</p>;
  }
  const f = post.fields;
  const outcome = outcomeOf(f);
  const links = Object.values((f.links as Record<string, string> | undefined) ?? {});
  const where = labelOf(connectors, String(f.connector ?? call?.connector ?? ""));
  // What went out, as its connector described it; a post from before #127 kept fixed fields.
  const sent: components["schemas"]["PublishField"][] = Array.isArray(f.sent)
    ? (f.sent as components["schemas"]["PublishField"][])
    : [
        { key: "title", label: "Title", value: f.title ?? post.title, editable: false, hint: "" },
        { key: "visibility", label: "Visibility", value: f.visibility, editable: false, hint: "" },
        { key: "schedule", label: "When", value: f.schedule ?? f.created ?? post.created, editable: false, hint: "" },
      ];
  return (
    <div className="published" data-testid="publish">
      <dl>
        <dt>Where</dt>
        <dd>{where}</dd>
        {sent.map((s) => (
          <div key={s.key} className="publish-field">
            <dt>{s.label}</dt>
            <dd>
              <PublishValue value={s.value} hint={s.hint} />
            </dd>
          </div>
        ))}
        {outcome && (
          <>
            <dt>Outcome</dt>
            <dd data-testid="outcome">
              {outcome.line} as of {outcome.day}
            </dd>
          </>
        )}
      </dl>
      {links[0] && (
        <a href={links[0]} target="_blank" rel="noreferrer" className="button">
          Open the post
        </a>
      )}
    </div>
  );
}

function RetroPanel({ run, stage }: { run: Run; stage: Stage }) {
  const [proposal, setProposal] = useState<Proposal | null | undefined>(undefined);
  useEffect(() => {
    api
      .GET("/api/worlds/{world_id}/proposals", { params: { path: { world_id: run.world_id } } })
      .then(({ data }) => setProposal((data ?? []).filter((p) => p.run_id === run.id).at(-1) ?? null))
      .catch(() => setProposal(null));
  }, [run.world_id, run.id, stage.status]);
  if (proposal === undefined) return <p role="status">Loading the proposal...</p>;
  if (proposal === null) {
    return (
      <p className="faint" data-testid="retro">
        {stage.status === "done" ? "The retro read this run and proposed no change." : "The retro reads the run once every other stage is done."}
      </p>
    );
  }
  const outcome = { pending: "Waiting for you to approve or reject it.", approved: "You approved it.", rejected: "You rejected it." }[
    proposal.status
  ];
  return (
    <div data-testid="retro">
      {proposal.skill && (
        <p>
          <b>
            {proposal.skill.action === "new" ? "New skill" : proposal.skill.action === "changed" ? "Change skill" : "Retire skill"}{" "}
            {proposal.skill.name}
          </b>
          : {proposal.skill.summary}
        </p>
      )}
      {proposal.workflow && <p>Workflow change: {proposal.workflow.summary}</p>}
      <p>
        {outcome}
        {proposal.note ? ` Your note: ${proposal.note}` : ""}
      </p>
    </div>
  );
}

/** The artifacts the earlier steps made, oldest first. */
const before = (run: Run, stage: Stage, artifacts: ArtifactFile[]) => {
  const earlier = new Set(run.stages.filter((s) => s.index < stage.index).map((s) => s.id));
  return artifacts.filter((a) => earlier.has(a.stage));
};

/** What a gate was about: the artifact its decision names, else what an approve gate reviews. */
export function reviewed(run: Run, stage: Stage, artifacts: ArtifactFile[]): ArtifactFile[] {
  const named = (run.decisions ?? []).filter((d) => d.stage_index === stage.index && d.artifact).map((d) => d.artifact);
  const picked = artifacts.filter((a) => named.includes(a.path));
  if (picked.length) return picked.slice(-1);
  return (run.decisions ?? []).some((d) => d.stage_index === stage.index && d.kind === "approve") || stage.status === "waiting"
    ? underReview(before(run, stage, artifacts))
    : [];
}

function GatePanel({ run, stage, open, artifacts, worldId }: { run: Run; stage: Stage; open: boolean; artifacts: ArtifactFile[]; worldId: string }) {
  const decisions = (run.decisions ?? []).filter((d) => d.stage_index === stage.index);
  if (open) return null; // the open gate leads the panel, its question and artifact included
  const about = decisions.length ? reviewed(run, stage, artifacts) : [];
  return (
    <div data-testid="gate-panel">
      {stage.ask && <p className="ask">{stage.ask}</p>}
      {about.length > 0 && <ArtifactView artifacts={about} worldId={worldId} />}
      {decisions.map((d) => (
        <p key={d.id} data-testid="decision">
          {decisionLine(d)}
        </p>
      ))}
      {decisions.length === 0 && stage.status !== "waiting" && <p className="faint">Not reached yet.</p>}
    </div>
  );
}

/** A connector step's inputs: each named artifact, the latest an earlier step made, and that step. */
export function inputsOf(run: Run, stage: Stage, artifacts: ArtifactFile[]) {
  const earlier = before(run, stage, artifacts);
  return (stage.inputs ?? []).map((name) => {
    const a = earlier.filter((x) => x.name === name || x.type === name).at(-1);
    return { name, a, from: a && run.stages.find((s) => s.id === a.stage) };
  });
}

function StepInputs({ run, stage, artifacts, worldId }: { run: Run; stage: Stage; artifacts: ArtifactFile[]; worldId: string }) {
  const ins = inputsOf(run, stage, artifacts);
  if (!ins.length) return null;
  return (
    <section className="phase" aria-label="In" data-testid="inputs">
      <h3>In</h3>
      <ul className="files">
        {ins.map(({ name, a, from }) => (
          <li key={name} data-testid="input">
            {a && from ? (
              <>
                <FileLink a={a} worldId={worldId}>
                  {a.name}
                </FileLink>{" "}
                <span className="faint">
                  from{" "}
                  <Link to="/worlds/$worldId/runs/$runId" params={{ worldId, runId: run.id }} search={{ stage: from.id }}>
                    step {from.index + 1}, {from.name}
                  </Link>
                </span>
              </>
            ) : (
              <>
                <code>{name}</code> <span className="faint">not made by an earlier step yet</span>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Every file a step made, records included, folded away; a text one can be edited. */
function AllFiles({ made, worldId, onEdit }: { made: ArtifactFile[]; worldId: string; onEdit?: (a: ArtifactFile) => void }) {
  if (!made.length) return null;
  return (
    <details className="technical" data-testid="all-files">
      <summary>All files ({made.length})</summary>
      <ul className="files">
        {made.map((a) => (
          <li key={a.path}>
            {a.fields.deleted ? (
              <span className="faint">{a.name}, deleted by retention</span>
            ) : (
              <FileLink a={a} worldId={worldId}>
                {a.name}
              </FileLink>
            )}{" "}
            <span className="faint">{a.type}</span>
            {onEdit && a.type !== "decision" && !a.fields.deleted && EDITABLE.test(a.name) && (
              <>
                {" "}
                <button type="button" aria-label={`Edit file ${a.name}`} onClick={() => onEdit(a)}>
                  Edit file
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The raw call and its answer, kept for when something needs checking. */
function TechnicalDetails({ call }: { call: Call | null | undefined }) {
  return (
    <details className="technical">
      <summary>Technical details</summary>
      {call === undefined ? (
        <p role="status">Loading the call...</p>
      ) : call === null ? (
        <p className="faint" data-testid="call">
          No call yet. It is recorded the moment this stage starts.
        </p>
      ) : (
        <div className="call-json" data-testid="call">
          <p className="faint">
            {call.connector} · <code>{call.tool}</code> · {call.status} · attempt {call.attempts}
            {call.polls ? ` · polled ${call.polls} times` : ""}
          </p>
          <div>
            <h4>Request</h4>
            <pre data-testid="call-request">{JSON.stringify(call.request, null, 2)}</pre>
          </div>
          <div>
            <h4>Response</h4>
            <pre data-testid="call-response">{call.response ? JSON.stringify(call.response, null, 2) : "Waiting for an answer."}</pre>
          </div>
        </div>
      )}
    </details>
  );
}

export type PanelProps = {
  run: Run;
  stage: Stage;
  worldId: string;
  made: ArtifactFile[];
  /** Every artifact of the run, for a step's inputs and what a gate reviewed. */
  artifacts: ArtifactFile[];
  agents: Agent[];
  log: LogEntry[];
  tasks: Task[];
  verdicts: Verdict[];
  call: Call | null | undefined;
  /** The world's connectors by name, which say what a connector step is. */
  connectors: Connectors;
  onTaskAdded: (task: Task) => void;
  /** What the panel leads with, above its tabs: a change being made, or the run's result. */
  lead?: ReactNode;
  /** The open gate's question and answer, or what you just decided: a gate's Decision tab, or
   * above the tabs of a stage a budget gate stopped. */
  decision?: ReactNode;
  /** Opens another stage: the header's previous and next. */
  onStep?: (id: string) => void;
  /** The gate's answer is the lead, so the gate panel does not repeat its question. */
  answering?: boolean;
  /** This step made the result shown above, so its player is not repeated here. */
  resultShown?: boolean;
  /** Opens an Artifact Edit of a text file the step made; only once it has finished. */
  onEditFile?: (a: ArtifactFile) => void;
  /** A failed step's ways on: re-run it (its check card first), or ask the run why. */
  onRetry?: () => void;
  onAsk?: () => void;
};

/** A step that stopped: the warning glyph and one plain sentence, the technical text behind
 * Details, then the ways on. */
function Failure({ error, onRetry, onAsk }: { error: string; onRetry?: () => void; onAsk?: () => void }) {
  return (
    <div className="stage-failure" role="alert" data-testid="failure">
      <p className="failure-line">
        <Icon name="warning" size={20} />
        <span>{plainError(error)}</span>
      </p>
      <details className="technical">
        <summary>Details</summary>
        <pre className="stage-log" data-testid="stage-log">
          {error}
        </pre>
      </details>
      {(onRetry || onAsk) && (
        <div className="row failure-actions">
          {onRetry && (
            <button type="button" className="primary" onClick={onRetry}>
              <Icon name="retry" /> Retry from this stage
            </button>
          )}
          {onAsk && (
            <button type="button" onClick={onAsk}>
              Ask this run
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** What a stage is, in the card's header: its kind in a word, a fan-out by its width. */
export function kindLabel(s: Stage, kind: string): string {
  if (s.items?.length) return `Fan-out of ${s.items.length}`;
  return { swarm: "Agents", gate: "Gate", workflow: "Workflow", retro: "Retro" }[kind] ?? cap(kind);
}

type Tab = { id: string; label: string; count?: number; body: ReactNode; keepsDock?: boolean };

/** The open stage's tabs. Every panel stays mounted, so a half-made answer survives a look at
 * another tab; a panel that `keepsDock` (the gate's decision) keeps its phone answer bar pinned
 * while another tab is open. */
function Tabs({ tabs, label }: { tabs: Tab[]; label: string }) {
  const [on, setOn] = useState(tabs[0]!.id);
  const active = tabs.some((t) => t.id === on) ? on : tabs[0]!.id;
  return (
    <>
      <div className="stage-tabs" role="tablist" aria-label={label}>
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={t.id === active} onClick={() => setOn(t.id)}>
            {t.label}
            {t.count ? <span className="tab-count">{t.count}</span> : null}
          </button>
        ))}
      </div>
      {tabs.map((t) => (
        <div
          key={t.id}
          role="tabpanel"
          aria-label={t.label}
          className={t.id === active ? "tab-panel" : t.keepsDock ? "tab-panel away keeps-dock" : "tab-panel away"}
          data-testid={`tab-${t.id}`}
        >
          {t.body}
        </div>
      ))}
    </>
  );
}

/** Part 7: the open stage as a card. Its header (look, kind, position, time, prev and next),
 * then what leads it (a change, the result), then tabs fitted to its kind: a gate's Decision,
 * All and Ask; an agent stage's Activity, Items, Files and Details; a connector's Result,
 * Inputs and Details. */
export function StagePanel(p: PanelProps) {
  const { run, stage, worldId, call } = p;
  // A swarm stage's Attempts: the latest shows unless you pick another; an earlier one shows
  // its own log lines and the files it left.
  const attempts = useAttempts(run, stage);
  const [picked, setPicked] = useState<{ stage: string; n: number } | null>(null);
  const last = attempts.at(-1)?.number ?? 1;
  const on = picked?.stage === stage.id && attempts.some((a) => a.number === picked.n) ? picked.n : last;
  const attempt = attempts.find((a) => a.number === on);
  const kept = useAttemptFiles(run, stage, attempts.length > 1 && on !== last ? on : null);
  const made = kept ?? p.made;
  const connectorOf = p.connectors[stage.calls ?? ""];
  const kind = kindOf(stage, made, connectorOf);
  const timings = kind === "audio" ? made.find((a) => a.name === "timings.json") : undefined;
  const connector = stage.type === "connector";
  // A build step's panel shows its builder's steps; anything else logged there (the sandbox
  // blocking its render) still gets the live log.
  const log = attemptLog(p.log, attempts, on);
  const shownLog = kind === "build" ? log.filter((e) => e.agent !== "builder") : log;
  const working = p.agents.filter((a) => a.status === "working");
  const prev = run.stages[stage.index - 1];
  const next = run.stages[stage.index + 1];
  const files = (
    <>
      {!p.resultShown && work(made).map((a) => <Made key={a.path} a={a} worldId={worldId} />)}
      {made.length === 0 && <p className="faint">Nothing made yet.</p>}
      <AllFiles made={made} worldId={worldId} onEdit={p.onEditFile} />
    </>
  );
  let tabs: Tab[];
  if (stage.type === "gate") {
    const all = choosable(before(run, stage, p.artifacts));
    tabs = [
      {
        id: "decision",
        label: "Decision",
        keepsDock: true,
        body: (
          <>
            {p.decision}
            <GatePanel run={run} stage={stage} open={!!p.answering} artifacts={p.artifacts} worldId={worldId} />
          </>
        ),
      },
      ...(all.length ? [{ id: "all", label: `All ${all.length}`, body: <ItemCards items={all} /> }] : []),
      {
        id: "ask",
        label: "Ask",
        body: (
          <div className="gate-ask">
            <p className="faint">Not sure? Ask the run what its stages found and why, before you answer.</p>
            {p.onAsk && (
              <button type="button" onClick={p.onAsk}>
                Ask this run
              </button>
            )}
          </div>
        ),
      },
    ];
  } else if (connector) {
    let result;
    if (timings) {
      result = p.resultShown ? <p className="faint">Its takes are the result shown above.</p> : <Takes timings={timings} worldId={worldId} />;
    } else if (kind === "build") {
      result = (
        <>
          <BuildPanel call={call} log={p.log} agents={p.agents} made={made} worldId={worldId} shown={p.resultShown} />
          <SaveAsTemplate runId={run.id} stage={stage} call={call} connector={connectorOf} />
        </>
      );
    } else if (kind === "video") {
      result = (
        <>
          {call?.template && (
            <p className="faint" data-testid="template">
              Rendered the Template <b>{call.template.name}</b>
            </p>
          )}
          <RenderProgress call={call} />
          <Video made={made} worldId={worldId} shown={p.resultShown} />
        </>
      );
    } else if (kind === "publish") {
      result = <PublishPanel made={made} call={call} connectors={p.connectors} />;
    } else {
      const out = work(made);
      result = out.length ? (
        p.resultShown ? (
          <p className="faint">It made {out.map((a) => a.name).join(", ")}, the result shown above.</p>
        ) : (
          out.map((a) => <Made key={a.path} a={a} worldId={worldId} />)
        )
      ) : (
        <p className="faint">{call?.status === "polling" ? `Working: ${Math.round((call.progress ?? 0) * 100)}%` : "Nothing made yet."}</p>
      );
    }
    const ins = (stage.inputs ?? []).length;
    tabs = [
      { id: "result", label: "Result", body: result },
      ...(ins ? [{ id: "inputs", label: "Inputs", count: ins, body: <StepInputs run={run} stage={stage} artifacts={p.artifacts} worldId={worldId} /> }] : []),
      {
        id: "details",
        label: "Details",
        body: (
          <>
            <AllFiles made={made} worldId={worldId} onEdit={p.onEditFile} />
            {shownLog.length > 0 && <LiveLog log={shownLog} tasks={p.tasks} />}
            <TechnicalDetails call={call} />
          </>
        ),
      },
    ];
  } else {
    // An agent stage: a swarm, a workflow or fan-out over child runs, the retro.
    const items = stage.items ?? [];
    const summary =
      stage.type === "swarm" ? (
        <>
          <AgentCards agents={p.agents} log={log} tasks={p.tasks} />
          <StageOutput verdicts={p.verdicts} artifacts={made} />
          <LiveLog log={shownLog} tasks={p.tasks} />
        </>
      ) : stage.type === "retro" ? (
        <>
          <RetroPanel run={run} stage={stage} />
          <LiveLog log={shownLog} tasks={p.tasks} />
        </>
      ) : (
        <>
          {stage.child ? null : <p className="faint">{items.length ? "Each item runs as its own run; open one from Items." : "Its run starts once the stage before it is done."}</p>}
          {shownLog.length > 0 && <LiveLog log={shownLog} tasks={p.tasks} />}
        </>
      );
    // A workflow or fan-out with no agent of its own has nothing to transcribe.
    const activity = stage.type === "swarm" || stage.type === "retro" || shownLog.length > 0 ? <ActivityViews summary={summary} log={shownLog} /> : summary;
    const spent = p.agents.reduce((sum, a) => sum + a.cost_usd, 0);
    tabs = [
      { id: "activity", label: "Activity", body: activity },
      ...(stage.type === "swarm"
        ? [{ id: "items", label: "Items", count: p.tasks.length, body: <Whiteboard runId={run.id} stage={stage} tasks={p.tasks} onAdded={p.onTaskAdded} /> }]
        : items.length
          ? [{ id: "items", label: "Items", count: items.length, body: <FanoutItems items={items} /> }]
          : []),
      { id: "files", label: "Files", count: made.length, body: files },
      {
        id: "details",
        label: "Details",
        body: (
          <dl className="facts" data-testid="stage-details">
            <dt>Kind</dt>
            <dd>{kindLabel(stage, kind)}</dd>
            <dt>Agents</dt>
            <dd>{p.agents.length ? p.agents.map((a) => a.name).join(", ") : "None yet"}</dd>
            <dt>Spent</dt>
            <dd>{money(spent)}</dd>
            {stage.started_at && (
              <>
                <dt>Started</dt>
                <dd>{new Date(stage.started_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</dd>
              </>
            )}
          </dl>
        ),
      },
    ];
  }
  return (
    <section className="panel stage-card" aria-label={`Step ${stage.index + 1}, ${stage.name}`} data-testid="stage-panel">
      <div className="stage-head">
        <span className="tile">
          <StageLook stage={stage} connector={connectorOf} />
        </span>
        <div>
          <h2>{stage.name}</h2>
          <p className="stage-meta" data-testid="stage-meta">
            {[
              kindLabel(stage, kind),
              `step ${stage.index + 1} of ${run.stages.length}`,
              attempts.length > 1 && stage.status !== "running"
                ? `${attempts.length} attempts, ${clock(attempts.reduce((sum, a) => sum + attemptSeconds(a), 0))}`
                : stepMeta(stage, run.status === "queued"),
            ].map((part, i) => (
              <span key={i}>
                {i > 0 && <Icon name="next" size={16} />}
                {part}
              </span>
            ))}
          </p>
        </div>
        {p.onStep && (
          <div className="stage-nav">
            <button type="button" className="icon" aria-label="Previous step" disabled={!prev} onClick={() => prev && p.onStep!(prev.id)}>
              <Icon name="back" />
            </button>
            <button type="button" className="icon" aria-label="Next step" disabled={!next} onClick={() => next && p.onStep!(next.id)}>
              <Icon name="next" />
            </button>
          </div>
        )}
      </div>
      {attempts.length > 1 && <AttemptTabs attempts={attempts} on={on} onPick={(n) => setPicked({ stage: stage.id, n })} running={stage.status === "running"} />}
      {p.lead}
      {attempt && <AttemptPin attempt={attempt} />}
      {/* A budget gate opens on whatever stage ran out: its answer leads that stage's card. */}
      {stage.type !== "gate" && p.decision}
      {stage.child && (
        <p data-testid="child-run">
          Child run:{" "}
          <Link to="/worlds/$worldId/runs/$runId" params={{ worldId: stage.child.world_id, runId: stage.child.id }}>
            {stage.child.name}
          </Link>
        </p>
      )}
      {/* A swarm stage pins each Attempt's words above; any other stage lists its notes. */}
      {stage.type !== "swarm" &&
        (stage.notes ?? []).map((note, i) => (
          <p className="faint" key={i}>
            Sent back with: {note}
          </p>
        ))}
      {stage.network && (
        <p className="notice" role="status" data-testid="stage-network">
          {stage.network}
        </p>
      )}
      {stage.waiting && (
        <p className="notice" role="status" data-testid="stage-waiting">
          {stage.waiting.charAt(0).toUpperCase() + stage.waiting.slice(1)}.
        </p>
      )}
      {stage.status === "failed" && stage.error && <Failure error={stage.error} onRetry={p.onRetry} onAsk={p.onAsk} />}
      <Tabs key={stage.id} tabs={tabs} label={`${stage.name} views`} />
      {messageable(stage, working) && <MessageBox run={run} stage={stage} working={working} />}
      {followable(stage) && <FollowUpBox key={`follow ${stage.id}`} run={run} stage={stage} />}
    </section>
  );
}

/** A fan-out's items: each a link to its child run, its state, and its error folded. */
function FanoutItems({ items }: { items: NonNullable<Stage["items"]> }) {
  return (
    <ul className="fanout-items" aria-label="Items" data-testid="stage-items">
      {items.map((item) => (
        <li key={item.key}>
          {item.child ? (
            <Link to="/worlds/$worldId/runs/$runId" params={{ worldId: item.child.world_id, runId: item.child.id }}>
              {item.title}
            </Link>
          ) : (
            item.title
          )}{" "}
          <span className={item.status === "failed" || item.status === "aborted" ? "chip warn" : "chip"}>{item.status}</span>
          {item.approved && <span className="chip ok">approved</span>}
          {item.error && <ItemError error={item.error} />}
        </li>
      ))}
    </ul>
  );
}
