import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { page } from "vitest/browser";

import "../index.css";

import { FakeEventSource, fakeFetch } from "../test/fakes";
import { mountAt } from "../test/mount";
import { statusLine, suggestName } from "./Logins";

const ts = "2026-10-26T10:00:00+00:00";
const video = { id: "world_1", name: "Video", purpose: "", created_at: ts };
const login = (over: object = {}) => ({
  name: "shop_admin",
  domains: ["admin.myshop.com", "*.myshop.com", "cdn.myshop.com"],
  username: "ana",
  has_totp: true,
  has_session: true,
  created_at: ts,
  updated_at: ts,
  rotated_at: ts,
  last_used_at: null,
  workflows: [{ id: "wf_1", name: "Pilot" }],
  last_use: null,
  ...over,
});
const base = {
  "GET /api/worlds/world_1": () => Response.json(video),
  "GET /api/worlds/world_1/notifications": () => Response.json({ slack_webhook: null }),
};

beforeEach(async () => {
  FakeEventSource.install();
  await page.viewport(390, 844);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const noSideScroll = () => expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390);

test("with no logins the row says what a login does, then adds one from the sheet", async () => {
  const bodies: unknown[] = [];
  let tries = 0;
  fakeFetch({
    ...base,
    "GET /api/worlds/world_1/logins": () => Response.json([]),
    "POST /api/worlds/world_1/logins": async (req) => {
      bodies.push(await req.json());
      tries += 1;
      if (tries === 1) return Response.json({ detail: "Domains: *.com is too broad" }, { status: 422 });
      return Response.json(login({ name: "myshop_admin", workflows: [], has_session: false }), { status: 201 });
    },
  });
  const { screen } = await mountAt("/worlds/world_1/settings");
  const row = screen.getByRole("region", { name: "Logins" });
  await expect.element(row).toMatchTextContent(/Let a workflow sign in to a site for you.*Name it after the account, like shop_admin\..*List every domain the site uses\./);
  noSideScroll();
  await row.getByRole("button", { name: "Add a login" }).click();

  const sheet = screen.getByRole("dialog", { name: "Add a login" });
  // what it is called and where come before the password
  const labels = sheet.getByText(/^(Name|Domains|Username|Password|Authenticator secret \(optional\))$/).elements().map((e) => e.textContent);
  expect(labels).toEqual(["Name", "Domains", "Username", "Password", "Authenticator secret (optional)"]);
  await sheet.getByLabelText("Domains").fill("*.com");
  await sheet.getByLabelText("Domains").fill("admin.myshop.com\n*.com");
  await expect.element(sheet.getByLabelText("Name")).toHaveValue("myshop_admin"); // from the first domain
  await sheet.getByLabelText("Username").fill("ana");
  const password = sheet.getByLabelText("Password", { exact: true });
  await password.fill("Pa55-word");
  await expect.element(password).toHaveAttribute("type", "password");
  await expect.element(password).toHaveAttribute("autocomplete", "new-password");
  await sheet.getByRole("button", { name: "Show" }).click();
  await expect.element(password).toHaveAttribute("type", "text");
  await expect.element(sheet).toMatchTextContent(/Recordings of each run show the pages, never the password\./);
  await sheet.getByRole("button", { name: "Save login" }).click();
  await expect.element(sheet.getByRole("alert")).toHaveTextContent("Domains: *.com is too broad");
  noSideScroll();
  await sheet.getByLabelText("Domains").fill("admin.myshop.com");
  await sheet.getByRole("button", { name: "Save login" }).click();
  await expect.element(row.getByRole("button", { name: /myshop_admin/ })).toBeVisible();
  expect(bodies[1]).toEqual({ name: "myshop_admin", domains: ["admin.myshop.com"], username: "ana", password: "Pa55-word", totp_secret: null });
});

test("a login's card and sheet: status, uses, rotate, and a delete refused while a workflow names it", async () => {
  const used = { run_id: "run_1", stage_id: "s1", run_name: "Pilot", stage_name: "sign", package: "browser", result: "signed_in", at: new Date(Date.now() - 2 * 3600_000).toISOString() };
  const rotations: unknown[] = [];
  fakeFetch({
    ...base,
    "GET /api/worlds/world_1/logins": () => Response.json([login({ last_use: used })]),
    "GET /api/worlds/world_1/logins/shop_admin/uses": () => Response.json([used]),
    "POST /api/worlds/world_1/logins/shop_admin/rotate": async (req) => {
      rotations.push(await req.json());
      return Response.json(login({ has_session: false, last_use: used }));
    },
  });
  const { screen } = await mountAt("/worlds/world_1/settings");
  const card = screen.getByRole("button", { name: "shop_admin, ana" });
  await expect.element(card).toMatchTextContent(/shop_admin.*ana.*admin\.myshop\.com.*\*\.myshop\.com.*\+1.*Saved session, used 2 h ago by Pilot.*In Pilot/);
  expect(card.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  noSideScroll();
  await card.click();

  const sheet = screen.getByRole("dialog", { name: "shop_admin" });
  await expect.element(sheet).toMatchTextContent(/Authenticator\s*set.*Saved session\s*kept/);
  await expect.element(sheet.getByRole("link", { name: "Pilot · sign" })).toBeVisible();
  await expect.element(sheet.getByRole("region", { name: "Used by" })).toMatchTextContent(/Signed in/);
  expect(sheet.element().textContent).not.toContain("Pa55");

  await sheet.getByRole("button", { name: "Delete login" }).click();
  await expect.element(sheet.getByTestId("delete-refused")).toHaveTextContent("Pilot names this login. Remove it from their stages first.");
  expect(sheet.getByRole("button", { name: "Delete", exact: true }).elements()).toHaveLength(0);

  await sheet.getByRole("button", { name: "Change password" }).click();
  await expect.element(sheet).toMatchTextContent(/Saving signs this login out everywhere on this box\./);
  await sheet.getByLabelText("New password", { exact: true }).fill("N3w-pass");
  await sheet.getByRole("button", { name: "Save password" }).click();
  await expect.element(sheet).toMatchTextContent(/Saved session\s*none/);
  expect(rotations).toEqual([{ password: "N3w-pass", totp_secret: null, clear_totp: false }]);
  noSideScroll();
});

test("a login no workflow names is deleted after Delete shop_admin?, and Not now keeps it", async () => {
  let deleted = 0;
  fakeFetch({
    ...base,
    "GET /api/worlds/world_1/logins": () => Response.json([login({ workflows: [] })]),
    "GET /api/worlds/world_1/logins/shop_admin/uses": () => Response.json([]),
    "DELETE /api/worlds/world_1/logins/shop_admin": () => {
      deleted += 1;
      return new Response(null, { status: 204 });
    },
  });
  const { screen } = await mountAt("/worlds/world_1/settings");
  await screen.getByRole("button", { name: "shop_admin, ana" }).click();
  const sheet = screen.getByRole("dialog", { name: "shop_admin" });
  await sheet.getByRole("button", { name: "Delete login" }).click();
  await expect.element(sheet).toMatchTextContent(/Delete shop_admin\? Its saved session goes too\./);
  await sheet.getByRole("button", { name: "Not now" }).click();
  expect(deleted).toBe(0);
  await sheet.getByRole("button", { name: "Delete login" }).click();
  await sheet.getByRole("button", { name: "Delete", exact: true }).click();
  await expect.element(screen.getByRole("button", { name: "shop_admin, ana" })).not.toBeInTheDocument();
  expect(deleted).toBe(1);
});

test("names and status lines read as the spec says", () => {
  expect(suggestName("admin.myshop.com")).toBe("myshop_admin");
  expect(suggestName("www.supplier.co")).toBe("supplier");
  expect(statusLine(login({ has_session: false }) as never)).toBe("Never used");
});
