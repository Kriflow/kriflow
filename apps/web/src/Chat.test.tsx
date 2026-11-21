import { useState } from "react";
import { expect, test, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";

import "./index.css";

import { ChangeFrame, Chat, type ChatTurn, DoneLine, What } from "./Chat";
import { mdBlocks } from "./Markdown";

const at = "2026-11-20T09:12:00+00:00";
const speaker = { name: "Run assistant", mark: "RA" };

function Harness({ turns: start = [], onSend = () => undefined, ...rest }: Partial<Parameters<typeof Chat>[0]> & { turns?: ChatTurn[] }) {
  const [turns, setTurns] = useState(start);
  return (
    <div style={{ height: 600, display: "flex" }}>
      <Chat
        label="Ask this run"
        title="Ask this run"
        context="Reads the run, proposes changes. Nothing changes until you apply."
        speaker={speaker}
        turns={turns}
        placeholder="Ask, or say what to change"
        meta={["Enter sends", "About $0.54 a question, $73.97 left this month"]}
        onSend={(text) => {
          onSend(text);
          setTurns((t) => [...t, { id: `t${t.length}`, mine: true, text, at }]);
        }}
        onEdit={(t) => setTurns((all) => all.filter((x) => x.id !== t.id))}
        {...rest}
      />
    </div>
  );
}

test("an empty chat says what it does and a starter sends itself", async () => {
  await page.viewport(1280, 800);
  const sent = vi.fn();
  const screen = await render(
    <Harness onSend={sent} empty={{ line: "Ask anything about this run, or say what to change.", starters: ["What will publish send?", "Re-run a stage with a note"] }} />,
  );
  await expect.element(screen.getByRole("heading", { name: "Ask this run" })).toBeVisible();
  await expect.element(screen.getByText("Ask anything about this run, or say what to change.")).toBeVisible();
  await screen.getByRole("button", { name: "What will publish send?" }).click();
  expect(sent).toHaveBeenCalledWith("What will publish send?");
  await expect.element(screen.getByText("Ask anything about this run")).not.toBeInTheDocument();
});

test("a reply renders real markdown with its speaker, time and a Read row; yours is a raised block with a time", async () => {
  await page.viewport(1280, 800);
  const reply = "Research stopped at its **done condition**: `at least 3 sources`.\n- 2 of the 3 are from one site.\n- Last week used [8](https://example.com/run).";
  const screen = await render(
    <Harness
      turns={[
        { id: "1", mine: true, text: "Why only three sources?", at },
        { id: "2", text: reply, at, read: [<span key="l" className="ref">research log</span>, <span key="f" className="ref">sources.json</span>] },
      ]}
    />,
  );
  const log = screen.getByRole("log", { name: "Conversation" });
  await expect.element(log).toHaveAttribute("aria-live", "polite");
  await expect.element(log.getByText("done condition")).toHaveProperty("tagName", "B");
  await expect.element(log.getByText("at least 3 sources")).toHaveProperty("tagName", "CODE");
  await expect.element(log.getByText("2 of the 3 are from one site.")).toHaveProperty("tagName", "LI");
  await expect.element(log.getByRole("link", { name: "8" })).toHaveAttribute("href", "https://example.com/run");
  expect(log.element().textContent).not.toContain("**");
  await expect.element(log).toMatchTextContent(/RARun assistant\d\d:\d\d/);
  await expect.element(log).toMatchTextContent(/Read\s*research log\s*sources\.json/);
  const mine = log.getByText("Why only three sources?");
  await expect.element(mine).toHaveClass("msg");
  await expect.element(log).toMatchTextContent(/You\d\d:\d\d/);
  const box = mine.element().getBoundingClientRect();
  const right = log.element().getBoundingClientRect().right;
  expect(right - box.right).toBeLessThan(40); // on the right
});

test("a code block in a reply has its language, Copy, and folds after 12 lines", async () => {
  await page.viewport(1280, 800);
  const yaml = Array.from({ length: 20 }, (_, i) => `key${i}: ${i}`).join("\n");
  const screen = await render(<Harness turns={[{ id: "1", text: `Here:\n\n\`\`\`yaml\n${yaml}\n\`\`\``, at }]} />);
  await expect.element(screen.getByText("yaml", { exact: true })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Copy" })).toBeVisible();
  const pre = screen.container.querySelector(".code pre")!;
  expect(pre.textContent?.split("\n")).toHaveLength(12);
  await screen.getByRole("button", { name: "Show all 20 lines" }).click();
  expect(pre.textContent?.split("\n")).toHaveLength(20);
});

test("Enter sends and Shift+Enter is a new line on a keyboard; the field grows to 6 lines then scrolls", async () => {
  await page.viewport(1280, 800);
  const sent = vi.fn();
  const screen = await render(<Harness onSend={sent} />);
  const field = screen.getByRole("textbox", { name: "Ask, or say what to change" });
  const send = screen.getByRole("button", { name: "Send" });
  await expect.element(send).toBeDisabled();
  await field.click();
  const one = field.element().getBoundingClientRect().height;
  await userEvent.keyboard("first{Shift>}{Enter}{/Shift}second");
  await expect.element(field).toHaveValue("first\nsecond");
  await expect.poll(() => field.element().getBoundingClientRect().height).toBeGreaterThan(one);
  await userEvent.keyboard("{Shift>}{Enter}{Enter}{Enter}{Enter}{Enter}{Enter}{Enter}{Enter}{/Shift}");
  const tall = field.element().getBoundingClientRect().height;
  const line = parseFloat(getComputedStyle(field.element()).lineHeight);
  expect(tall).toBeLessThanOrEqual(6 * line + 12);
  expect(field.element().scrollHeight).toBeGreaterThan(field.element().clientHeight);
  await field.fill("Why only three sources?");
  await userEvent.keyboard("{Enter}");
  expect(sent).toHaveBeenCalledWith("Why only three sources?");
  await expect.element(field).toHaveValue("");
});

test("on a phone return is a new line and the button sends", async () => {
  await page.viewport(390, 844);
  const sent = vi.fn();
  const screen = await render(<Harness onSend={sent} />);
  const field = screen.getByRole("textbox", { name: "Ask, or say what to change" });
  await field.click();
  await userEvent.keyboard("a{Enter}b");
  await expect.element(field).toHaveValue("a\nb");
  expect(sent).not.toHaveBeenCalled();
  await screen.getByRole("button", { name: "Send" }).click();
  expect(sent).toHaveBeenCalledWith("a\nb");
});

test("@ lists the stages and files and inserts a reference, shown as a chip once sent", async () => {
  await page.viewport(1280, 800);
  const sent = vi.fn();
  const screen = await render(<Harness onSend={sent} refs={["research", "draft", "publish", "draft.md"]} />);
  const field = screen.getByRole("textbox", { name: "Ask, or say what to change" });
  await field.click();
  await userEvent.keyboard("What will ");
  await screen.getByRole("button", { name: "Reference a stage or file" }).click();
  const list = screen.getByRole("listbox", { name: "Stages and files" });
  await expect.element(list).toBeVisible();
  await userEvent.keyboard("pub");
  await expect.element(list.getByRole("option")).toHaveTextContent("publish");
  await userEvent.keyboard("{Enter}");
  await expect.element(field).toHaveValue("What will @publish ");
  await expect.element(list).not.toBeInTheDocument();
  await userEvent.keyboard("send? @dr");
  await screen.getByRole("option", { name: "draft.md" }).click();
  await expect.element(field).toHaveValue("What will @publish send? @draft.md ");
  await userEvent.keyboard("{Enter}");
  expect(sent).toHaveBeenCalledWith("What will @publish send? @draft.md");
  await expect.element(screen.getByText("@publish")).toHaveClass("ref-chip");
});

test("while it works the speaker shows a live dot and seconds, and Send waits", async () => {
  await page.viewport(1280, 800);
  const screen = await render(<Harness turns={[{ id: "1", mine: true, text: "What will publish send?", at }]} waiting={Date.now() - 4000} />);
  const status = screen.getByRole("status");
  await expect.element(status).toMatchTextContent(/Run assistant/);
  await expect.element(status.getByTestId("waited")).toMatchTextContent(/^[4-6] s$/);
  expect(status.element().querySelector(".live-dot")).not.toBeNull();
  await screen.getByRole("textbox").fill("And the subject?");
  await expect.element(screen.getByRole("button", { name: "Send" })).toBeDisabled();
});

test("reduced motion holds the live dot still", async () => {
  await page.viewport(1280, 800);
  const screen = await render(<Harness waiting={Date.now()} />);
  const dot = screen.container.querySelector(".live-dot")!;
  // The suite's browser may or may not prefer reduced motion; the rule is checked either way.
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  expect(getComputedStyle(dot).animationName).toBe(reduce ? "none" : "pulse");
  const sheet = [...document.styleSheets].flatMap((s) => [...s.cssRules]).map((r) => r.cssText).join("\n");
  expect(sheet).toMatch(/prefers-reduced-motion: reduce\)\s*\{[^}]*\.live-dot\s*\{\s*animation: (none|auto ease 0s 1 normal none running none)/);
});

test("a failed message stays in the log with Retry and Edit", async () => {
  await page.viewport(1280, 800);
  const retry = vi.fn();
  const screen = await render(
    <Harness turns={[{ id: "1", mine: true, text: "Cut the hashtags to five", at, failed: "the assistant timed out after 60 s" }]} onRetry={retry} />,
  );
  await expect.element(screen.getByRole("alert")).toHaveTextContent("Not sent: the assistant timed out after 60 s.RetryEdit");
  await screen.getByRole("button", { name: "Retry" }).click();
  expect(retry).toHaveBeenCalledWith(expect.objectContaining({ id: "1", text: "Cut the hashtags to five" }));
  await screen.getByRole("button", { name: "Edit" }).click();
  await expect.element(screen.getByRole("textbox")).toHaveValue("Cut the hashtags to five");
  await expect.element(screen.getByRole("alert")).not.toBeInTheDocument();
});

test("a question offers numbered choices and Something else; a number key picks on a keyboard", async () => {
  await page.viewport(1280, 800);
  const chose = vi.fn();
  const question = { text: "Where should the approved summary go?", choices: [{ label: "Email it to me", hint: "email connector" }, { label: "Keep it in the library only" }] };
  const screen = await render(<Harness turns={[{ id: "q", text: "Drafted 5 stages.", at, question }]} onChoose={(_t, label) => chose(label)} />);
  const group = screen.getByRole("group", { name: "Where should the approved summary go?" });
  await expect.element(group.getByRole("button", { name: "1 Email it to me email connector" })).toBeVisible();
  await group.getByRole("button", { name: "Something else, I'll type it" }).click();
  await expect.element(screen.getByRole("textbox")).toHaveFocus();
  (document.activeElement as HTMLElement).blur();
  await userEvent.keyboard("2");
  expect(chose).toHaveBeenCalledWith("Keep it in the library only");
});

test("an answered question marks the chosen one and dims the rest", async () => {
  await page.viewport(1280, 800);
  const question = { text: "Where?", choices: [{ label: "Email it to me" }, { label: "Keep it" }], chosen: "Email it to me" };
  const screen = await render(<Harness turns={[{ id: "q", text: "Ok.", at, question }]} onChoose={() => undefined} />);
  await expect.element(screen.getByRole("button", { name: /Email it to me chosen/ })).toHaveAttribute("aria-pressed", "true");
  await expect.element(screen.getByRole("button", { name: /Keep it/ })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: /Something else/ }).elements()).toHaveLength(0);
});

test("a change card shows its status, the -/+ diff, its checks and cost; out of date and would not pass read differently", async () => {
  await page.viewport(1280, 800);
  const screen = await render(
    <div>
      <ChangeFrame
        title="Re-run from research"
        status="Proposed"
        diff={[
          { sign: "-", key: "research", text: "done" },
          { sign: "+", key: "research", text: "pending" },
        ]}
        what={[<What key="ok" tone="ok">Check passed. Runs in place.</What>, <What key="r">Resets research and pick topic.</What>]}
        actions={<button className="primary">Apply</button>}
        cost="$1.20 of $18.40 left"
      />
      <ChangeFrame title="Skip publish" status="Out of date" what={[<What key="s" tone="warn">The run moved on since this was proposed.</What>]} actions={<button>Ask again</button>} testId="stale" />
      <ChangeFrame title="Edit publish" status="Would not pass" testId="bad" />
      <DoneLine>
        <b>Re-run from research</b> applied by you, 09:13
      </DoneLine>
      <DoneLine dismissed>Not now: Skip retro</DoneLine>
    </div>,
  );
  const card = screen.getByRole("region", { name: "Re-run from research" });
  await expect.element(card).toMatchTextContent(/Re-run from researchProposed/);
  await expect.element(card.getByRole("deletion")).toHaveTextContent("research done");
  await expect.element(card.getByRole("insertion")).toHaveTextContent("research pending");
  await expect.element(card).toMatchTextContent(/Check passed\. Runs in place\.Resets research and pick topic\..*Apply\$1\.20 of \$18\.40 left/);
  expect(getComputedStyle(card.element().querySelector(".add")!, "::before").content).toBe('"+"');
  await expect.element(screen.getByTestId("stale")).toHaveClass("stale");
  await expect.element(screen.getByTestId("stale")).toMatchTextContent(/Out of date/);
  await expect.element(screen.getByTestId("bad")).toHaveClass("bad");
  await expect.element(screen.getByRole("status").first()).toHaveTextContent("Re-run from research applied by you, 09:13");
  await expect.element(screen.getByRole("status").nth(1)).toHaveTextContent("Not now: Skip retro");
});

test("on a 390 px phone the chat fits with no horizontal scroll, long words and code included", async () => {
  await page.viewport(390, 844);
  const long = "x".repeat(300);
  const screen = await render(
    <Harness
      turns={[
        { id: "1", mine: true, text: long, at },
        { id: "2", text: `A long id ${long} and\n\n\`\`\`\n${long}\n\`\`\``, at },
      ]}
    />,
  );
  await expect.element(screen.getByRole("log")).toBeVisible();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390);
  const send = screen.getByRole("button", { name: "Send" }).element().getBoundingClientRect();
  expect(send.right).toBeLessThanOrEqual(390);
  expect(send.height).toBeGreaterThanOrEqual(36);
});

test("a list right under a line of text is its own block, as chat replies write it", () => {
  expect(mdBlocks("Two things:\n- one\n- two\nThat is all.")).toEqual(["Two things:", "- one\n- two", "That is all."]);
  expect(mdBlocks("- a\n  still a\n- b")).toEqual(["- a\n  still a\n- b"]);
  expect(mdBlocks("```\nx:\n- y\n```")).toEqual(["```\nx:\n- y\n```"]);
});
