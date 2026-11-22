import { Link, useParams, useRouter } from "@tanstack/react-router";
import { type ReactNode, useEffect, useState } from "react";

import { api } from "../api/client";
import { useConfirm } from "../Dialog";
import { money, type Run, STATUS_LABEL } from "../api/runs";
import type { components } from "../api/schema";
import { Empty } from "../Empty";
import { Icon } from "../Icon";
import { moment, MOMENTS } from "../moments";
import { Loading, Skeleton } from "../Skeleton";
import { usePhone } from "../usePhone";
import { useRefresh } from "../refresh";
import { PullToRefresh } from "../PullToRefresh";
import { Menu } from "./Menu";

type Workflow = components["schemas"]["ListedWorkflow"];

/** The world's workflows: what each is at, how its last run went, what a run costs, and a start. */
export function Workflows() {
  const { worldId } = useParams({ from: "/worlds/$worldId/workflows" });
  const router = useRouter();
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  // null until read, so a start knows whether it is the world's first run.
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [confirm, dialog] = useConfirm();
  const phone = usePhone();

  const fresh = useRefresh();
  useEffect(() => {
    // The rows show at once; each workflow's check (seconds, it may start connectors) follows.
    const path = { world_id: worldId };
    let checked = false;
    api
      .GET("/api/worlds/{world_id}/workflows", { params: { path, query: { check: false, archived: true } } })
      .then(({ data }) => {
        if (!data) throw new Error("load failed");
        if (!checked) setWorkflows(data);
      })
      .catch(() => setError("Could not load the workflows. Is the API running?"));
    api
      .GET("/api/worlds/{world_id}/workflows", { params: { path, query: { archived: true } } })
      .then(({ data }) => {
        if (!data) return;
        checked = true;
        setWorkflows(data);
      })
      .catch(() => undefined);
    api
      .GET("/api/worlds/{world_id}/runs", { params: { path: { world_id: worldId } } })
      .then(({ data }) => data && setRuns(data))
      .catch(() => undefined);
  }, [worldId, fresh]);

  async function start(workflowId: string) {
    setStarting(workflowId);
    setError(null);
    const { data, error, response } = await api.POST("/api/workflows/{workflow_id}/runs", {
      params: { path: { workflow_id: workflowId } },
      body: { baseline: false },
    });
    setStarting(null);
    if (error || !data) {
      const detail = (error as { detail?: unknown } | undefined)?.detail;
      setError(typeof detail === "string" ? `Not started: ${detail}.` : `Could not start the run (${response.status}).`);
      return;
    }
    if (runs?.length === 0) moment(MOMENTS.firstRun);
    void router.navigate({ to: "/worlds/$worldId/runs/$runId", params: { worldId, runId: data.id } });
  }

  /** Archive hides it and stops its schedule; restore undoes both. The row keeps its check. */
  async function setArchived(w: Workflow, archive: boolean) {
    if (
      archive &&
      !(await confirm({
        title: `Archive ${w.name}?`,
        lines: ["It leaves the lists, its schedule stops and new runs of it are refused.", "Runs already going keep going. Restore it any time under Show archived."],
        action: "Archive",
      }))
    )
      return;
    setError(null);
    const call = archive ? "/api/workflows/{workflow_id}/archive" : "/api/workflows/{workflow_id}/restore";
    const { data } = await api.POST(call, { params: { path: { workflow_id: w.id } } });
    if (!data) return setError(`Could not ${archive ? "archive" : "restore"} ${w.name}. Try again.`);
    setWorkflows((prev) => prev?.map((x) => (x.id === w.id ? { ...x, archived_at: data.archived_at } : x)) ?? prev);
  }

  const live = workflows?.filter((w) => !w.archived_at) ?? null;
  const archivedCount = (workflows?.length ?? 0) - (live?.length ?? 0);
  const shown = showArchived ? workflows : live;
  const mine = (w: Workflow) => (runs ?? []).filter((r) => r.workflow_id === w.id);
  // One trailing play glyph per row: green stays for the data and the one primary, New workflow.
  // An archived one shows its chip and Restore instead.
  const actions = (w: Workflow) =>
    w.archived_at ? (
      <>
        <span className="chip">archived</span>
        <button type="button" onClick={() => setArchived(w, false)} aria-label={`Restore ${w.name}`}>
          Restore
        </button>
      </>
    ) : (
      <>
        <button
          type="button"
          className="icon play"
          onClick={() => start(w.id)}
          disabled={starting !== null}
          aria-label={`Start a run of ${w.name}`}
          aria-busy={starting === w.id || undefined}
        >
          <Icon name="play" size={20} />
        </button>
        <Menu
          label={`More for ${w.name}`}
          className="icon"
          button={<Icon name="more" size={20} />}
          items={[{ label: "Archive", note: "Hides it and stops its schedule; restore it any time", onSelect: () => setArchived(w, true) }]}
          sheet
        />
      </>
    );

  return (
    <main>
      <PullToRefresh />
      <header className="page">
        <div>
          <h1>Workflows</h1>
          <p className="sub">
            {live ? `${live.length} in this world. ` : ""}
            {!phone && "Each save is a new version; a run keeps the version it started on."}
          </p>
        </div>
        {workflows?.length !== 0 && (
          <Link to="/worlds/$worldId/workflows/new" params={{ worldId }} className="button primary">
            New workflow
          </Link>
        )}
      </header>
      {dialog}
      {error && <p role="alert">{error}</p>}
      {archivedCount > 0 && (
        <div className="row">
          <button type="button" className="chip-button" aria-pressed={showArchived} onClick={() => setShowArchived(!showArchived)}>
            Show archived ({archivedCount})
          </button>
        </div>
      )}
      {workflows === null && !error && (
        <Loading className={phone ? "wf-rows" : "wf-loading"}>
          {Array.from({ length: phone ? 4 : 5 }, (_, i) => (
            <Skeleton key={i} />
          ))}
        </Loading>
      )}
      {live?.length === 0 && !showArchived && (
        <Empty
          icon="workflows"
          testId="no-workflows"
          action={
            <Link to="/worlds/$worldId/workflows/new" params={{ worldId }} className="button primary">
              Write a workflow
            </Link>
          }
        >
          A workflow is a list of steps you write once and run again and again.
        </Empty>
      )}
      {phone && shown && shown.length > 0 && (
        <ul className="wf-rows stagger">
          {shown.map((w) => (
            <WorkflowRow key={w.id} worldId={worldId} workflow={w} runs={mine(w)}>
              {actions(w)}
            </WorkflowRow>
          ))}
        </ul>
      )}
      {!phone && shown && shown.length > 0 && (
        <div className="scroll-x">
          <table className="scores workflows" data-testid="workflows">
            <thead>
              <tr>
                <th>Workflow</th>
                <th className="num">Version</th>
                <th>Last run</th>
                <th className="num">Runs</th>
                <th className="num">Avg cost</th>
                <th>
                  <span className="visually-hidden">Action</span>
                </th>
              </tr>
            </thead>
            <tbody className="stagger">
              {shown.map((w) => {
                const these = mine(w);
                const cost = these.reduce((sum, r) => sum + r.spent_usd, 0);
                const last = latest(these);
                return (
                  <tr key={w.id} data-testid="workflow">
                    <td>
                      <Link to="/worlds/$worldId/workflows/$workflowId" params={{ worldId, workflowId: w.id }}>
                        {w.name}
                      </Link>
                      {w.violations?.slice(0, 1).map((v) => (
                        <span key={v.rule} className="faint">
                          {" "}
                          Fails today's check: {v.message}.
                        </span>
                      ))}
                    </td>
                    <td className="num">v{w.current_version}</td>
                    <td>{last ? <LastRun run={last} /> : <span className="faint">-</span>}</td>
                    <td className="num">{these.length}</td>
                    <td className="num">{these.length ? money(cost / these.length) : "-"}</td>
                    <td className="action">{actions(w)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

const latest = (runs: Run[]) => runs.reduce<Run | null>((a, r) => (a && a.created_at > r.created_at ? a : r), null);

/** The last run's state in words, amber when it waits on you. */
function LastRun({ run }: { run: Run }) {
  return run.status === "waiting_gate" ? <span className="warn">waiting for you</span> : <>{STATUS_LABEL[run.status]}</>;
}

/** A workflow on a phone: one row, the name over its version, last run and cost a run, the
 * row opening the editor and one play glyph at the end starting a run. */
function WorkflowRow({ worldId, workflow: w, runs, children }: { worldId: string; workflow: Workflow; runs: Run[]; children: ReactNode }) {
  const last = latest(runs);
  const cost = runs.reduce((sum, r) => sum + r.spent_usd, 0) / (runs.length || 1);
  return (
    <li className="wf-row" data-testid="workflow-row">
      <Link to="/worlds/$worldId/workflows/$workflowId" params={{ worldId, workflowId: w.id }} aria-label={`Edit ${w.name}`}>
        <b>{w.name}</b>
        <small>
          v{w.current_version} · {last ? (
            <>
              <LastRun run={last} /> · {money(cost)} a run
            </>
          ) : (
            "no runs yet"
          )}
        </small>
        {w.violations?.slice(0, 1).map((v) => (
          <small key={v.rule} className="warn">
            Fails today's check: {v.message}.
          </small>
        ))}
      </Link>
      {children}
    </li>
  );
}
