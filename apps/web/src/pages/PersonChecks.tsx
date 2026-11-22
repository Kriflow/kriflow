import { Link } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";

import { quiet } from "../api/client";
import type { components } from "../api/schema";
import { useRefresh } from "../refresh";
import { notify } from "../Toast";

type Check = components["schemas"]["PersonCheckView"];

/** How often an open page asks again: a Person Check waits only ten minutes. */
const POLL_MS = 4000;

/** The open Person Checks, every world's or one run's, read again on a refresh and every few
 * seconds. ponytail: polled, not streamed; follow `person_check.*` events if this costs. */
export function usePersonChecks(runId?: string): [Check[], () => void] {
  const [checks, setChecks] = useState<Check[]>([]);
  const fresh = useRefresh();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let stale = false;
    const read = runId
      ? quiet.GET("/api/runs/{run_id}/person-checks", { params: { path: { run_id: runId } } })
      : quiet.GET("/api/person-checks");
    read.then(({ data }) => !stale && data && setChecks(data)).catch(() => undefined);
    const t = setTimeout(() => setTick((n) => n + 1), POLL_MS);
    return () => {
      stale = true;
      clearTimeout(t);
    };
  }, [runId, fresh, tick]);
  return [checks, () => setTick((n) => n + 1)];
}

/** Every open Person Check as a card: Needs you lists them all, the run page its own. */
export function PersonChecks({ runId, linked = false }: { runId?: string; linked?: boolean }) {
  const [checks, reload] = usePersonChecks(runId);
  if (!checks.length) return null;
  return (
    <section className="person-checks" aria-label="Sign-in checks">
      {checks.map((c) => (
        <PersonCheckCard key={c.id} check={c} linked={linked} onDone={reload} />
      ))}
    </section>
  );
}

/** One Person Check (spec parts list): who waits and for which site, the page as the box saw
 * it, then one ask: the code the site sent, or Try again and Stop. */
function PersonCheckCard({ check, linked, onDone }: { check: Check; linked: boolean; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const run = check.run_name ?? "A run";
  const where = check.site ? ` to ${check.site}` : "";

  async function send(body: { code?: string; action?: "try_again" | "stop" }) {
    setBusy(true);
    const { response, error } = await quiet.POST("/api/person-checks/{check_id}", { params: { path: { check_id: check.id } }, body });
    setBusy(false);
    if (response.status === 404) notify("This check closed already.", { tone: "error" });
    else if (error) {
      const detail = (error as { detail?: unknown }).detail;
      return notify(typeof detail === "string" ? detail : "Not sent. Try again.", { tone: "error" });
    } else notify(body.code ? "Code sent. The run goes on." : body.action === "stop" ? "Stopped. The stage fails." : "Trying again.", { tone: "ok" });
    setCode("");
    onDone();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void send({ code });
  }

  return (
    <article className="panel person-check" data-testid="person-check">
      <p role="status" className="pc-status">
        <strong>{run}</strong> is waiting for you to sign in{where}.
        {linked && (
          <>
            {" "}
            <Link to="/worlds/$worldId/runs/$runId" params={{ worldId: check.world_id, runId: check.run_id }}>
              Open the run
            </Link>
          </>
        )}
      </p>
      {check.has_screenshot && (
        <img className="pc-shot" src={`/api/person-checks/${check.id}/screenshot`} alt={`What ${check.site ?? "the site"} shows`} />
      )}
      {check.kind === "code" ? (
        <form onSubmit={submit} className="pc-ask">
          <label>
            <span>{check.ask}</span>
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              maxLength={64}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          <button type="submit" className="primary" disabled={busy || !code.trim()}>
            Send code
          </button>
          <span className="faint">The run waits 10 minutes, then stops.</span>
        </form>
      ) : (
        <div className="pc-ask">
          <p>The site is showing a check the box cannot pass.</p>
          <div className="pc-actions">
            <button type="button" className="primary" disabled={busy} onClick={() => void send({ action: "try_again" })}>
              Try again
            </button>
            <button type="button" className="danger" disabled={busy} onClick={() => void send({ action: "stop" })}>
              Stop the stage
            </button>
          </div>
          <span className="faint">The run waits 10 minutes, then stops.</span>
        </div>
      )}
    </article>
  );
}
