/** The one chat (Chat v1): a header, a log that scrolls inside itself and a pinned composer,
 * shared by Ask this run, Ask before you answer at a gate, the workflow editor's planner and the
 * Office's Talk. Surfaces differ only in their words, starters, speaker and what a card changes;
 * the Office restyles the same parts in its own tokens. A skin-neutral leaf: it reads no API. */
import { type KeyboardEvent, type ReactNode, type RefObject, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import type { Exchange } from "./api/assistant";
import { Icon, type IconName } from "./Icon";
import { Markdown } from "./Markdown";
import { usePhone } from "./usePhone";

export type ChatQuestion = { text: string; choices: { label: string; hint?: string }[]; chosen?: string | null };

/** One turn in the log: yours (`mine`) or the speaker's. A reply's `read` chips name what it read,
 * `question` offers choices to tap, and `after` holds its change cards. A message of yours that
 * did not go keeps `failed`, the reason, with Retry and Edit. */
export type ChatTurn = {
  id: string;
  mine?: boolean;
  text: string;
  at?: string | null;
  read?: ReactNode[];
  failed?: string | null;
  question?: ChatQuestion | null;
  after?: ReactNode;
};

export type Speaker = { name: string; role?: string; mark?: ReactNode };

/** "09:12", in the reader's clock. */
export const hhmm = (at: string) => new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

/** "RA" for Run assistant, "R" for Richard. */
const initials = (name: string) =>
  name
    .split(/\s+/)
    .map((w) => w[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();

/** The glyph a Read chip leads with: a file, a log, or a stage. */
export const refIcon = (label: string): IconName => (/\.[a-z0-9]{1,5}\b/i.test(label) ? "assets" : /\blog\b/i.test(label) ? "filter" : "stage");

export function Chat({
  label,
  title,
  context,
  lead,
  onClose,
  speaker,
  turns,
  waiting = null,
  empty,
  chips,
  placeholder,
  meta,
  refs = [],
  onSend,
  onRetry,
  onEdit,
  onChoose,
  className = "",
  testId,
  fit = false,
}: {
  /** The region's accessible name. */
  label: string;
  title?: string;
  context?: string;
  /** Before the title: the Office's face. */
  lead?: ReactNode;
  onClose?: () => void;
  speaker: Speaker;
  turns: ChatTurn[];
  /** When the unanswered message went (ms), while the speaker works on it. */
  waiting?: number | null;
  /** The empty log: one line on what it does, and prompts to start from. */
  empty?: { line?: string; starters: string[] };
  /** Prompts as a row above the field, kept while the log fills (a gate's). */
  chips?: string[];
  placeholder: string;
  meta?: ReactNode[];
  /** The stages and files `@` offers, from what the page already has. */
  refs?: string[];
  onSend: (text: string) => void;
  onRetry?: (turn: ChatTurn) => void;
  /** The failed turn leaves the log; its text goes back into the composer. */
  onEdit?: (turn: ChatTurn) => void;
  onChoose?: (turn: ChatTurn, label: string) => void;
  className?: string;
  testId?: string;
  /** Size the chat to end at the window's foot (above a pinned bar or the keyboard), so its
   * composer is in view from the first paint and the log scrolls inside. */
  fit?: boolean;
}) {
  const phone = usePhone();
  const root = useRef<HTMLElement>(null);
  useFit(root, fit);
  const log = useRef<HTMLOListElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [active, setActive] = useState(0);
  const listId = useId();
  const fieldId = useId();

  // The newest turn shows: the log keeps to its foot as it grows or is resized, unless you
  // scrolled up to read.
  const atFoot = useRef(true);
  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
    atFoot.current = true;
  }, [turns.length, waiting]);
  useEffect(() => {
    const el = log.current;
    if (!el) return;
    const onScroll = () => (atFoot.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40);
    const keep = new ResizeObserver(() => atFoot.current && (el.scrollTop = el.scrollHeight));
    keep.observe(el);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      keep.disconnect();
      el.removeEventListener("scroll", onScroll);
    };
  }, []);

  // The field grows with its text up to 6 lines (CSS caps it), then scrolls.
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  // The open question, the newest reply's, for number keys on a keyboard.
  const last = turns.at(-1);
  const open = last && !last.mine && last.question && !last.question.chosen ? last : null;
  useEffect(() => {
    if (!open || phone || !onChoose) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      const t = e.target;
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || e.metaKey || e.ctrlKey || e.altKey) return;
      if (document.querySelector("dialog[open]:not(.chat-sheet)")) return;
      const choice = open.question?.choices[Number(e.key) - 1];
      if (choice) {
        e.preventDefault();
        onChoose(open, choice.label);
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [open, phone, onChoose]);

  const options = mention ? refs.filter((r) => r.toLowerCase().includes(mention.query.toLowerCase())).slice(0, 8) : [];
  const listing = !!mention && options.length > 0;

  function send() {
    const said = text.trim();
    if (!said || waiting !== null) return;
    onSend(said);
    setText("");
    setMention(null);
  }

  /** What `@` is being typed right before the caret, if any. */
  function track(value: string, caret: number) {
    const before = value.slice(0, caret);
    const at = /(^|\s)@([^\s@]*)$/.exec(before);
    setMention(at && refs.length ? { start: caret - (at[2] ?? "").length - 1, query: at[2] ?? "" } : null);
    setActive(0);
  }

  function insert(ref: string) {
    const el = field.current;
    if (!mention || !el) return;
    const caret = el.selectionStart ?? text.length;
    const next = `${text.slice(0, mention.start)}@${ref} ${text.slice(caret)}`;
    const at = mention.start + ref.length + 2;
    setText(next);
    setMention(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(at, at);
    });
  }

  function openRefs() {
    const el = field.current;
    if (!el) return;
    const caret = el.selectionStart ?? text.length;
    const pad = caret > 0 && !/\s$/.test(text.slice(0, caret)) ? " " : "";
    const next = `${text.slice(0, caret)}${pad}@${text.slice(caret)}`;
    setText(next);
    setMention({ start: caret + pad.length, query: "" });
    setActive(0);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(caret + pad.length + 1, caret + pad.length + 1);
    });
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (listing) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setActive((n) => (n + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        insert(options[active] ?? options[0]!);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation(); // the list closes, not the sheet
        setMention(null);
        return;
      }
    }
    // Enter sends on a keyboard; a phone's return is a new line and the button sends.
    if (e.key === "Enter" && !e.shiftKey && !phone && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  }

  const showEmpty = turns.length === 0 && waiting === null && empty;
  return (
    <section ref={root} className={`chat ${className}`.trim()} aria-label={label} data-testid={testId}>
      {title && (
        <header className="chat-head">
          {lead}
          <div className="grow">
            <h2>{title}</h2>
            {context && <p className="ctx">{context}</p>}
          </div>
          {onClose && (
            <button type="button" className="icon chat-close" aria-label="Close" onClick={onClose}>
              <Icon name="x" />
            </button>
          )}
        </header>
      )}
      <ol ref={log} className="chat-log" role="log" aria-label="Conversation" aria-live="polite" tabIndex={0}>
        {showEmpty && (
          <li className="chat-empty">
            {empty.line && <p>{empty.line}</p>}
            {empty.starters.length > 0 && (
              <div className="chat-starters">
                {empty.starters.map((s) => (
                  <button key={s} type="button" className="chat-starter" onClick={() => onSend(s)}>
                    {s}
                    <Icon name="next" />
                  </button>
                ))}
              </div>
            )}
          </li>
        )}
        {turns.map((t) =>
          t.mine ? (
            <li key={t.id} className={t.failed ? "turn you failed" : "turn you"}>
              <p className="who">
                <b>You</b>
                {t.at && <time dateTime={t.at}>{hhmm(t.at)}</time>}
              </p>
              <p className="msg">{withRefs(t.text, refs)}</p>
              {t.failed && (
                <p className="not-sent" role="alert">
                  <Icon name="warning" />
                  Not sent: {t.failed.replace(/\.$/, "")}.
                  {onRetry && (
                    <button type="button" onClick={() => onRetry(t)}>
                      <Icon name="retry" />
                      Retry
                    </button>
                  )}
                  {onEdit && (
                    <button
                      type="button"
                      onClick={() => {
                        onEdit(t);
                        setText(t.text);
                        field.current?.focus();
                      }}
                    >
                      Edit
                    </button>
                  )}
                </p>
              )}
            </li>
          ) : (
            <li key={t.id} className="turn">
              <Who speaker={speaker} at={t.at} />
              {t.text && <Markdown text={t.text} code={code} />}
              {t.read && t.read.length > 0 && (
                <p className="refs">
                  <span className="lead">Read</span>
                  {t.read}
                </p>
              )}
              {t.after}
              {t.question && (
                <Choices
                  question={t.question}
                  onChoose={onChoose && ((choice) => onChoose(t, choice))}
                  onOther={() => field.current?.focus()}
                />
              )}
            </li>
          ),
        )}
        {waiting !== null && (
          <li className="turn" role="status">
            <Who speaker={speaker} since={waiting} />
            <span className="visually-hidden">{speaker.name} is working on it</span>
          </li>
        )}
      </ol>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        {chips && chips.length > 0 && (
          <div className="chiprow">
            {chips.map((c) => (
              <button key={c} type="button" className="chipbtn" disabled={waiting !== null} onClick={() => onSend(c)}>
                {c}
              </button>
            ))}
          </div>
        )}
        {listing && (
          <ul className="ref-list" id={listId} role="listbox" aria-label="Stages and files">
            {options.map((r, i) => (
              <li
                key={r}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault(); // the field keeps focus
                  insert(r);
                }}
              >
                <Icon name={refIcon(r)} />
                {r}
              </li>
            ))}
          </ul>
        )}
        <div className="field">
          {refs.length > 0 && (
            <button type="button" className="icon add" aria-label="Reference a stage or file" onClick={openRefs}>
              <Icon name="at" />
            </button>
          )}
          <label htmlFor={fieldId} className="visually-hidden">
            {placeholder}
          </label>
          <textarea
            ref={field}
            id={fieldId}
            rows={1}
            value={text}
            placeholder={placeholder}
            enterKeyHint={phone ? "enter" : "send"}
            aria-controls={listing ? listId : undefined}
            aria-autocomplete={refs.length ? "list" : undefined}
            aria-activedescendant={listing ? `${listId}-${active}` : undefined}
            onChange={(e) => {
              setText(e.target.value);
              track(e.target.value, e.target.selectionStart ?? e.target.value.length);
            }}
            onKeyDown={onKeyDown}
            onBlur={() => setMention(null)}
          />
          {/* The API cannot cancel an answer yet, so Send waits rather than turning into Stop. */}
          <button type="submit" className="icon send" aria-label="Send" disabled={!text.trim() || waiting !== null}>
            <Icon name="up" />
          </button>
        </div>
        {meta && meta.length > 0 && (
          <p className="meta">
            {meta.map((m, i) => (
              <span key={i}>{m}</span>
            ))}
          </p>
        )}
      </form>
    </section>
  );
}

/** Keep `el` ending at the foot of what is visible: the visual viewport (so a phone keyboard
 * pushes the composer up), or the top of a bar pinned over the page (the tab bar, a save bar). */
function useFit(el: RefObject<HTMLElement | null>, on: boolean) {
  useLayoutEffect(() => {
    const node = el.current;
    if (!on || !node) return;
    const vv = globalThis.visualViewport;
    const fit = () => {
      const pinned = [...document.querySelectorAll<HTMLElement>(".action-bar, .tabbar")].filter((b) => getComputedStyle(b).position === "fixed" && b.offsetHeight > 0);
      // offsetTop, not the box: a bar sliding in is still below its place.
      const foot = Math.min(vv ? vv.height + vv.offsetTop : innerHeight, ...pinned.map((b) => b.offsetTop));
      const top = node.getBoundingClientRect().top + scrollY;
      node.style.height = `${Math.round(Math.max(280, Math.min(foot - 32, foot - top - 12)))}px`;
    };
    fit();
    addEventListener("resize", fit);
    vv?.addEventListener("resize", fit);
    // The page's own size moves when a pinned bar comes or goes (main pads for it).
    const watch = new ResizeObserver(fit);
    watch.observe(document.body);
    return () => {
      watch.disconnect();
      removeEventListener("resize", fit);
      vv?.removeEventListener("resize", fit);
      node.style.height = "";
    };
  }, [el, on]);
}

/** The speaker line: mark, name, role, then the time, or a live dot and seconds while it works. */
function Who({ speaker, at, since }: { speaker: Speaker; at?: string | null; since?: number }) {
  return (
    <p className="who">
      <span className="mark" aria-hidden="true">
        {speaker.mark ?? initials(speaker.name)}
      </span>
      <b>{speaker.name}</b>
      {speaker.role && <span className="role">{speaker.role}</span>}
      {since !== undefined ? <Waited since={since} /> : at && <time dateTime={at}>{hhmm(at)}</time>}
    </p>
  );
}

function Waited({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <>
      <span className="live-dot" aria-hidden="true" />
      <time data-testid="waited">{Math.max(0, Math.round((now - since) / 1000))} s</time>
    </>
  );
}

/** A question as numbered choices to tap, and Something else to type instead. Once answered the
 * chosen one stays marked and the rest dim. */
function Choices({ question, onChoose, onOther }: { question: ChatQuestion; onChoose?: (label: string) => void; onOther: () => void }) {
  const done = !!question.chosen;
  return (
    <div className="ask-choices" role="group" aria-label={question.text}>
      <p className="ask-q">{question.text}</p>
      <div className="choices">
        {question.choices.map((c, i) => (
          <button
            key={c.label}
            type="button"
            className="choice"
            aria-pressed={done ? question.chosen === c.label : undefined}
            disabled={done || !onChoose}
            onClick={() => onChoose?.(c.label)}
          >
            <kbd>{i + 1}</kbd>
            {c.label}
            {done && question.chosen === c.label ? <small className="chosen">chosen</small> : c.hint && <small>{c.hint}</small>}
          </button>
        ))}
        {!done && (
          <button type="button" className="choice other" onClick={onOther}>
            Something else, I'll type it
          </button>
        )}
      </div>
    </div>
  );
}

/** Your message with each `@reference` it names shown as a chip. */
function withRefs(text: string, refs: string[]): ReactNode {
  if (!refs.length) return text;
  const names = [...refs].sort((a, b) => b.length - a.length).map((r) => r.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return text.split(new RegExp(`(@(?:${names.join("|")}))`, "g")).map((part, i) =>
    i % 2 ? (
      <span key={i} className="ref-chip">
        {part}
      </span>
    ) : (
      part
    ),
  );
}

/** Code or YAML in a reply: mono, with its language and Copy, folded after 12 lines. */
const code = (lang: string, body: string, key: number) => <Code key={key} lang={lang} body={body} />;

const FOLD = 12;

function Code({ lang, body }: { lang: string; body: string }) {
  const rows = body.split("\n");
  const [all, setAll] = useState(rows.length <= FOLD);
  const [copied, setCopied] = useState(false);
  return (
    <div className="code">
      <p className="code-head">
        <span>{lang || "code"}</span>
        <button
          type="button"
          onClick={() =>
            void navigator.clipboard
              ?.writeText(body)
              .then(() => setCopied(true))
              .catch(() => undefined)
          }
        >
          <Icon name={copied ? "check" : "copy"} />
          {copied ? "Copied" : "Copy"}
        </button>
      </p>
      <pre>{all ? body : rows.slice(0, FOLD).join("\n")}</pre>
      {!all && (
        <button type="button" className="code-more" onClick={() => setAll(true)}>
          Show all {rows.length} lines
        </button>
      )}
    </div>
  );
}

export type CardStatus = "Proposed" | "Out of date" | "Would not pass" | "Applied" | "Not now";
export type DiffRow = { sign: "-" | "+" | " "; key?: string; text: string };

const TONE: Record<CardStatus, string> = { Proposed: "", "Out of date": "stale", "Would not pass": "bad", Applied: "", "Not now": "" };

/** A change card's frame, as every surface shows a Run Change (Chat v1): the title and its
 * status, the -/+ diff, the check and what it resets (`what`), then the actions with the cost
 * against the money left. Each card's own logic fills it; the Office restyles it. */
export function ChangeFrame({
  title,
  status,
  diff = [],
  what = [],
  actions,
  cost,
  children,
  testId = "change-card",
}: {
  title: string;
  status: CardStatus;
  diff?: DiffRow[];
  what?: ReactNode[];
  actions?: ReactNode;
  cost?: string | null;
  children?: ReactNode;
  testId?: string;
}) {
  const tone = TONE[status];
  const keyWidth = Math.max(0, ...diff.map((r) => r.key?.length ?? 0));
  return (
    <section className={`change ${tone}`.trim()} aria-label={title} data-testid={testId}>
      <header className="change-head">
        <h3>{title}</h3>
        <span className={`pill ${tone === "stale" ? "warning" : tone === "bad" ? "negative" : ""}`.trim()}>{status}</span>
      </header>
      {(diff.length > 0 || what.length > 0 || children) && (
        <div className="change-body">
          {diff.length > 0 && (
            <ul className="diff" aria-label="The change">
              {diff.map((r, i) => {
                const row = (
                  <>
                    {r.key !== undefined && <span className="k">{r.key.padEnd(keyWidth + 2)}</span>}
                    {r.text}
                  </>
                );
                return (
                  <li key={i} className={r.sign === "-" ? "del" : r.sign === "+" ? "add" : "ctx"}>
                    {r.sign === "-" ? <del>{row}</del> : r.sign === "+" ? <ins>{row}</ins> : row}
                  </li>
                );
              })}
            </ul>
          )}
          {what.length > 0 && <div className="what">{what}</div>}
          {children}
        </div>
      )}
      {actions && (
        <div className="change-foot">
          {actions}
          <span className="sp" />
          {cost && <span className="cost">{cost}</span>}
        </div>
      )}
    </section>
  );
}

/** One line of the card's checks: passed (a tick), a warning, or a refusal. */
export function What({ tone = "", children, role }: { tone?: "ok" | "warn" | "bad" | ""; children: ReactNode; role?: "status" | "alert" }) {
  return (
    <p className={tone} role={role}>
      {tone === "ok" && <Icon name="check" />}
      {tone === "warn" && <Icon name="warning" />}
      {children}
    </p>
  );
}

/** An applied or dismissed card, shrunk to one line so the log stays readable. */
export function DoneLine({ dismissed, children, action }: { dismissed?: boolean; children: ReactNode; action?: ReactNode }) {
  return (
    <div className={dismissed ? "line-done dismissed" : "line-done"} role="status">
      <Icon name={dismissed ? "x" : "check"} />
      <span>{children}</span>
      {action}
    </div>
  );
}

/** The phone keyboard's height, as `--kb` on the page, so a sheet docks its composer above it
 * (iOS does not resize the layout viewport for the keyboard; visualViewport says how much it
 * covers). */
export function useKeyboardInset(on: boolean) {
  useEffect(() => {
    const vv = globalThis.visualViewport;
    if (!on || !vv) return;
    const root = document.documentElement;
    const set = () => root.style.setProperty("--kb", `${Math.max(0, Math.round(innerHeight - vv.height - vv.offsetTop))}px`);
    set();
    vv.addEventListener("resize", set);
    vv.addEventListener("scroll", set);
    return () => {
      vv.removeEventListener("resize", set);
      vv.removeEventListener("scroll", set);
      root.style.removeProperty("--kb");
    };
  }, [on]);
}

/** A gate's exchanges as chat turns, shared with the Office's Talk: your question, then the
 * reply with what it read, what it filled and its cards. */
export function exchangeTurns(
  exchanges: Exchange[],
  filled: (said: NonNullable<Exchange["said"]>) => string | null,
  cards: (said: NonNullable<Exchange["said"]>) => ReactNode,
): ChatTurn[] {
  return exchanges.flatMap((e) => {
    const mine: ChatTurn = { id: e.id, mine: true, text: e.asked, at: e.at, failed: e.failed };
    if (!e.said) return [mine];
    const note = filled(e.said);
    return [
      mine,
      {
        id: e.said.id,
        text: e.said.text,
        at: e.said.at,
        read: (e.said.sources ?? []).map((s, n) => (
          <span key={n} className="ref">
            <Icon name={refIcon(s.label)} />
            {s.label}
          </span>
        )),
        after: (
          <>
            {note && <p className="filled-note">{note}</p>}
            {cards(e.said)}
          </>
        ),
      },
    ];
  });
}
