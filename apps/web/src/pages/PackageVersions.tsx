import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { useConfirm } from "../Dialog";
import { packageRoute } from "../router";
import { Loading, Skeleton } from "../Skeleton";
import { detail, SwitchDiff } from "./Connectors";

type PackageVersion = components["schemas"]["PackageVersion"];
type PackageSwitch = components["schemas"]["PackageSwitch"];

/** Every installed version of one package (issue 471): its state, which worlds pin it, how many
 * saved workflow versions record it, and a switch of this world's pin or an uninstall of it.
 * An uninstall the API refuses, as of a version in use, says why on the row. */
export function PackageVersions() {
  const [confirm, dialog] = useConfirm();
  const { worldId, name } = packageRoute.useParams();
  const [versions, setVersions] = useState<PackageVersion[] | null | undefined>(undefined);
  const [pinned, setPinned] = useState<string | undefined>(undefined);
  const [switching, setSwitching] = useState<{ p: PackageVersion; diff: PackageSwitch } | null>(null);
  const [said, setSaid] = useState<{ version: string; message: string } | null>(null);

  function load() {
    return Promise.all([
      api.GET("/api/packages/{name}/versions", { params: { path: { name } } }).then(({ data, response }) => setVersions(response.status === 404 ? null : (data ?? null))),
      api.GET("/api/worlds/{world_id}/packages", { params: { path: { world_id: worldId } } }).then(({ data }) => setPinned(data?.find((e) => e.name === name)?.version)),
    ]);
  }

  useEffect(() => {
    void load().catch(() => setVersions(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worldId, name]);

  async function askSwitch(p: PackageVersion) {
    setSaid(null);
    const { data, error } = await api.GET("/api/worlds/{world_id}/packages/{name}/switch", {
      params: { path: { world_id: worldId, name }, query: { version: p.version } },
    });
    if (error || !data) {
      setSaid({ version: p.version, message: detail(error) ?? `Could not read what ${p.label} ${p.version} changes. Try again.` });
      return;
    }
    setSwitching({ p, diff: data });
  }

  async function enable(p: PackageVersion) {
    setSaid(null);
    setSwitching(null);
    const { error } = await api.PUT("/api/worlds/{world_id}/packages/{name}", { params: { path: { world_id: worldId, name } }, body: { version: p.version } });
    if (error) setSaid({ version: p.version, message: detail(error) ?? `Could not switch to ${p.version}. Try again.` });
    else await load();
  }

  async function disable(p: PackageVersion) {
    const ok = await confirm({
      title: `Disable ${p.label} in this world?`,
      lines: [`Its workflows that call ${name} stop passing their checks.`, "Other worlds keep it."],
      action: "Disable",
      danger: true,
    });
    if (!ok) return;
    setSaid(null);
    const { error } = await api.DELETE("/api/worlds/{world_id}/packages/{name}", { params: { path: { world_id: worldId, name } } });
    if (error) setSaid({ version: p.version, message: `Could not disable ${p.label}. Try again.` });
    else await load();
  }

  async function uninstall(p: PackageVersion) {
    if (!(await confirm({ title: `Uninstall ${name} ${p.version}?`, lines: ["Its folder is removed for every world."], action: "Uninstall", danger: true }))) return;
    setSaid(null);
    const { error } = await api.DELETE("/api/packages/{name}/{version}", { params: { path: { name, version: p.version } } });
    if (error) setSaid({ version: p.version, message: detail(error) ?? `Could not uninstall ${name} ${p.version}. Try again.` });
    else await load();
  }

  const back = (
    <Link to="/worlds/$worldId/connectors" params={{ worldId }}>
      ‹ Connectors
    </Link>
  );
  if (versions === null) {
    return (
      <main>
        <h1>{name} is not installed</h1>
        <p className="sub">Every version of it was uninstalled. {back}</p>
      </main>
    );
  }
  // The pinned version names the page, else the newest; an old local draft may carry another label.
  const label = (versions?.find((v) => v.version === pinned) ?? versions?.at(-1))?.label ?? name;
  return (
    <main>
      {dialog}
      <header className="page">
        <div>
          <p className="sub">{back}</p>
          <h1>{label} versions</h1>
          <p className="sub">
            Every installed version of <span className="mono">{name}</span>, once for every world. A world runs the one it pins;
            a saved workflow version keeps the one it was saved with, so a version in use cannot be uninstalled.
          </p>
        </div>
      </header>
      {versions === undefined ? (
        <Loading className="con-groups">
          <Skeleton h={120} />
        </Loading>
      ) : (
        <section className="panel con-groups">
          <ul className="con-list" aria-label={`Versions of ${name}`}>
            {versions.map((p) => {
              const pins = p.pinned_by ?? [];
              const here = pinned === p.version;
              return (
                <li key={p.version} data-version={p.version} className="con-row">
                  <span className={p.state === "installed" ? "dot live" : "dot warn"} />
                  <span className="con-main">
                    <b className="mono">{p.version}</b>
                    <span className="faint">
                      {p.problem ?? p.about}
                      {p.commit && <> · commit {p.commit.slice(0, 12)}</>}
                    </span>
                    <span className="faint">
                      {pins.length === 0 ? "No world pins it" : `Pinned by ${pins.map((w) => w.name).join(", ")}`} · recorded by{" "}
                      {p.workflow_versions ?? 0} saved workflow {p.workflow_versions === 1 ? "version" : "versions"}
                    </span>
                  </span>
                  {p.kind && <span className="faint">{p.kind}</span>}
                  <span className={p.state === "installed" ? "pill" : "pill warning"}>{p.state === "installed" ? "Installed" : "Invalid Manifest"}</span>
                  {here ? (
                    <>
                      <span className="pill">Enabled here</span>
                      <button type="button" onClick={() => disable(p)} aria-label={`Disable ${name} ${p.version}`}>
                        Disable
                      </button>
                    </>
                  ) : (
                    p.state === "installed" && (
                      <button type="button" onClick={() => (pinned ? askSwitch(p) : enable(p))} aria-label={`Switch to ${name} ${p.version}`}>
                        {pinned ? "Switch to this version" : "Enable"}
                      </button>
                    )
                  )}
                  <button type="button" onClick={() => uninstall(p)} aria-label={`Uninstall ${name} ${p.version}`}>
                    Uninstall
                  </button>
                  {said?.version === p.version && <p role="alert">{said.message}</p>}
                  {switching?.p === p && (
                    <SwitchDiff p={p} diff={switching.diff} worldId={worldId} onSwitch={() => enable(p)} onCancel={() => setSwitching(null)} onSetUp={() => askSwitch(p)} />
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </main>
  );
}
