import { clock, type Run } from "../api/runs";
import { stageSeconds } from "./StagePanel";

type Stage = Run["stages"][number];

/** How long a step took, or has taken so far, as its segment names it. */
function spent(s: Stage): string {
  const secs = stageSeconds(s);
  if (secs === null) return s.status === "skipped" ? "skipped" : "not started";
  return s.status === "waiting" ? `waiting ${clock(secs)}` : clock(secs);
}

/** A run list row's small strip: its steps side by side, each as wide as its time. Time waiting
 * on a person is capped at 1.5 times the longest working step so it cannot hide the work. Steps
 * not started get a fixed dashed slot. */
export function TimeStrip({ stages }: { stages: Stage[] }) {
  const secs = (s: Stage) => stageSeconds(s) ?? 0;
  const cap = Math.max(60, ...stages.filter((s) => s.type !== "gate").map(secs)) * 1.5;
  const shown = (s: Stage) => (s.type === "gate" ? Math.min(secs(s), cap) : secs(s));
  const total = stages.reduce((sum, s) => sum + shown(s), 0) || 1;
  const share = (s: Stage) => Math.max(shown(s) / total, 0.03);
  return (
    <div className="strip" role="group" aria-label="Time per step" data-testid="strip">
      {stages.map((s) => (
        <span
          key={s.id}
          className={`strip-seg seg-${s.status}${s.type === "gate" ? " seg-gate" : ""}`}
          style={stageSeconds(s) !== null ? { flex: `${share(s)} 1 0` } : undefined}
          title={`${s.name} · ${spent(s)}`}
          aria-label={`${s.name} · ${spent(s)}`}
          role="img"
        />
      ))}
    </div>
  );
}
