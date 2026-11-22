import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { page } from "vitest/browser";

import "../index.css";

import type { components } from "../api/schema";
import { FakeEventSource, fakeFetch } from "../test/fakes";
import { answerConfirm, mountAt } from "../test/mount";

type WorldList = components["schemas"]["WorldList"];
type Entry = components["schemas"]["Entry"];

const ts = "2026-11-20T10:00:00+00:00";
const ideas: WorldList = { world_id: "world_1", name: "ideas", statuses: ["pitched", "making"], done: [], rejected: [], review: [], created_at: ts, updated_at: ts, published: null };
const entry = (key: string, title: string, claimed_by: string | null = null): Entry => ({
  list: "ideas", key, title, status: "pitched", score: null, fields: {}, file: null, claimed_by, runs: claimed_by ? [claimed_by] : [], created_at: ts, updated_at: ts,
});
const base = {
  "GET /api/worlds": () => Response.json([{ id: "world_1", name: "Video", purpose: "", created_at: ts }]),
  "GET /api/gates": () => Response.json([]),
};

beforeEach(async () => {
  FakeEventSource.install();
  await page.viewport(1280, 800);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("a world with no lists creates one from a form: name, statuses, and which are done, rejected or wait", async () => {
  let lists: WorldList[] = [];
  let sent: unknown = null;
  fakeFetch({
    ...base,
    "GET /api/worlds/world_1/lists": () => Response.json(lists),
    "PUT /api/worlds/world_1/lists/leads": async (req) => {
      sent = await req.json();
      lists = [{ ...ideas, name: "leads", statuses: ["new", "contacted", "won", "lost"] }];
      return Response.json(lists[0]);
    },
    "GET /api/worlds/world_1/lists/leads/entries": () => Response.json([]),
  });
  await page.viewport(390, 844);
  const { screen } = await mountAt("/worlds/world_1/lists");
  await expect.element(screen.getByTestId("no-lists")).toBeVisible();
  await screen.getByRole("button", { name: "New list" }).click();
  const form = screen.getByRole("dialog");
  await form.getByLabelText("Name", { exact: true }).fill("leads");
  await form.getByLabelText("Statuses, in order").fill("new, contacted, won,lost");
  await form.getByRole("group", { name: "Done" }).getByLabelText("won").click();
  await form.getByRole("group", { name: "Rejected" }).getByLabelText("lost").click();
  await form.getByRole("group", { name: "Waits for you" }).getByLabelText("new").click();
  const create = form.getByRole("button", { name: "Create list" });
  expect(create.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390);
  await create.click();
  await expect.element(screen.getByText("Created leads.", { exact: true })).toBeVisible(); // a toast
  expect(sent).toEqual({ statuses: ["new", "contacted", "won", "lost"], done: ["won"], rejected: ["lost"], review: ["new"] });
  await expect.element(screen.getByText("0 Entries in leads.")).toBeVisible();
});

test("a refused name keeps the form open with the API's reason", async () => {
  fakeFetch({
    ...base,
    "GET /api/worlds/world_1/lists": () => Response.json([]),
    "PUT /api/worlds/world_1/lists/Bad%20Name": () => Response.json({ detail: "list 'Bad Name': use lowercase letters, digits, - and _ (up to 64)" }, { status: 422 }),
  });
  const { screen } = await mountAt("/worlds/world_1/lists");
  await screen.getByRole("button", { name: "New list" }).click();
  await screen.getByLabelText("Name", { exact: true }).fill("Bad Name");
  await screen.getByLabelText("Statuses, in order").fill("a");
  await screen.getByRole("button", { name: "Create list" }).click();
  await expect.element(screen.getByText(/Could not create: list 'Bad Name': use lowercase/)).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Create list" })).toBeVisible();
});

test("deleting a list confirms first and says how many Entries go with it", async () => {
  let lists = [ideas];
  const calls = fakeFetch({
    ...base,
    "GET /api/worlds/world_1/lists": () => Response.json(lists),
    "GET /api/worlds/world_1/lists/ideas/entries": () => Response.json([entry("a", "A"), entry("b", "B"), entry("c", "C")]),
    "DELETE /api/worlds/world_1/lists/ideas": () => {
      lists = [];
      return new Response(null, { status: 204 });
    },
  });
  await page.viewport(390, 844);
  const { screen } = await mountAt("/worlds/world_1/lists");
  const del = screen.getByRole("button", { name: "Delete list ideas" });
  await expect.element(del).toBeVisible();
  expect(del.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  await del.click();
  expect(await answerConfirm(screen, "Cancel")).toContain("Delete the list ideas?Its 3 Entries go with it.This cannot be undone.");
  expect(calls).not.toContain("DELETE /api/worlds/world_1/lists/ideas");
  await del.click();
  await answerConfirm(screen, "Delete");
  await expect.element(screen.getByText("Deleted ideas and its 3 Entries.", { exact: true })).toBeVisible();
  await expect.element(screen.getByTestId("no-lists")).toBeVisible();
});

test("an Entry deletes from its sheet after a confirm; one a live run holds is refused with the reason", async () => {
  let entries = [entry("cats", "Cats"), entry("dogs", "Dogs", "run_1")];
  const calls = fakeFetch({
    ...base,
    "GET /api/worlds/world_1/lists": () => Response.json([ideas]),
    "GET /api/worlds/world_1/lists/ideas/entries": () => Response.json(entries),
    "GET /api/worlds/world_1/lists/ideas/entries/cats": () => Response.json({ ...entries[0], history: [] }),
    "GET /api/worlds/world_1/lists/ideas/entries/dogs": () => Response.json({ ...entry("dogs", "Dogs", "run_1"), history: [] }),
    "DELETE /api/worlds/world_1/lists/ideas/entries/cats": () => {
      entries = entries.filter((e) => e.key !== "cats");
      return new Response(null, { status: 204 });
    },
    "DELETE /api/worlds/world_1/lists/ideas/entries/dogs": () =>
      Response.json({ detail: "Entry 'dogs' of ideas is held by run run_1, which has not ended" }, { status: 409 }),
  });
  await page.viewport(390, 844);
  const { screen } = await mountAt("/worlds/world_1/lists");
  await screen.getByRole("button", { name: /^Cats/ }).click();
  const del = screen.getByTestId("entry-sheet").getByRole("button", { name: "Delete" });
  await expect.element(del).toBeVisible();
  expect(del.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  await del.click();
  expect(await answerConfirm(screen, "Cancel")).toContain("Delete Cats?It leaves ideas.This cannot be undone.");
  expect(calls).not.toContain("DELETE /api/worlds/world_1/lists/ideas/entries/cats");
  await screen.getByRole("button", { name: /^Cats/ }).click();
  await screen.getByTestId("entry-sheet").getByRole("button", { name: "Delete" }).click();
  await answerConfirm(screen, "Delete");
  await expect.element(screen.getByText("Deleted Cats.", { exact: true })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: /^Cats/ })).not.toBeInTheDocument();

  await screen.getByRole("button", { name: /^Dogs/ }).click();
  await screen.getByTestId("entry-sheet").getByRole("button", { name: "Delete" }).click();
  await answerConfirm(screen, "Delete");
  await expect.element(screen.getByText(/Could not delete: Entry 'dogs' of ideas is held by run run_1/)).toBeVisible();
  await expect.element(screen.getByRole("button", { name: /^Dogs/ })).toBeVisible();
});
