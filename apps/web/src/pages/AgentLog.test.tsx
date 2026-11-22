import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { Agent, LogEntry } from "../api/events";
import { FakeEventSource, fakeFetch, run, stage, task } from "../test/fakes";
import { mountAt } from "../test/mount";

const ts = "2026-10-26T10:00:01+00:00";
const researching = run("running", [stage(0, "research", "swarm", "running")], { spent_usd: 0.0123 });
const agent = (over: Partial<Agent> = {}): Agent => ({
  id: "agent_1",
  world_id: "world_1",
  run_id: "run_1",
  stage_id: "stage_0",
  name: "worker-1",
  role: "worker",
  status: "working",
  session_id: "session-1",
  cost_usd: 0.0123,
  created_at: ts,
  updated_at: ts,
  skills: ["world_1:hooks"],
  model: "claude-haiku-4-5",
  ...over,
});
const entry = (seq: number, over: Partial<LogEntry> = {}): LogEntry => ({
  seq,
  agent: "worker-1",
  stage_id: "stage_0",
  kind: "tool",
  ts,
  tool: "mcp__swarmify__query_graph",
  input: {},
  detail: "",
  ...over,
});

/** A desktop opens an agent stage on its Transcript; these read the Summary. */
const summary = (screen: Awaited<ReturnType<typeof mountAt>>["screen"]) => screen.getByRole("button", { name: "Summary" }).click();

beforeEach(() => FakeEventSource.install());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("a swarm stage shows its agents as cards saying what each does now, and a live log in words", async () => {
  const claim = entry(2, { tool: "mcp__swarmify__claim_task", input: { task_id: "task_1" } });
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(researching),
    "GET /api/runs/run_1/agents": () => Response.json([agent({ skills: ["world_1:hooks", "deep-research"] })]),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json([entry(1), claim]),
    "GET /api/runs/run_1/stages/stage_0/tasks": () => Response.json([task("task_1", "claimed", { title: "Fact-check the candidates" })]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  await summary(screen);
  const card = screen.getByTestId("agent");
  await expect.element(card).toMatchTextContent(/Worker 1\s*working\s*worker · \$0\.0123 · claude-haiku-4-5/);
  await expect.element(card.getByTestId("doing")).toHaveTextContent("Claimed: Fact-check the candidates");
  await expect.element(card.getByText("2 skills")).toBeVisible();
  await expect.element(card.getByText("world_1:hooks, deep-research")).not.toBeVisible();
  await expect.element(screen.getByTestId("log")).toMatchTextContent(/10:00:01 Worker 1: Searching the world's memory/);
  expect(screen.getByTestId("run-cost").query()).toBeNull(); // one spend line, under the result

  FakeEventSource.emit("agent.logged", {
    payload: entry(3, { tool: "WebSearch", input: { query: "kookaburra call" } }),
  });
  FakeEventSource.emit("agent.logged", { payload: entry(1) });
  FakeEventSource.emit("agent.logged", { payload: entry(4, { stage_id: "stage_9", tool: "elsewhere" }) });
  await expect.element(card.getByTestId("doing")).toHaveTextContent('Searching the web for "kookaburra call"');
  FakeEventSource.emit("agent.logged", {
    payload: entry(5, { kind: "denied", tool: "Bash", detail: "Bash is not allowed in this stage." }),
  });
  FakeEventSource.emit("agent.updated", { payload: agent({ status: "done", cost_usd: 0.05 }) });
  await expect.element(screen.getByTestId("log")).toMatchTextContent(/kookaburra call"\s*10:00:01 Worker 1: Refused Bash: Bash is not allowed in this stage\.$/);
  await expect.element(card).toMatchTextContent(/done\s*worker · \$0\.0500.*Finished/);
});

test("a JSON answer in the log collapses to one line that opens", async () => {
  const planned = entry(1, {
    agent: "planner",
    kind: "result",
    tool: null,
    detail: 'Session ended at an estimated $0.2105. {"tasks":[{"title":"a"},{"title":"b"}]}',
  });
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(researching),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json([planned]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  await summary(screen);
  const log = screen.getByTestId("log");
  await expect.element(log).toMatchTextContent(/Planner: Session ended at an estimated \$0\.2105\.\s*Planned 2 tasks\{/);
  await expect.element(log.getByText('"title": "a"', { exact: false })).not.toBeVisible();
  await log.getByText("Planned 2 tasks").click();
  await expect.element(log.getByText('"title": "a"', { exact: false })).toBeVisible();
});

test("before any agent starts the panel says what will appear", async () => {
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json({ ...researching, spent_usd: 0 }),
    "GET /api/runs/run_1/agents": () => Response.json([]),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json([]),
    "GET /api/runs/run_1/stages/stage_0/tasks": () => Response.json([]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  await summary(screen);
  await expect.element(screen.getByTestId("agents")).toMatchTextContent(/No agent has started yet/);
  await expect.element(screen.getByRole("region", { name: "Live log" })).toMatchTextContent(/Every step an agent takes appears here/);
});

test("a connector step shows what the sandbox blocked in its live log, under the connector's name", async () => {
  const trying = run("running", [stage(0, "try", "connector", "running", { calls: "intruder" })]);
  const blocked = (seq: number, detail: string) => entry(seq, { agent: "intruder", kind: "denied", tool: null, detail });
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(trying),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json([blocked(1, "Blocked by the sandbox: read /data/setup.json")]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  const log = screen.getByTestId("log");
  await expect.element(log).toMatchTextContent(/10:00:01 intruder: Blocked by the sandbox: read \/data\/setup\.json/);
  FakeEventSource.emit("agent.logged", { payload: blocked(2, "Blocked by the sandbox: connect example.com:443") });
  await expect.element(log).toMatchTextContent(/intruder: Blocked by the sandbox: connect example\.com:443$/);
});

test("live log lines fade up as they arrive; the lines there on first paint do not", async () => {
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(researching),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json([entry(1), entry(2)]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  await summary(screen);
  const log = screen.getByTestId("log");
  await vi.waitFor(() => expect(log.getByRole("listitem").elements()).toHaveLength(2));
  FakeEventSource.emit("agent.logged", { payload: entry(3, { tool: "WebSearch", input: { query: "owls" } }) });
  await vi.waitFor(() => expect(log.getByRole("listitem").elements()).toHaveLength(3));
  const arrived = log.getByRole("listitem").elements().map((li) => li.classList.contains("arrived"));
  expect(arrived).toEqual([false, false, true]);
});

test("the Transcript shows each agent's words, tool calls that open, edits as diffs, and one agent", async () => {
  const at = (s: number) => `2026-10-26T10:00:${String(s).padStart(2, "0")}.000+00:00`;
  const log = [
    entry(1, { kind: "text", tool: null, input: null, detail: "I'll check the pricing page first.", ts: at(1) }),
    entry(2, { tool: "WebFetch", input: { url: "https://example.com/pricing" }, ref: "t1", ts: at(2) }),
    entry(3, { kind: "output", tool: "WebFetch", input: { chars: 38000, error: false }, detail: "Free: up to 3 seats", ref: "t1", ts: "2026-10-26T10:00:03.200+00:00" }),
    entry(4, { tool: "Edit", input: { file_path: "/w/runs/r/s/report.md", old_string: "# Report\nold line\n", new_string: "# Report\nnew line\n" }, ref: "t2", ts: at(4) }),
    entry(5, { agent: "worker-2", kind: "text", tool: null, input: null, detail: "Item 10 is too old, skipping it.", ts: at(5) }),
  ];
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(researching),
    "GET /api/runs/run_1/agents": () => Response.json([agent(), agent({ id: "agent_2", name: "worker-2" })]),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json(log),
    "GET /api/runs/run_1/stages/stage_0/tasks": () => Response.json([]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  const tx = screen.getByTestId("transcript");
  await expect.element(tx).toMatchTextContent(/Worker 1\s*0:00\s*I'll check the pricing page first\./);
  await expect.element(tx).toMatchTextContent(/Worker 2\s*0:04\s*Item 10 is too old/);

  const fetch = tx.getByTestId("tool-row").first();
  await expect.element(fetch).toMatchTextContent(/WebFetch\s*example\.com\/pricing\s*1\.2 s/);
  await expect.element(fetch.getByText("Free: up to 3 seats", { exact: false })).not.toBeVisible();
  await fetch.getByText("WebFetch").click();
  await expect.element(fetch.getByText("Free: up to 3 seats", { exact: false })).toBeVisible();
  await expect.element(fetch).toMatchTextContent(/\(38K chars\)/);

  const edit = tx.getByTestId("tool-row").nth(1);
  await expect.element(edit).toMatchTextContent(/Edit\s*s\/report\.md\s*-1 \+1 lines/);
  await edit.getByText("Edit").click();
  await expect.element(edit.getByTestId("diff")).toHaveTextContent("- old line+ new line");

  await screen.getByTestId("transcript-agent").selectOptions("Worker 2");
  await expect.element(tx).not.toMatchTextContent(/pricing/);
  await expect.element(tx).toMatchTextContent(/Item 10 is too old/);

  // A new line streams in; the Summary never shows the words or the results.
  await screen.getByTestId("transcript-agent").selectOptions("Every agent");
  FakeEventSource.emit("agent.logged", { payload: entry(6, { kind: "text", tool: null, input: null, detail: "Both agree.", ts: at(9) }) });
  await expect.element(tx).toMatchTextContent(/Both agree\.$/);
  await summary(screen);
  await expect.element(screen.getByTestId("log")).not.toMatchTextContent(/Both agree|Free: up to/);
  await expect.element(screen.getByTestId("agent").first().getByTestId("doing")).toHaveTextContent("Edited s/report.md");
});

test("a running stage takes a message to everyone or one agent, and the Transcript shows its receipt", async () => {
  const sent: unknown[] = [];
  const queued = (seq: number, who: string, over: Record<string, unknown> = {}) =>
    entry(seq, { agent: who, kind: "message", tool: null, detail: "Skip anything older than 30 days.", ref: "change_1", input: { state: "queued", to: null, interrupt: false, why: "mid-turn and gets this at its next turn.", ...over } });
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(researching),
    "GET /api/runs/run_1/agents": () => Response.json([agent(), agent({ id: "agent_2", name: "worker-2" }), agent({ id: "agent_3", name: "planner", role: "planner", status: "done" })]),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json([queued(1, "worker-1"), queued(2, "worker-2"), queued(3, "worker-1", { state: "delivered", why: null })]),
    "GET /api/runs/run_1/stages/stage_0/tasks": () => Response.json([]),
    "POST /api/runs/run_1/messages": async (req) => {
      sent.push(await req.json());
      return Response.json({}, { status: 201 });
    },
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  const message = screen.getByTestId("message");
  await expect.element(message).toMatchTextContent(/You to everyone still working\s*Skip anything older than 30 days\./);
  await expect.element(message.getByTestId("receipt")).toHaveTextContent("Delivered to 1 agent. Queued: Worker 2 is mid-turn and gets this at its next turn.");
  FakeEventSource.emit("agent.logged", { payload: queued(4, "worker-2", { state: "delivered", why: null }) });
  await expect.element(message.getByTestId("receipt")).toHaveTextContent("Delivered to 2 agents, saved as a run change");
  await summary(screen);
  await expect.element(screen.getByRole("region", { name: "Live log" })).toMatchTextContent(/Nothing yet/);

  const box = screen.getByTestId("message-box");
  await expect.element(box.getByRole("button", { name: "Everyone working (2)" })).toHaveAttribute("aria-pressed", "true");
  await expect.element(box.getByRole("button", { name: /Planner/ })).not.toBeInTheDocument();
  await expect.element(box.getByRole("button", { name: "Send" })).toBeDisabled();
  await box.getByRole("textbox").fill("Use the filing, not the blog post.");
  await box.getByRole("button", { name: /Worker 2/ }).click();
  await box.getByRole("button", { name: "Stop and do this instead" }).click();
  await expect.poll(() => sent).toEqual([{ stage: "stage_0", text: "Use the filing, not the blog post.", to: "worker-2", interrupt: true }]);
  await expect.element(box.getByRole("textbox")).toHaveValue("");
  await box.getByRole("textbox").fill("And keep it short.");
  await box.getByRole("button", { name: "Send" }).click();
  await expect.poll(() => sent.at(-1)).toEqual({ stage: "stage_0", text: "And keep it short.", to: "worker-2", interrupt: false });
});

test("no message box once no agent of the stage is working", async () => {
  fakeFetch({
    "GET /api/runs/run_1": () => Response.json(researching),
    "GET /api/runs/run_1/agents": () => Response.json([agent({ status: "done" })]),
    "GET /api/runs/run_1/stages/stage_0/log": () => Response.json([entry(1)]),
    "GET /api/runs/run_1/stages/stage_0/tasks": () => Response.json([]),
  });
  const { screen } = await mountAt("/worlds/world_1/runs/run_1");
  await expect.element(screen.getByTestId("transcript")).toBeVisible();
  await expect.element(screen.getByTestId("message-box")).not.toBeInTheDocument();
});

test("a message its Attempt ended before any agent took reads as not delivered", async () => {
  const { receipt } = await import("./Message");
  const said = (state: string, why: string | null) => ({ seq: 1, agent: "worker-1", stage_id: "stage_1", kind: "message", ts, detail: "Hi.", input: { state, why } }) as LogEntry;
  expect(receipt([said("queued", "between sessions and gets this first when its session resumes."), said("dropped", "the stage ended first")])).toEqual({
    queued: true,
    text: "Not delivered: the stage ended first",
  });
});
