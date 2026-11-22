import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { page } from "vitest/browser";

import "../index.css";

import type { Agent, ArtifactFile } from "../api/events";
import { artifact, FakeEventSource, fakeFetch, gate, run, stage } from "../test/fakes";
import { mountAt } from "../test/mount";
import { spendByStage } from "./RunSide";

const ts = "2026-10-26T10:00:00+00:00";
const agent = (id: string, runId: string, stageId: string, cost: number): Agent =>
  ({ id, world_id: "world_1", run_id: runId, stage_id: stageId, name: id, role: "worker", status: "done", session_id: null, skills: [], cost_usd: cost, created_at: ts, updated_at: ts, cast: {} }) as Agent;

/** Two findings of a type no code knows, scored and sourced, as an earlier stage made them. */
const findings: ArtifactFile[] = [
  artifact({ name: "a.md", path: "runs/run_1/stage_0/artifacts/a.md", type: "finding", title: "Rival ships a free tier", fields: { rank: 1, score: 92, sources: ["x", "y", "z"], line: "Pricing changed on Tuesday." } }),
  artifact({ name: "b.md", path: "runs/run_1/stage_0/artifacts/b.md", type: "finding", title: "Two teams merge", fields: { rank: 2, score: 87, sources: ["q"] }, body: "Confirmed in a filing." }),
];

beforeEach(async () => {
  FakeEventSource.install();
  await page.viewport(1280, 800);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("a pick gate's card has Decision, All and Ask tabs; its cards are any item type with scored chips, and picking still answers", async () => {
  const waiting = run("waiting_gate", [stage(0, "gather", "swarm", "done"), stage(1, "pick", "gate", "waiting", { ask: "Pick what goes in." }), stage(2, "draft", "swarm", "pending")]);
  const decided: unknown[] = [];
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(waiting),
    "GET /api/runs/run_1/artifacts": () => Response.json(findings),
    "GET /api/gates": () => Response.json([gate("pick", { stage_id: "stage_1", stage_index: 1, stage_name: "pick", ask: "Pick what goes in." })]),
    "POST /api/runs/run_1/decision": async (req) => {
      decided.push(await req.json());
      return Response.json(waiting);
    },
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1?stage=stage_1");
  const card = screen.getByTestId("stage-panel");
  await expect.element(card.getByTestId("stage-meta")).toMatchTextContent(/^Gate\s*step 2 of 3\s*waiting/);
  await expect.element(card.getByRole("tablist")).toMatchTextContent(/^Decision\s*All 2\s*Ask$/);
  const first = card.getByTestId("pick-card").first();
  await expect.element(first).toMatchTextContent(/Rival ships a free tier\s*Pricing changed on Tuesday\./);
  await expect.element(first.getByTestId("card-chips")).toMatchTextContent(/^Score 92\s*3 sources$/);
  await expect.element(card.getByTestId("pick-card").nth(1).getByTestId("card-chips")).toMatchTextContent(/^Score 87\s*1 source$/);

  await card.getByRole("tab", { name: "All 2" }).click();
  await expect.element(card.getByTestId("tab-all").getByTestId("item").first()).toMatchTextContent(/Rival ships a free tier\s*Pricing changed on Tuesday\.\s*Score 92/);
  await card.getByRole("tab", { name: "Ask" }).click();
  await expect.element(card.getByTestId("tab-ask").getByRole("button", { name: "Ask this run" })).toBeVisible();

  await card.getByRole("tab", { name: "Decision" }).click();
  await first.click();
  await card.getByRole("button", { name: "Continue with this pick" }).click();
  await vi.waitFor(() => expect(decided).toMatchObject([{ choice: "Rival ships a free tier", artifact: findings[0]!.path }]));
});

test("an agent stage's card has Activity, Items, Files and Details; a connector's has Result, Inputs and Details; prev and next move along", async () => {
  const done = run("done", [stage(0, "gather", "swarm", "done"), stage(1, "send", "connector", "done", { calls: "speech", inputs: ["a.md"] })]);
  fakeFetch({ "GET /api/runs/run_1": () => Response.json(done), "GET /api/runs/run_1/artifacts": () => Response.json(findings) });
  const { screen, router } = await mountAt("/worlds/world_1/runs/run_1?stage=stage_0");
  const card = screen.getByTestId("stage-panel");
  await expect.element(card.getByRole("tablist")).toMatchTextContent(/^Activity\s*Items\s*Files\s*2\s*Details$/);
  await card.getByRole("tab", { name: "Details" }).click();
  await expect.element(card.getByTestId("stage-details")).toMatchTextContent(/Kind\s*Agents/);
  await card.getByRole("button", { name: "Next step" }).click();
  await vi.waitFor(() => expect(router.state.location.search).toEqual({ stage: "stage_1" }));
  await expect.element(card.getByRole("tablist")).toMatchTextContent(/^Result\s*Inputs\s*1\s*Details$/);
  await expect.element(card.getByRole("button", { name: "Next step" })).toBeDisabled();
  await card.getByRole("button", { name: "Previous step" }).click();
  await vi.waitFor(() => expect(router.state.location.search).toEqual({ stage: "stage_0" }));
});

test("the side column lists every file the run made with its line by type, and spend by step adds up to the run's spend", async () => {
  const done = run(
    "done",
    [
      stage(0, "gather", "swarm", "done"),
      stage(1, "each", "workflow", "done", { items: [{ key: "1", title: "one", status: "done", child: { id: "run_c", world_id: "world_1", name: "each one" } }] } as never),
      stage(2, "retro", "retro", "done"),
    ],
    { spent_usd: 1.79, budget_usd: 5 },
  );
  const table = artifact({ name: "findings.csv", path: "runs/run_1/stage_0/artifacts/findings.csv", type: "file", body: "a,b,c\n1,2,3\n4,5,6\n", stage: "stage_0" });
  const report = artifact({ name: "report.md", path: "runs/run_1/stage_1/artifacts/report.md", type: "file", body: "one two three four", stage: "stage_1" });
  const verdict = artifact({ name: "verdict.md", path: "runs/run_1/stage_0/verdict.md", type: "verdict" });
  const agents = [agent("w1", "run_1", "stage_0", 0.42), agent("w2", "run_1", "stage_0", 0.88), agent("c1", "run_c", "sx", 0.43), agent("r", "run_1", "stage_2", 0.06)];
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(done),
    "GET /api/runs/run_1/artifacts": () => Response.json([table, report, verdict]),
    "GET /api/runs/run_1/agents": () => Response.json(agents),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1?stage=stage_0");
  const side = screen.getByTestId("made");
  await expect.element(side).toMatchTextContent(/Made in this run\s*2 files/);
  await expect.element(side.getByTestId("made-file").first()).toMatchTextContent(/^findings\.csv\s*2 rows, 3 columns/);
  await expect.element(side.getByTestId("made-file").nth(1)).toMatchTextContent(/^report\.md\s*4 words/);
  await expect.element(side.getByRole("link", { name: /^findings\.csv.*made by gather/ })).toBeVisible();

  const rows = screen.getByTestId("spend-row");
  await expect.element(rows.first()).toMatchTextContent(/^gather\s*\$1\.30/);
  await expect.element(rows.nth(1)).toMatchTextContent(/^each\s*\$0\.43/);
  await expect.element(rows.nth(2)).toMatchTextContent(/^retro\s*\$0\.06/);
  const { rows: by, below } = spendByStage(done, agents);
  expect(by.reduce((sum, r) => sum + r.usd, below)).toBeCloseTo(done.spent_usd);
});

test("a stage held for its not_before time says when it runs, in its card, its row and the hero", async () => {
  const at = new Date(Date.now() + 3 * 3600_000).toISOString();
  const held = run("running", [
    stage(0, "approve", "gate", "done"),
    stage(1, "post", "connector", "running", { calls: "publish", started_at: ts, runs_at: at, config: { not_before: "17:00 America/Toronto" } }),
  ]);
  fakeFetch({ "GET /api/runs/run_1": () => Response.json(held), "GET /api/runs/run_1/artifacts": () => Response.json([]) });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1?stage=stage_1");
  const when = new Date(at).toLocaleString();
  await expect.element(screen.getByTestId("stage-holding")).toHaveTextContent(`Waits until ${when}, its not before time (17:00 America/Toronto). It holds no slot until then.`);
  await expect.element(screen.getByTestId("stage-meta")).toMatchTextContent(/waits until/);
  await expect.element(screen.getByTestId("hero-sub")).toHaveTextContent(`post, step 2 of 2. Waits until ${when}. Nothing needs you yet.`);
});
