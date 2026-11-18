import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { artifact, FakeEventSource, fakeFetch, gate, run, stage } from "../test/fakes";
import { mountAt } from "../test/mount";

const ts = "2026-10-26T10:00:00+00:00";
const research = { id: "world_2", name: "Research", purpose: "", created_at: ts };
const top = { id: "run_top", world_id: "world_1", name: "Weekly short" };
const mid = { id: "run_mid", world_id: "world_2", name: "Trends" };

beforeEach(() => FakeEventSource.install());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("the runs page links each child run to its parent, in whatever world it lives", async () => {
  const child = run("running", [stage(0, "research", "swarm", "running")], { ...mid, parent: top, parent_run_id: top.id });
  const plain = run("done", [stage(0, "x", "connector", "done")], { id: "run_alone", world_id: "world_2", name: "Alone" });
  fakeFetch({
    "GET /api/worlds/world_2": () => Response.json(research),
    "GET /api/worlds/world_2/runs": () => Response.json([child, plain]),
  });
  const { screen } = await mountAt("/worlds/world_2/runs");
  await expect.element(screen.getByTestId("run-list")).toMatchTextContent(/Trends.*child of Weekly short\s*running/);
  await expect
    .element(screen.getByRole("link", { name: "Weekly short" }))
    .toHaveAttribute("href", "/worlds/world_1/runs/run_top");
  expect(screen.getByTestId("child-of").elements()).toHaveLength(1);
});

test("a workflow stage links to its child run and the child links back to its parent", async () => {
  const parent = run("running", [stage(0, "trends", "workflow", "running", { child: mid }), stage(1, "pick", "gate", "pending")], {
    ...top,
    spent_usd: 0.25,
  });
  const agents = [
    { id: "a1", run_id: "run_mid", stage_id: "s9", name: "worker-1", status: "done", session_id: null, skills: [], cost_usd: 0.25, created_at: ts, updated_at: ts },
  ];
  fakeFetch({
    "GET /api/runs/run_top": () => Response.json(parent),
    "GET /api/runs/run_top/agents": () => Response.json(agents),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_top");
  await expect.element(screen.getByTestId("status-line")).toHaveTextContent("Trends waits on its child run Trends · step 1 of 2");
  await expect.element(screen.getByTestId("child-run")).toMatchTextContent("Child run: Trends");
  await expect.element(screen.getByRole("link", { name: "Trends" })).toHaveAttribute("href", "/worlds/world_2/runs/run_mid");
});

test("a child run's page names its parent and what it shares with it", async () => {
  const child = run("waiting_gate", [stage(0, "pick", "gate", "waiting")], { ...mid, parent: top, parent_run_id: top.id });
  fakeFetch({ "GET /api/runs/run_mid": () => Response.json(child) });
  const { screen } = await mountAt("/worlds/world_2/runs/run_mid");
  await expect.element(screen.getByTestId("child-of")).toMatchTextContent("child of Weekly short");
  await expect.element(screen.getByText(/spends that run's budget/)).toBeVisible();
  await expect.element(screen.getByRole("link", { name: "Weekly short" })).toHaveAttribute("href", "/worlds/world_1/runs/run_top");
});

test("the gates inbox labels a child run's gate with its parent", async () => {
  fakeFetch({
    "GET /api/worlds": () => Response.json([research]),
    "GET /api/gates": () => Response.json([gate("pick", { run_id: "run_mid", world_id: "world_2", run_name: "Trends", parent_run_id: top.id, parent: top })]),
  });
  const { screen } = await mountAt("/gates");
  const card = screen.getByTestId("gate-run_mid");
  await expect.element(card).toMatchTextContent(/Research\s*Trends\s*stage 2, pick\s*waiting .*child of Weekly short/);
  await expect.element(card.getByRole("link", { name: "Weekly short" })).toHaveAttribute("href", "/worlds/world_1/runs/run_top");
});

test("a fan-out stage lists its items with a status chip and a link to each child", async () => {
  const items = [
    { key: "p1", title: "Paragraph 1", status: "done", approved: false, child: { id: "run_p1", world_id: "world_2", name: "Translate: Paragraph 1" } },
    { key: "p2", title: "Paragraph 2", status: "failed", approved: false, error: "could not translate", child: { id: "run_p2", world_id: "world_2", name: "Translate: Paragraph 2" } },
    { key: "p3", title: "Paragraph 3", status: "pending", approved: false, child: null },
  ];
  const parent = run("running", [stage(0, "translate each", "workflow", "running", { items })], top);
  fakeFetch({
    "GET /api/runs/run_top": () => Response.json(parent),
    "GET /api/runs/run_top/agents": () => Response.json([]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_top");
  const list = screen.getByTestId("stage-items");
  await expect.element(list).toMatchTextContent(/Paragraph 1\s*done.*Paragraph 2\s*failed\s*could not translate.*Paragraph 3\s*pending/);
  await expect.element(list.getByRole("link", { name: "Paragraph 2" })).toHaveAttribute("href", "/worlds/world_2/runs/run_p2");
  expect(list.getByRole("link").elements()).toHaveLength(2);
});

test("an items gate retries the failed items", async () => {
  const sent: unknown[] = [];
  const items = gate("items", { run_id: "run_top", world_id: "world_1", run_name: "Weekly short", ask: "1 of 3 items did not finish" });
  fakeFetch({
    "GET /api/worlds": () => Response.json([research]),
    "GET /api/gates": () => Response.json([items]),
    "POST /api/runs/run_top/decision": async (req: Request) => {
      sent.push(await req.json());
      return Response.json(run("running", [], top));
    },
  });
  const { screen } = await mountAt("/gates");
  await screen.getByRole("button", { name: "Retry the failed items" }).click();
  await expect.poll(() => sent).toEqual([{ note: "", choice: "retry" }]);
});

test("an approve items gate shows each item's output and sends approvals and send-backs together", async () => {
  const sent: unknown[] = [];
  const child = (id: string) => ({ id, world_id: "world_2", name: `Make: ${id}` });
  const items = [
    { key: "p1", title: "Paragraph 1", status: "done", approved: true, child: child("run_p1") },
    { key: "p2", title: "Paragraph 2", status: "done", approved: false, child: child("run_p2") },
    { key: "p3", title: "Paragraph 3", status: "failed", approved: false, error: "could not translate", child: child("run_p3") },
  ];
  const parent = run("waiting_gate", [stage(0, "split", "connector", "done"), stage(1, "translate each", "workflow", "waiting", { items })], top);
  const review = gate("approve_items", { run_id: "run_top", world_id: "world_1", stage_name: "translate each" });
  fakeFetch({
    "GET /api/worlds": () => Response.json([research]),
    "GET /api/gates": () => Response.json([review]),
    "GET /api/runs/run_top": () => Response.json(parent),
    "GET /api/runs/run_top/stages/stage_1/artifacts": (req: Request) => {
      const key = new URL(req.url).searchParams.get("folder");
      const name = `${key}.png`;
      return Response.json([{ ...artifact({ name, path: `runs/run_top/stage_1/artifacts/${key}/${name}`, title: name, type: "file" }) }]);
    },
    "POST /api/runs/run_top/items/review": async (req: Request) => {
      sent.push(await req.json());
      return Response.json(parent);
    },
  });
  const { screen } = await mountAt("/gates");
  const list = screen.getByTestId("review-items");
  await expect.element(list).toMatchTextContent(/Paragraph 1\s*approved.*Paragraph 2\s*done.*Paragraph 3\s*failed\s*could not translate/);
  await expect.element(list.getByRole("img", { name: "p2.png" })).toBeVisible();
  await expect.element(list.getByRole("img", { name: "p1.png" })).toBeVisible();
  const send = screen.getByRole("button", { name: "Send the verdicts" });
  await expect.element(send).toBeDisabled();
  await list.getByRole("radio", { name: "Approve Paragraph 2" }).click();
  await list.getByRole("radio", { name: "Send back Paragraph 3" }).click();
  await list.getByRole("textbox", { name: "What to change in Paragraph 3" }).fill("Warmer, less stiff");
  await send.click();
  await expect.poll(() => sent).toEqual([{ approve: ["p2"], send_back: { p3: "Warmer, less stiff" } }]);
});

test("a partial approval keeps the approve items gate open with the items as they now stand", async () => {
  const child = (id: string) => ({ id, world_id: "world_2", name: `Make: ${id}` });
  const items = (p1: boolean) => [
    { key: "p1", title: "Paragraph 1", status: "done", approved: p1, child: child("run_p1") },
    { key: "p2", title: "Paragraph 2", status: "done", approved: false, child: child("run_p2") },
  ];
  let approved = false;
  const parent = () => run("waiting_gate", [stage(0, "split", "connector", "done"), stage(1, "translate each", "workflow", "waiting", { items: items(approved) })], top);
  const review = gate("approve_items", { run_id: "run_top", world_id: "world_1", stage_name: "translate each" });
  fakeFetch({
    "GET /api/worlds": () => Response.json([research]),
    "GET /api/gates": () => Response.json([review]),
    "GET /api/runs/run_top": () => Response.json(parent()),
    "GET /api/runs/run_top/stages/stage_1/artifacts": () => Response.json([]),
    "POST /api/runs/run_top/items/review": () => {
      approved = true;
      return Response.json(parent());
    },
  });
  const { screen } = await mountAt("/gates");
  const list = screen.getByTestId("review-items");
  await list.getByRole("radio", { name: "Approve Paragraph 1" }).click();
  await screen.getByRole("button", { name: "Send the verdicts" }).click();
  await expect.element(list).toMatchTextContent(/Paragraph 1\s*approved.*Paragraph 2\s*done/);
  expect(list.getByRole("radio", { name: "Approve Paragraph 1" }).elements()).toHaveLength(0);
  await expect.element(list.getByRole("radio", { name: "Approve Paragraph 2" })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Send the verdicts" })).toBeDisabled();
});

test("a fan-out child's gate names its item beside its parent", async () => {
  const child = gate("approve", { run_id: "run_p1", world_id: "world_2", run_name: "Translate: P1", parent_run_id: top.id, parent: top, item: "P1" });
  fakeFetch({
    "GET /api/worlds": () => Response.json([research]),
    "GET /api/gates": () => Response.json([child]),
    "GET /api/runs/run_p1/artifacts": () => Response.json([]),
  });
  const { screen } = await mountAt("/gates");
  await expect.element(screen.getByTestId("gate-run_p1")).toMatchTextContent(/child of Weekly short\s*item P1/);
});
