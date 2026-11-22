import { expect, test } from "vitest";

import type { LogEntry } from "../api/events";
import { run, stage } from "../test/fakes";
import { type Attempt, attemptLine, attemptLog, rerunWarning } from "./Attempts";

const one: Attempt = { number: 1, started_at: "2026-11-21T10:00:00Z", ended_at: "2026-11-21T10:01:40Z", spent_usd: 0.31, cause: null };
const two: Attempt = { number: 2, started_at: "2026-11-21T10:05:00Z", ended_at: "2026-11-21T10:06:10Z", spent_usd: 0.22, cause: { kind: "follow_up", note: "Cut it.", by: "operator", at: "2026-11-21T10:05:00Z" } };
const line = (seq: number, ts: string) => ({ seq, agent: "worker-1", stage_id: "stage_1", kind: "text", ts, detail: "" }) as LogEntry;

test("an Attempt tab reads its time and spend, and whose change started it", () => {
  expect(attemptLine(one, false)).toBe("1m 40s, $0.31");
  expect(attemptLine(two, false)).toBe("your change, 1m 10s, $0.22");
  expect(attemptLine(two, true)).toBe("your change, running");
});

test("each Attempt shows only its own log lines", () => {
  const log = [line(1, "2026-11-21T10:00:30Z"), line(2, "2026-11-21T10:05:30Z")];
  expect(attemptLog(log, [one, two], 1).map((e) => e.seq)).toEqual([1]);
  expect(attemptLog(log, [one, two], 2).map((e) => e.seq)).toEqual([2]);
  expect(attemptLog(log, [one], 1)).toBe(log);
});

test("the warning names the later stages that run again and the gates that wait again", () => {
  const r = run("waiting_gate", [stage(0, "Draft", "swarm", "done"), stage(1, "Check", "swarm", "done"), stage(2, "Approve", "gate", "waiting")]);
  const p = { before: { Draft: "done", Check: "done", Approve: "waiting" }, in_place: true } as never;
  expect(rerunWarning(r, r.stages[0]!, p)).toBe("A change here reruns Check and Approve. Approve waits for you again.");
  const ended = { before: { Draft: "done" }, in_place: false, fork_from: "Draft" } as never;
  expect(rerunWarning(r, r.stages[0]!, ended)).toBe("Nothing after it runs again. The run has ended, so this continues as a new run from Draft.");
});
