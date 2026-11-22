import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { page } from "vitest/browser";

import { FakeEventSource, fakeFetch, run, stage } from "../test/fakes";
import { answerConfirm, mountAt } from "../test/mount";
import "../index.css";

const ts = "2026-10-26T10:00:00+00:00";
const video = { id: "world_1", name: "Video", purpose: "", created_at: ts };
const done = run("done", [stage(0, "render", "connector", "done")], { spent_usd: 2 });

beforeEach(async () => {
  FakeEventSource.install();
  await page.viewport(390, 844);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("the runs list leaves archived runs out of its rows and figures until Show archived", async () => {
  const asked: string[] = [];
  const old = run("aborted", [stage(0, "render", "connector", "done")], { id: "run_2", name: "Old one", spent_usd: 7, archived_at: ts });
  fakeFetch({
    "GET /api/worlds/world_1": () => Response.json(video),
    "GET /api/worlds/world_1/runs": (req) => {
      asked.push(new URL(req.url).search);
      return Response.json([done, old]);
    },
  });
  const { screen } = await mountAt("/worlds/world_1/runs");
  const list = screen.getByTestId("run-list");
  await expect.element(list).toMatchTextContent(/Render then pick/);
  expect(list.element().textContent).not.toMatch(/Old one/);
  await expect.element(screen.getByText(/^1 run, \$2\.00 spent/)).toBeVisible();
  expect(asked.every((q) => q.includes("archived=true"))).toBe(true);

  const toggle = screen.getByRole("button", { name: "Show archived (1)" });
  expect(toggle.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  await toggle.click();
  await expect.element(list).toMatchTextContent(/Old one.*archived/);
  await expect.element(screen.getByText(/^2 runs, \$9\.00 spent/)).toBeVisible();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390);

  // An archive from elsewhere leaves the list live.
  await toggle.click();
  FakeEventSource.emit("run.updated", { payload: { ...done, archived_at: ts } });
  await expect.element(screen.getByRole("button", { name: "Show archived (2)" })).toBeVisible();
  expect(screen.getByTestId("run-list").elements()).toHaveLength(0);
});

test("a finished run archives from its menu after a confirm, says so, and restores", async () => {
  const posted: string[] = [];
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(done),
    "POST /api/runs/run_1/archive": () => {
      posted.push("archive");
      return Response.json({ ...done, archived_at: ts });
    },
    "POST /api/runs/run_1/restore": () => {
      posted.push("restore");
      return Response.json({ ...done, archived_at: null });
    },
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  await screen.getByRole("button", { name: "Run actions" }).first().click();
  await screen.getByRole("menuitem", { name: /^Archive run/ }).click();
  expect(await answerConfirm(screen, "Archive")).toMatch(/Archive Render then pick\?.*child runs/);
  const note = screen.getByTestId("archived");
  await expect.element(note).toMatchTextContent(/archived.*Left out of the runs list/);
  const restore = note.getByRole("button", { name: "Restore" });
  expect(restore.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  await restore.click();
  await expect.element(screen.getByTestId("archived")).not.toBeInTheDocument();
  expect(posted).toEqual(["archive", "restore"]);
});

test("a live run offers no archive", async () => {
  fakeFetch({ "GET /api/runs/run_1": () => Response.json(run("running", [stage(0, "render", "connector", "running")])) });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  await screen.getByRole("button", { name: "Run actions" }).first().click();
  await expect.element(screen.getByRole("menuitem", { name: /^Pause/ })).toBeVisible();
  expect(screen.getByRole("menuitem", { name: /^Archive/ }).elements()).toHaveLength(0);
});
