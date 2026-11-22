import { afterEach, expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import "../index.css";

import { Progress } from "../Progress";
import { FakeEventSource } from "../test/fakes";
import { reloadGates, useOpenGates } from "./gates";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function Count() {
  const { gates } = useOpenGates();
  return <p>{gates === null ? "loading" : `${gates.length} open`}</p>;
}

test("reading the open gates never shows the loading line, and reads do not pile up", async () => {
  FakeEventSource.install();
  const answers: (() => void)[] = [];
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(
    () => new Promise<Response>((r) => answers.push(() => r(Response.json([], { headers: { "X-Event-Seq": "0" } })))),
  );
  const screen = await render(
    <>
      <Progress />
      <Count />
    </>,
  );
  // each read asks for the gates and the open Person Checks; count the gates' reads
  const reads = () => fetch.mock.calls.filter(([req]) => String((req as Request).url ?? req).includes("/api/gates")).length;
  const answerAll = () => answers.splice(0).forEach((a) => a());
  reloadGates();
  reloadGates(); // a burst of run updates while the slow first read is in flight
  await wait(300);
  expect(screen.getByTestId("route-progress").query()).toBeNull();
  expect(reads()).toBe(1);

  answerAll();
  await expect.element(screen.getByText("0 open")).toBeInTheDocument();
  await vi.waitFor(() => expect(reads()).toBe(2)); // one more, for what came in meanwhile
  answerAll();
  await wait(50);
  expect(reads()).toBe(2);
  expect(screen.getByTestId("route-progress").query()).toBeNull();
});
