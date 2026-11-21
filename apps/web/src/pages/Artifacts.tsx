import { Link } from "@tanstack/react-router";
import { Fragment, type ReactNode } from "react";

import type { ArtifactFile } from "../api/events";
import { Icon } from "../Icon";
import { fileUrl, Markdown } from "../Markdown";
import { Media } from "../Media";
import { standalone } from "../pwa";
import { cap, the } from "../skin";

// The world file URL and the markdown renderer live in a skin-neutral leaf the office may use too.
export { type DocBase, fileUrl, imageSrc, Markdown, mdBlocks } from "../Markdown";

/** The raw file behind an artifact. A binary is listed by its sidecar (`short.mp4.md`), so the
 * bytes live one `.md` short of its path. */
export const rawUrl = (worldId: string, a: Pick<ArtifactFile, "path" | "name">) =>
  fileUrl(worldId, a.path.endsWith(`/${a.name}.md`) ? a.path.slice(0, -3) : a.path);

/** A document the reader shows: markdown or plain text. */
export const DOCUMENT = /\.(md|txt)$/i;

/** Open a file: a document in the in-app reader, anything else as the raw file in a new tab. */
export function FileLink({ a, worldId, className, children }: { a: Pick<ArtifactFile, "path" | "name">; worldId: string; className?: string; children: ReactNode }) {
  if (DOCUMENT.test(a.name)) {
    return (
      <Link to="/worlds/$worldId/read" params={{ worldId }} search={{ path: a.path }} className={className}>
        {children}
      </Link>
    );
  }
  return (
    <a href={rawUrl(worldId, a)} target="_blank" rel="noreferrer" className={className}>
      {children}
    </a>
  );
}

export const VIDEO = /\.(mp4|webm|mov|m4v)$/i;
export const IMAGE = /\.(png|jpe?g|gif|webp|svg|avif)$/i;
export const AUDIO = /\.(wav|mp3|m4a|ogg|flac|aac)$/i;

/** `runs/<run>/<stage>/notes/trends.md#p3` as "trends.md, paragraph 3", linking to the note. */
function Source({ ref, worldId }: { ref: string; worldId: string }) {
  const [path = "", p] = ref.split("#p");
  return (
    // In a browser a new tab keeps a half-made pick on the gate; the installed app has no tabs,
    // so there it opens in place, as a route, not a second window loading the whole app.
    <Link to="/worlds/$worldId/read" params={{ worldId }} search={{ path }} target={standalone() ? undefined : "_blank"} rel="noreferrer">
      {path.split("/").pop()}, paragraph {p}
    </Link>
  );
}

/** The first paragraph of the body that is not its heading, for a card with no hook. */
export function summary(body: string): string {
  const para = body
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .find((b) => b && !b.startsWith("#"));
  return (para ?? "").replace(/\s+/g, " ").slice(0, 200);
}

/** Choosable cards for a pick gate as a ranked radio list (story 32): rank, title and hook or
 * summary, with the judge's reason and the note paragraphs it came from folded away (story 33). */
/** A pick gate that takes several cards: the paths picked in order, the rejected ones with the
 * reason typed so far, and how to change either. */
export type MultiPick = {
  picks: number;
  picked: string[];
  rejected: Record<string, string>;
  toggle: (path: string) => void;
  /** A reason ("" for none) rejects the card; null takes the reject back. */
  reject: (path: string, reason: string | null) => void;
};

export function ArtifactCards({ cards, worldId, multi }: { cards: ArtifactFile[]; worldId: string; multi?: MultiPick }) {
  return (
    <fieldset className="pick-cards" data-testid="pick-cards">
      <legend>
        {multi ? `Your picks, up to ${multi.picks}` : "Your pick"}
        {cards.some((c) => c.fields.rank != null) ? `, ranked by ${the("judge")}` : ""}
      </legend>
      {cards.map((a) => {
        const sources = (a.fields.sources as string[] | undefined) ?? [];
        const hook = typeof a.fields.hook === "string" ? a.fields.hook : summary(a.body);
        const why = [a.fields.reason, a.fields.why].find((w) => typeof w === "string" && w) as string | undefined;
        const order = multi ? multi.picked.indexOf(a.path) : -1;
        const out = multi ? a.path in multi.rejected : false;
        const card = (
          <label key={a.path} className="pick-card" data-testid="pick-card" data-rejected={out || undefined}>
            {multi ? (
              <input
                type="checkbox"
                name="artifacts"
                value={a.path}
                checked={order >= 0}
                disabled={out || (order < 0 && multi.picked.length >= multi.picks)}
                onChange={() => multi.toggle(a.path)}
              />
            ) : (
              <input type="radio" name="artifact" value={a.path} required />
            )}
            <span className="rank" title={a.fields.rank != null ? `${cap(the("judge"))}'s #${String(a.fields.rank)} of ${cards.length}` : undefined}>
              {a.fields.rank != null ? `#${String(a.fields.rank)}` : a.type}
            </span>
            <b>{a.title}</b>
            {order >= 0 ? (
              <span className="pick-order" aria-label={`pick ${order + 1}`}>
                {order + 1}
              </span>
            ) : (
              <Icon name="check" size={20} className="pick-check" />
            )}
            {hook && <span className="hook">{hook}</span>}
            <details>
              <summary>Why</summary>
              {why && <p>Why: {why}</p>}
              <p>
                {sources.length ? (
                  <>
                    From{" "}
                    {sources.map((s, i) => (
                      <span key={s}>
                        {i > 0 && "; "}
                        <Source ref={s} worldId={worldId} />
                      </span>
                    ))}
                  </>
                ) : (
                  "No source paragraph cited."
                )}
              </p>
            </details>
          </label>
        );
        if (!multi) return card;
        return (
          <div key={a.path} className="pick-slot">
            {card}
            {out ? (
              <div className="pick-reject row">
                <input
                  aria-label={`Why reject ${a.title} (optional)`}
                  placeholder="Why (optional)"
                  value={multi.rejected[a.path]}
                  onChange={(e) => multi.reject(a.path, e.target.value)}
                />
                <button type="button" onClick={() => multi.reject(a.path, null)}>
                  Undo reject
                </button>
              </div>
            ) : (
              <button type="button" className="pick-reject-button" aria-label={`Reject ${a.title}`} onClick={() => multi.reject(a.path, "")}>
                Reject
              </button>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}

/** Whether an artifact is a video. */
export const isVideo = (a: Pick<ArtifactFile, "type" | "name">) => a.type === "video" || VIDEO.test(a.name);

/** The artifacts under review, in place (story 54): a player for a video, first, then the image
 * itself (a tap opens it full screen), rendered markdown for a document, a link for anything
 * else. */
export function ArtifactView({ artifacts, worldId }: { artifacts: ArtifactFile[]; worldId: string }) {
  const shown = [...artifacts].sort((a, b) => Number(isVideo(b)) - Number(isVideo(a)));
  return (
    <section className="artifact-view" aria-label="Under review" data-testid="under-review">
      {shown.map((a) => {
        const url = rawUrl(worldId, a);
        return (
          <figure key={a.path}>
            <figcaption>
              <b>{a.title}</b> <span className="faint">{a.type}</span>
            </figcaption>
            {a.fields.deleted ? (
              <p className="faint">{a.name} was deleted by retention; its record stays.</p>
            ) : isVideo(a) ? (
              <Media video src={url} alt={a.title} testId="player" />
            ) : IMAGE.test(a.name) ? (
              <Media src={url} alt={a.title} />
            ) : a.name.endsWith(".md") ? (
              <>
                <TypedText body={a.body} />
                <Markdown text={a.body} base={{ worldId, path: a.path }} />
              </>
            ) : (
              <FileLink a={a} worldId={worldId}>
                Open {a.name}
              </FileLink>
            )}
          </figure>
        );
      })}
    </section>
  );
}

/** A typed artifact's top-level text from its fenced JSON block, as the voice a script's judge
 * chose, so it shows on the gate before the long value below. */
export function TypedText({ body }: { body: string }) {
  const block = /```json[ \t]*\n([\s\S]*?)\n```/.exec(body)?.[1];
  let value: unknown = null;
  try {
    value = block ? JSON.parse(block) : null;
  } catch {
    return null;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const text = Object.entries(value).filter((e): e is [string, string] => typeof e[1] === "string");
  if (!text.length) return null;
  return (
    <dl className="typed-text" data-testid="typed-text">
      {text.map(([k, v]) => (
        <Fragment key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

