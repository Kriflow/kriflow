import { type ReactNode, useLayoutEffect, useRef } from "react";

import type { Agent } from "../api/events";
import type { Connector, Connectors } from "../api/connectors";
import { clock, current, duration, money, type Run, took } from "../api/runs";
import { who } from "../api/tasks";
import { CountTick } from "../CountTick";
import { Elapsed } from "../Elapsed";
import { Icon, type IconName } from "../Icon";
import { stageSeconds, stepMeta } from "./StagePanel";

type Stage = Run["stages"][number];

/** How the run is doing, as the hero's tone and the glyph it leads with. */
const TONE: Record<Run["status"], [string, IconName]> = {
  queued: ["idle", "clock"],
  running: ["live", "play"],
  waiting_gate: ["warn", "bell"],
  paused: ["idle", "pause"],
  done: ["pos", "check"],
  failed: ["neg", "warning"],
  aborted: ["neg", "stop"],
};

const SETTLED = new Set(["done", "failed", "aborted"]);

/** Seconds a fan-out stage still needs at the pace of its settled items; null when unknown. */
export function timeLeft(s: Stage, now = Date.now()): number | null {
  const items = s.items ?? [];
  const settled = items.filter((i) => SETTLED.has(i.status)).length;
  const secs = stageSeconds(s, now);
  if (s.status !== "running" || !settled || settled === items.length || secs === null) return null;
  return (secs / settled) * (items.length - settled);
}

/** The line under the hero's status: where the run stands, and against what. */
export function heroSub(run: Run, seconds: number | null, now = Date.now()): string {
  const at = current(run);
  const of = at ? `${at.name}, step ${at.index + 1} of ${run.stages.length}.` : "";
  if (run.status === "waiting_gate" && at) {
    const waited = stageSeconds(at, now);
    return `${of}${waited === null ? "" : ` Waiting ${took(waited)}.`}`;
  }
  if (run.status === "running" && at) {
    const left = timeLeft(at, now);
    return left === null ? "" : `About ${took(Math.max(60, left))} left at this pace.`;
  }
  const last = run.last_run_seconds;
  if (run.status === "done" && seconds !== null && last != null) {
    const gap = seconds - last;
    if (Math.abs(gap) < Math.max(30, last * 0.05)) return "About as long as last time.";
    return `${took(Math.abs(gap))} ${gap < 0 ? "faster" : "slower"} than last time.`;
  }
  return "";
}

/** A stable short label and colour for one agent of a run, from its name. */
export function faceOf(name: string): { label: string; colour: string } {
  const words = who(name).split(/\s+/);
  const label = words.length > 1 ? `${words[0]![0]}${words.at(-1)![0]}` : words[0]!.slice(0, 2);
  const hash = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
  return { label: label.charAt(0).toUpperCase() + label.slice(1), colour: FACE_COLOURS[hash % FACE_COLOURS.length]! };
}
const FACE_COLOURS = ["#8be0b4", "#ffd38a", "#9ec5ff", "#f5a3c0", "#c7b4ff", "#86e1e8"];

/** Up to four agents as overlapping faces. Decorative: the count beside them is what is read. */
export function Faces({ agents }: { agents: Agent[] }) {
  return (
    <span className="faces" aria-hidden="true">
      {agents.slice(0, 4).map((a) => {
        const f = faceOf(a.name);
        return (
          <span key={a.id} className="face" style={{ background: f.colour }} title={who(a.name)}>
            {f.label}
          </span>
        );
      })}
    </span>
  );
}

/** The top of the run page: one status line, its one action, and four numbers each with what it
 * is measured against. `line` is the status sentence; `action` the button that matters now. */
export function RunHero({ run, line, action, agents }: { run: Run; line: ReactNode; action?: ReactNode; agents: Agent[] }) {
  const [tone, glyph] = TONE[run.status];
  const seconds = duration(run);
  const sub = heroSub(run, seconds);
  const live = run.status === "running" || run.status === "waiting_gate";
  const working = agents.filter((a) => a.status === "working");
  const shown = working.length ? working : agents;
  const last = run.last_run_seconds;
  const budget = run.budget_usd;
  const spent = run.spent_usd ?? 0;
  const done = run.stages.filter((s) => s.status === "done").length;
  return (
    <section className="hero" data-tone={tone} aria-label="Status" data-testid="hero">
      <span className="hero-badge">
        <Icon name={glyph} size={24} live={run.status === "running"} />
      </span>
      <div className="hero-line">{line}</div>
      {action && <div className="hero-go">{action}</div>}
      {sub && (
        <p className="hero-sub" data-testid="hero-sub">
          {sub}
        </p>
      )}
      <dl className="stats">
        <div className="stat">
          <dt className="stat-ic">
            <Icon name="clock" />
            <span className="visually-hidden">Time</span>
          </dt>
          <dd>
            <b>{seconds === null ? "Not started" : <Elapsed run={run} />}</b>
            <span data-testid="last-run">
              {live ? "so far" : "took"}
              {last != null && `, last run ${took(last)}`}
            </span>
          </dd>
        </div>
        <div className="stat">
          <dt className="stat-ic">
            <Icon name="coin" />
            <span className="visually-hidden">Spend</span>
          </dt>
          <dd data-testid="spend-stat">
            <b>
              <CountTick value={spent} format={money} />
              <small>{budget != null ? ` of ${money(budget)}` : " spent"}</small>
            </b>
            {budget != null && (
              <span className="mini" aria-hidden="true">
                <i style={{ width: `${Math.min(100, (spent / budget) * 100)}%` }} />
              </span>
            )}
          </dd>
        </div>
        <div className="stat">
          <dt className="stat-ic">
            <Icon name="people" />
            <span className="visually-hidden">Agents</span>
          </dt>
          <dd data-testid="agents-stat">
            <b>
              {shown.length} <small>{shown.length === 1 ? "agent" : "agents"}</small> <Faces agents={shown} />
            </b>
            <span>{working.length ? "working now" : "worked on this run"}</span>
          </dd>
        </div>
        <div className="stat">
          <dt className="stat-ic">
            <Icon name="steps" />
            <span className="visually-hidden">Steps</span>
          </dt>
          <dd data-testid="steps-stat">
            <b>
              {done} of {run.stages.length}
            </b>
            <span>steps done</span>
          </dd>
        </div>
      </dl>
    </section>
  );
}

/** The glyph of a stage kind; a connector stage wears its package's own icon instead. */
const KIND_ICON: Record<string, IconName> = { swarm: "people", gate: "gate", workflow: "workflows", retro: "memory", connector: "plug" };

/** A stage's look: its package's icon for a connector with one, else the glyph of its kind (a
 * workflow stage over items is a fan-out). */
export function StageLook({ stage, connector }: { stage: Stage; connector?: Connector }) {
  if (stage.type === "connector" && connector?.icon && connector.version) {
    const src = `/api/packages/${encodeURIComponent(connector.name)}/${encodeURIComponent(connector.version)}/icon`;
    return <img src={src} alt="" width={22} height={22} data-testid="package-icon" />;
  }
  const name = stage.items?.length ? "fan" : (KIND_ICON[stage.type] ?? "stage");
  return <Icon name={name} size={20} className={`kind-${stage.items?.length ? "fan-out" : stage.type}`} />;
}

/** What the rail says under a stage's name: its time, its wait, its progress, or nothing yet. */
function railMeta(s: Stage, queued: boolean): string {
  const secs = stageSeconds(s);
  const items = s.items ?? [];
  if (s.status === "waiting") return secs === null ? "waiting" : `waiting ${clock(secs)}`;
  if (s.status === "running" && items.length) return `${items.filter((i) => SETTLED.has(i.status)).length} of ${items.length}`;
  if (s.status === "pending" && !queued) return "";
  return stepMeta(s, queued);
}

/** Every stage as an icon tile in order: a tick when done, amber while it waits for you, blue
 * while it runs, a count on a fan-out. Tapping one selects it (the page pins it in the URL); the
 * rail scrolls sideways and keeps the selected tile in its middle, and never selects by itself.
 * `note` names a change made to a stage: `mark` shows under it when nothing else does, `label`
 * joins its label. */
export function StageRail(p: {
  run: Run;
  selected?: string;
  onSelect: (id: string) => void;
  connectors: Connectors;
  note?: (s: Stage) => { mark: string; label: string } | undefined;
}) {
  const { run, selected } = p;
  const rail = useRef<HTMLOListElement>(null);
  useLayoutEffect(() => {
    const box = rail.current;
    const tile = box?.querySelector<HTMLElement>("[aria-pressed='true']");
    if (!box || !tile) return;
    const [b, t] = [box.getBoundingClientRect(), tile.getBoundingClientRect()];
    box.scrollLeft += t.left + t.width / 2 - (b.left + b.width / 2);
  }, [selected]);
  const queued = run.status === "queued";
  return (
    <ol className="rail" ref={rail} aria-label="Steps" data-testid="timeline">
      {run.stages.map((s) => {
        const now = queued ? s.index === 0 : s.status === "running" || s.status === "waiting";
        const meta = railMeta(s, queued);
        const extra = p.note?.(s);
        const label = `Step ${s.index + 1}, ${s.name}, ${stepMeta(s, queued)}${extra ? `, ${extra.label}` : ""}`;
        const count = s.items?.length;
        return (
          <li key={s.id} data-status={s.status} className={`node node-${s.status}${now ? " now" : ""}`}>
            <button type="button" aria-pressed={s.id === selected} aria-current={now ? "step" : undefined} aria-label={label} title={label} onClick={() => p.onSelect(s.id)}>
              <span className="tile">
                <StageLook stage={s} connector={p.connectors[s.calls ?? ""]} />
                {!!count && <span className="count">×{count}</span>}
                {s.status === "done" && (
                  <span className="mark mark-done">
                    <Icon name="check" />
                  </span>
                )}
                {s.status === "failed" && (
                  <span className="mark mark-failed">
                    <Icon name="x" />
                  </span>
                )}
              </span>
              <span className="nm">{s.name}</span>
              <span className={meta || !extra ? "dt" : "dt edited"}>{meta || extra?.mark || " "}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
