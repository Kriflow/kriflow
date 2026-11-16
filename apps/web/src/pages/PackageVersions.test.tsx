import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { FakeEventSource, fakeFetch } from "../test/fakes";
import { answerConfirm, mountAt } from "../test/mount";

const pkg = { name: "echo", label: "Echo", kind: "stage", about: "Says it back.", source: "connectors/echo", installed_at: "2026-10-30T10:00:00+00:00", state: "installed", problem: null, ref: null, commit: null, subfolder: null };
const studio = { id: "world_1", name: "Video" };
const other = { id: "world_2", name: "Other" };
const unchanged = {
  from: "1.0.0",
  to: "2.0.0",
  params: { added: [], removed: [], changed: [] },
  setup: { added: [], removed: [], changed: [] },
  required: [],
  permissions: { added: [], removed: [], changed: [] },
  needs: [],
};

beforeEach(() => FakeEventSource.install());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("lists every version with its state, who pins it and how many workflow versions record it; a switch shows the diff, an uninstall in use says why on its row", async () => {
  let enabled = [{ name: "echo", version: "1.0.0" }];
  const bodies: unknown[] = [];
  const calls = fakeFetch({
    "GET /api/packages/echo/versions": () =>
      Response.json([
        { ...pkg, version: "0.9.0", state: "invalid", problem: "kind: 'robot' is not one of [...]", pinned_by: [], workflow_versions: 0 },
        { ...pkg, version: "1.0.0", pinned_by: [studio, other], workflow_versions: 3 },
        { ...pkg, version: "2.0.0", commit: "abcdef1234567890", pinned_by: [], workflow_versions: 1 },
      ]),
    "GET /api/worlds/world_1/packages": () => Response.json(enabled),
    "GET /api/worlds/world_1/packages/echo/switch": () =>
      Response.json({ ...unchanged, params: { added: ["speed"], removed: [], changed: [] }, permissions: { added: ["network: eu.example.org"], removed: [], changed: [] } }),
    "PUT /api/worlds/world_1/packages/echo": async (req) => {
      const body = (await req.json()) as { version: string };
      bodies.push(body);
      enabled = [{ name: "echo", ...body }];
      return Response.json(enabled[0]);
    },
    "DELETE /api/packages/echo/1.0.0": () =>
      Response.json({ detail: "echo 1.0.0 is in use: world Video pins it; Say it v1 in world Video was saved with it" }, { status: 409 }),
  });
  const { screen, router } = await mountAt("/worlds/world_1/connectors/echo");
  await expect.element(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Echo versions");
  const rows = screen.getByRole("list", { name: "Versions of echo" }).getByRole("listitem");
  await expect.element(rows.nth(0)).toMatchTextContent(/0\.9\.0\s*kind: 'robot'.*No world pins it · recorded by 0 saved workflow versions\s*stage\s*Invalid Manifest\s*Uninstall$/);
  await expect.element(rows.nth(1)).toMatchTextContent(/1\.0\.0\s*Says it back\.\s*Pinned by Video, Other · recorded by 3 saved workflow versions\s*stage\s*Installed\s*Enabled here\s*Disable\s*Uninstall$/);
  await expect.element(rows.nth(2)).toMatchTextContent(/2\.0\.0\s*Says it back\. · commit abcdef123456\s*No world pins it · recorded by 1 saved workflow version\s*stage\s*Installed\s*Switch to this version\s*Uninstall$/);
  expect(screen.getByRole("button", { name: "Switch to echo 0.9.0" }).elements()).toHaveLength(0); // invalid: never enabled

  await screen.getByRole("button", { name: "Switch to echo 2.0.0" }).click();
  const shown = screen.getByRole("region", { name: "Switch echo to 2.0.0" });
  await expect.element(shown).toMatchTextContent(/Switch Echo from 1\.0\.0 to 2\.0\.0/);
  await expect.element(shown).toMatchTextContent(/Params\s*Added speed/);
  await expect.element(shown).toMatchTextContent(/Permissions\s*Added network: eu\.example\.org/);
  await shown.getByRole("button", { name: "Keep 1.0.0" }).click();
  expect(bodies).toEqual([]);
  await screen.getByRole("button", { name: "Switch to echo 2.0.0" }).click();
  await shown.getByRole("button", { name: "Switch to 2.0.0" }).click();
  await expect.element(rows.nth(2)).toMatchTextContent(/2\.0\.0.*Enabled here/);
  await expect.element(rows.nth(1)).toMatchTextContent(/1\.0\.0.*Switch to this version/);
  expect(bodies).toEqual([{ version: "2.0.0" }]);

  await screen.getByRole("button", { name: "Uninstall echo 1.0.0" }).click();
  expect(await answerConfirm(screen, "Uninstall")).toContain("Uninstall echo 1.0.0?");
  await expect.element(rows.nth(1).getByRole("alert")).toHaveTextContent("echo 1.0.0 is in use: world Video pins it; Say it v1 in world Video was saved with it");
  expect(calls).toContain("DELETE /api/packages/echo/1.0.0");

  await screen.getByRole("link", { name: "‹ Connectors" }).first().click();
  expect(router.state.location.pathname).toBe("/worlds/world_1/connectors");
});

test("a package with no version left says so", async () => {
  fakeFetch({
    "GET /api/packages/gone/versions": () => Response.json({ detail: "gone is not installed" }, { status: 404 }),
    "GET /api/worlds/world_1/packages": () => Response.json([]),
  });
  const { screen } = await mountAt("/worlds/world_1/connectors/gone");
  await expect.element(screen.getByRole("heading", { level: 1 })).toHaveTextContent("gone is not installed");
});
