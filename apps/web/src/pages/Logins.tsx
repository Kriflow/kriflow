import { Link } from "@tanstack/react-router";
import { cloneElement, type FormEvent, type ReactElement, type ReactNode, useEffect, useState } from "react";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { Sheet } from "../Sheet";
import { Loading, Skeleton } from "../Skeleton";
import { notify } from "../Toast";

type Login = components["schemas"]["LoginView"];
type Use = components["schemas"]["UseView"];
type RowProps = { title: string; about: string; children: ReactNode };

/** A login's last result, in words. */
export const RESULT_WORDS: Record<string, string> = {
  signed_in: "Signed in",
  reused_session: "Reused session",
  person_check: "Asked you for a code",
  refused_origin: "Refused another site",
  failed: "Failed",
  received: "Used",
};

export function ago(ts: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(ts)) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

/** The card's status line: what the login has, paired with who used it and when. */
export function statusLine(login: Login): string {
  const use = login.last_use;
  const by = use ? `used ${ago(use.at)}${use.run_name ? ` by ${use.run_name}` : ""}` : "";
  if (login.has_session) return use ? `Saved session, ${by}` : "Saved session, not used yet";
  return use ? `No saved session yet, ${by}` : "Never used";
}

/** The field a refusal names (`Domains: *.com is too broad`), for its line under that field. */
const FIELD_OF: Record<string, string> = {
  Name: "name",
  Domains: "domains",
  Username: "username",
  Password: "password",
  "Authenticator secret": "totp",
};
function refusal(error: unknown): { field: string; text: string } {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  const text = typeof detail === "string" ? detail : "Not saved. Try again.";
  const label = text.split(":")[0] ?? "";
  return { field: FIELD_OF[label] ?? "", text };
}

/** A first guess at a login's name from its first domain: admin.myshop.com is myshop_admin. */
export function suggestName(domain: string): string {
  const labels = domain.trim().toLowerCase().replace(/^\*\./, "").split(":")[0]?.split(".") ?? [];
  const parts = labels.slice(0, -1).filter((l) => l !== "www").reverse();
  const name = parts.join("_").replace(/[^a-z0-9_]/g, "_");
  return /^[a-z]/.test(name) ? name.slice(0, 40) : "";
}

const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

function Chips({ domains, max }: { domains: string[]; max?: number }) {
  const shown = max ? domains.slice(0, max) : domains;
  return (
    <span className="login-chips">
      {shown.map((d) => (
        <span key={d} className="chip">
          {d}
        </span>
      ))}
      {shown.length < domains.length && <span className="chip faint">+{domains.length - shown.length}</span>}
    </span>
  );
}

/** The world's World Logins on Settings (spec: World Logins), phone first: a card per login,
 * the add sheet, and each login's sheet to change, rotate, forget its session or delete it. */
export function LoginsRow({ worldId, Row }: { worldId: string; Row: (p: RowProps) => ReactNode }) {
  const path = { params: { path: { world_id: worldId } } };
  const [logins, setLogins] = useState<Login[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const load = () =>
    api
      .GET("/api/worlds/{world_id}/logins", path)
      .then(({ data }) => setLogins(data ?? []))
      .catch(() => setLogins([]));
  useEffect(() => void load(), [worldId]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = logins?.find((l) => l.name === open) ?? null;
  return (
    <Row title="Logins" about="Sign-in details for sites your workflows work on. Passwords stay on this box and are never shown again.">
      <div className="panel set-panel logins" aria-label="Logins">
        {logins === null ? (
          <Loading className="login-list">
            <Skeleton h={64} />
            <Skeleton h={64} />
          </Loading>
        ) : logins.length === 0 ? (
          <div className="logins-empty">
            <p>Let a workflow sign in to a site for you, like your shop's admin or a supplier portal.</p>
            <ul className="faint">
              <li>Name it after the account, like shop_admin.</li>
              <li>List every domain the site uses.</li>
            </ul>
          </div>
        ) : (
          <ul className="login-list">
            {logins.map((l) => (
              <li key={l.name}>
                <button type="button" className="login-card" onClick={() => setOpen(l.name)} aria-label={`${l.name}, ${l.username}`}>
                  <strong className="login-name">{l.name}</strong>
                  <span className="faint">{l.username}</span>
                  <Chips domains={l.domains} max={2} />
                  <span className="login-status">{statusLine(l)}</span>
                  {l.workflows.length > 0 && <span className="faint">In {l.workflows.map((w) => w.name).join(", ")}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="set-save start">
          <button type="button" onClick={() => setAdding(true)}>
            Add a login
          </button>
        </div>
      </div>
      <Sheet open={adding} onClose={() => setAdding(false)} label="Add a login" className="login-sheet">
        <AddLogin
          worldId={worldId}
          onSaved={(l) => {
            setAdding(false);
            setLogins((all) => [...(all ?? []), l].sort((a, b) => a.name.localeCompare(b.name)));
            notify(`Saved ${l.name}. Name it in a stage: login: ${l.name}.`, { tone: "ok" });
          }}
        />
      </Sheet>
      <Sheet open={!!shown} onClose={() => setOpen(null)} label={shown?.name ?? "Login"} className="login-sheet" head={false}>
        {shown && (
          <LoginSheet
            worldId={worldId}
            login={shown}
            onChanged={(l) => setLogins((all) => (all ?? []).map((x) => (x.name === l.name ? l : x)))}
            onDeleted={() => {
              setOpen(null);
              setLogins((all) => (all ?? []).filter((x) => x.name !== shown.name));
              notify(`Deleted ${shown.name}.`, { tone: "ok" });
            }}
          />
        )}
      </Sheet>
    </Row>
  );
}

/** A field with its label, its error and its help. The control is named by the label alone
 * (`aria-label`), so what is typed in it never joins its name. */
function Field({ label, help, error, children }: { label: string; help?: string; error?: string; children: ReactElement<{ "aria-label"?: string }> }) {
  return (
    <div className="login-field">
      <label>
        <span>{label}</span>
        {children.props["aria-label"] ? children : cloneElement(children, { "aria-label": label })}
      </label>
      {error && (
        <span role="alert" className="bad">
          {error}
        </span>
      )}
      {help && <span className="faint">{help}</span>}
    </div>
  );
}

function Password({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const [shown, setShown] = useState(false);
  return (
    <span className="login-password">
      <input
        type={shown ? "text" : "password"}
        aria-label={label}
        autoComplete="new-password"
        required
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button type="button" onClick={() => setShown((s) => !s)} aria-pressed={shown}>
        {shown ? "Hide" : "Show"}
      </button>
    </span>
  );
}

/** The add sheet: what it is called and where before the password (the easy question first). */
function AddLogin({ worldId, onSaved }: { worldId: string; onSaved: (l: Login) => void }) {
  const [name, setName] = useState("");
  const [named, setNamed] = useState(false);
  const [domains, setDomains] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [bad, setBad] = useState<{ field: string; text: string } | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setBad(null);
    const { data, error } = await api.POST("/api/worlds/{world_id}/logins", {
      params: { path: { world_id: worldId } },
      body: { name: name.trim(), domains: lines(domains), username, password, totp_secret: totp || null },
    });
    setBusy(false);
    if (!data) return setBad(refusal(error));
    onSaved(data);
  }

  const err = (field: string) => (bad?.field === field ? bad.text : undefined);
  return (
    <form className="login-form" onSubmit={save} aria-label="Add a login">
      <Field label="Name" help={`Workflows name it in a stage: login: ${name || "shop_admin"}.`} error={err("name")}>
        <input
          required
          maxLength={40}
          placeholder="shop_admin"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          value={name}
          onChange={(e) => {
            setNamed(true);
            setName(e.target.value);
          }}
        />
      </Field>
      <Field
        label="Domains"
        help="Sign-in fills only on these sites, and the stage can reach only these. Use *.myshop.com for every subdomain."
        error={err("domains")}
      >
        <textarea
          required
          rows={3}
          placeholder="admin.myshop.com"
          autoCapitalize="none"
          spellCheck={false}
          value={domains}
          onChange={(e) => {
            setDomains(e.target.value);
            if (!named) setName(suggestName(lines(e.target.value)[0] ?? ""));
          }}
        />
      </Field>
      <Field label="Username" error={err("username")}>
        <input required autoComplete="off" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      <Field label="Password" error={err("password")}>
        <Password label="Password" value={password} onChange={setPassword} />
      </Field>
      <Field
        label="Authenticator secret (optional)"
        help="The text under the QR code when you set up 2FA. With it, sign-in needs no code from you."
        error={err("totp")}
      >
        <input autoComplete="off" autoCapitalize="characters" spellCheck={false} value={totp} onChange={(e) => setTotp(e.target.value)} />
      </Field>
      {bad && !bad.field && <p role="alert">{bad.text}</p>}
      <p className="faint">Recordings of each run show the pages, never the password.</p>
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Saving..." : "Save login"}
      </button>
    </form>
  );
}

type Open = "password" | "edit" | "delete" | null;

/** A login's sheet: what it is and who used it, then what can be done to it. */
function LoginSheet({
  worldId,
  login,
  onChanged,
  onDeleted,
}: {
  worldId: string;
  login: Login;
  onChanged: (l: Login) => void;
  onDeleted: () => void;
}) {
  const path = { params: { path: { world_id: worldId, name: login.name } } };
  const [uses, setUses] = useState<Use[] | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [busy, setBusy] = useState(false);
  const [bad, setBad] = useState<{ field: string; text: string } | null>(null);
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [clearTotp, setClearTotp] = useState(false);
  const [domains, setDomains] = useState(login.domains.join("\n"));
  const [username, setUsername] = useState(login.username);

  useEffect(() => {
    api
      .GET("/api/worlds/{world_id}/logins/{name}/uses", path)
      .then(({ data }) => setUses(data ?? []))
      .catch(() => setUses([]));
  }, [worldId, login.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (what: Open) => {
    setBad(null);
    setOpen((o) => (o === what ? null : what));
  };

  async function done<T>(call: Promise<{ data?: T; error?: unknown }>, said: string): Promise<T | undefined> {
    setBusy(true);
    setBad(null);
    const { data, error } = await call;
    setBusy(false);
    if (!data) {
      setBad(refusal(error));
      return undefined;
    }
    notify(said, { tone: "ok" });
    setOpen(null);
    return data;
  }

  async function rotate(e: FormEvent) {
    e.preventDefault();
    const body = { password, totp_secret: totp || null, clear_totp: clearTotp };
    const data = await done(api.POST("/api/worlds/{world_id}/logins/{name}/rotate", { ...path, body }), "Password changed. It is signed out everywhere on this box.");
    if (data) {
      setPassword("");
      setTotp("");
      onChanged(data);
    }
  }

  async function edit(e: FormEvent) {
    e.preventDefault();
    const body = { domains: lines(domains), username };
    const data = await done(api.PATCH("/api/worlds/{world_id}/logins/{name}", { ...path, body }), "Saved.");
    if (data) onChanged(data);
  }

  async function forget() {
    const data = await done(api.DELETE("/api/worlds/{world_id}/logins/{name}/session", path), "Forgotten. The next run signs in again.");
    if (data) onChanged(data);
  }

  async function remove() {
    setBusy(true);
    const { response } = await api.DELETE("/api/worlds/{world_id}/logins/{name}", path);
    setBusy(false);
    if (response.ok) return onDeleted();
    setBad({ field: "", text: "Not deleted. Try again." });
  }

  const err = (field: string) => (bad?.field === field ? bad.text : undefined);
  const named = login.workflows;
  return (
    <div className="login-detail">
      <header className="login-head">
        <h2>{login.name}</h2>
        <span className="faint">{login.username}</span>
      </header>
      <Chips domains={login.domains} />
      <dl className="kv">
        <dt>Authenticator</dt>
        <dd>{login.has_totp ? "set" : "not set"}</dd>
        <dt>Saved session</dt>
        <dd>{login.has_session ? "kept" : "none"}</dd>
        <dt>Password changed</dt>
        <dd>{new Date(login.rotated_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</dd>
      </dl>
      <section aria-label="Used by">
        <h3>Used by</h3>
        {uses === null ? (
          <Skeleton h={42} />
        ) : uses.length === 0 ? (
          <p className="faint">No run has used it yet.</p>
        ) : (
          <ul className="login-uses">
            {uses.map((u) => (
              <li key={`${u.run_id}-${u.stage_id}-${u.at}`}>
                <Link to="/worlds/$worldId/runs/$runId" params={{ worldId, runId: u.run_id }}>
                  {u.run_name ?? "A deleted run"}
                  {u.stage_name ? ` · ${u.stage_name}` : ""}
                </Link>
                <span>{RESULT_WORDS[u.result] ?? u.result}</span>
                <span className="faint">{ago(u.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {bad && !bad.field && <p role="alert">{bad.text}</p>}
      <div className="login-actions">
        <button type="button" aria-expanded={open === "password"} onClick={() => toggle("password")}>
          Change password
        </button>
        {open === "password" && (
          <form className="login-form" onSubmit={rotate} aria-label="Change password">
            <Field label="New password" error={err("password")}>
              <Password label="New password" value={password} onChange={setPassword} />
            </Field>
            <Field label="New authenticator secret (optional)" error={err("totp")}>
              <input autoComplete="off" spellCheck={false} value={totp} onChange={(e) => setTotp(e.target.value)} disabled={clearTotp} />
            </Field>
            {login.has_totp && (
              <label className="check">
                <input type="checkbox" checked={clearTotp} onChange={(e) => setClearTotp(e.target.checked)} />
                Take the authenticator secret out
              </label>
            )}
            <p className="faint">Saving signs this login out everywhere on this box.</p>
            <button type="submit" className="primary" disabled={busy}>
              Save password
            </button>
          </form>
        )}
        <button type="button" aria-expanded={open === "edit"} onClick={() => toggle("edit")}>
          Edit domains or username
        </button>
        {open === "edit" && (
          <form className="login-form" onSubmit={edit} aria-label="Edit domains or username">
            <Field label="Domains" help="One per line." error={err("domains")}>
              <textarea required rows={3} autoCapitalize="none" spellCheck={false} value={domains} onChange={(e) => setDomains(e.target.value)} />
            </Field>
            <Field label="Username" error={err("username")}>
              <input required autoComplete="off" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} />
            </Field>
            <button type="submit" className="primary" disabled={busy}>
              Save changes
            </button>
          </form>
        )}
        {login.has_session && (
          <>
            <button type="button" onClick={() => void forget()} disabled={busy}>
              Forget saved session
            </button>
            <span className="faint">The next run signs in again.</span>
          </>
        )}
        <button type="button" className="danger" aria-expanded={open === "delete"} onClick={() => toggle("delete")}>
          Delete login
        </button>
        {open === "delete" &&
          (named.length > 0 ? (
            <p role="alert" data-testid="delete-refused">
              {named.map((w, i) => (
                <span key={w.id}>
                  {i > 0 && (i === named.length - 1 ? " and " : ", ")}
                  <Link to="/worlds/$worldId/workflows/$workflowId" params={{ worldId, workflowId: w.id }}>
                    {w.name}
                  </Link>
                </span>
              ))}{" "}
              {named.length > 1 ? "name" : "names"} this login. Remove it from their stages first.
            </p>
          ) : (
            <div className="login-confirm" role="group" aria-label="Delete this login">
              <p>
                Delete {login.name}? Its saved session goes too.
              </p>
              <div className="login-confirm-buttons">
                <button type="button" className="danger" onClick={() => void remove()} disabled={busy}>
                  Delete
                </button>
                <button type="button" onClick={() => setOpen(null)}>
                  Not now
                </button>
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}
