import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { page } from "vitest/browser";

import "../index.css";

import { FakeEventSource, fakeFetch } from "../test/fakes";
import { mountAt } from "../test/mount";

const ts = "2026-10-26T10:00:00+00:00";
const video = { id: "world_1", name: "Video", purpose: "", created_at: ts };
const check = (over: object = {}) => ({
  id: "pc_1",
  world_id: "world_1",
  run_id: "run_1",
  run_name: "Pilot",
  stage_id: "s1",
  stage_name: "sign",
  package: "browser",
  kind: "code",
  ask: "Enter the code we sent to ...67",
  site: "admin.myshop.com",
  has_screenshot: true,
  opened_at: ts,
  closes_at: ts,
  ...over,
});

beforeEach(async () => {
  FakeEventSource.install();
  await page.viewport(390, 844);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("on a phone, Needs you shows a Person Check first and sends the code typed", async () => {
  let open = [check()];
  const sent: unknown[] = [];
  fakeFetch({
    "GET /api/worlds": () => Response.json([video]),
    "GET /api/gates": () => Response.json([]),
    "GET /api/notices": () => Response.json([{ kind: "person_check", text: "A run is waiting", world_id: "world_1", run_id: "run_1" }]),
    "GET /api/person-checks": () => Response.json(open),
    "POST /api/person-checks/pc_1": async (req) => {
      sent.push(await req.json());
      open = [];
      return new Response(null, { status: 204 });
    },
  });
  const { screen } = await mountAt("/gates");
  const card = screen.getByTestId("person-check");
  await expect.element(card.getByRole("status")).toHaveTextContent("Pilot is waiting for you to sign in to admin.myshop.com. Open the run");
  await expect.element(card.getByRole("img")).toHaveAttribute("src", "/api/person-checks/pc_1/screenshot");
  const field = card.getByLabelText("Enter the code we sent to ...67");
  await expect.element(field).toHaveAttribute("inputmode", "numeric");
  await expect.element(field).toHaveAttribute("autocomplete", "one-time-code");
  await expect.element(card).toMatchTextContent(/The run waits 10 minutes, then stops\./);
  // the notice is the push; on the page the card is where it is answered
  expect(screen.getByTestId("notices-person_check").elements()).toHaveLength(0);
  const send = card.getByRole("button", { name: "Send code" });
  expect(send.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390);

  await field.fill("481516");
  await send.click();
  await expect.element(screen.getByTestId("person-check")).not.toBeInTheDocument();
  expect(sent).toEqual([{ code: "481516" }]);
});

test("the run page shows its own look check with Try again and Stop the stage", async () => {
  const sent: unknown[] = [];
  fakeFetch({
    "GET /api/runs/run_1/person-checks": () => Response.json([check({ kind: "look", ask: "A captcha", site: null, has_screenshot: false })]),
    "POST /api/person-checks/pc_1": async (req) => {
      sent.push(await req.json());
      return new Response(null, { status: 204 });
    },
  });
  const { PersonChecks } = await import("./PersonChecks");
  const { render } = await import("vitest-browser-react");
  const screen = await render(<PersonChecks runId="run_1" />);
  const card = screen.getByTestId("person-check");
  await expect.element(card).toMatchTextContent(/Pilot is waiting for you to sign in\..*The site is showing a check the box cannot pass\./);
  await card.getByRole("button", { name: "Try again" }).click();
  await card.getByRole("button", { name: "Stop the stage" }).click();
  await vi.waitFor(() => expect(sent).toEqual([{ action: "try_again" }, { action: "stop" }]));
});
