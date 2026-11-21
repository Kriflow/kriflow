import type { ReactNode } from "react";

/** A run file, served by the API under its path in the world folder. */
export const fileUrl = (worldId: string, path: string) =>
  `/api/worlds/${worldId}/files/${path.split("/").map(encodeURIComponent).join("/")}`;

/** Markdown as agents write it: headings, paragraphs, lists, fenced code, bold, code spans and
 * web links and images, and tables. Built as elements, never as HTML, so an agent's text cannot
 * script the page. Given the document's place in its world (`base`), an image linked by a path
 * relative to the document loads through the files route. ponytail: no nesting; a markdown
 * library when documents need it. */
export function Markdown({ text, base, code }: { text: string; base?: DocBase; code?: CodeBlock }) {
  return <div className="md">{mdBlocks(text).map((b, i) => block(b, i, base, code))}</div>;
}

/** How a fenced code block shows, given its language and text; a plain `<pre>` by default. */
export type CodeBlock = (lang: string, text: string, key: number) => ReactNode;

const ITEM = /^([-*] |\d+\. )/;

/** A document's blocks: split at blank lines, except inside a fenced code block, which stays
 * whole. A list right under a line of text (no blank line, as chat replies write it) is a block
 * of its own, and so is the text after it. */
export function mdBlocks(text: string): string[] {
  const blocks: string[] = [];
  let cur: string[] = [];
  let fenced = false;
  const close = () => {
    const b = cur.join("\n").trim();
    if (b) blocks.push(b);
    cur = [];
  };
  for (const line of text.split("\n")) {
    const fence = line.trim().startsWith("```");
    if (fence && !fenced) close(); // a fence opens its own block
    const last = [...cur].reverse().find((l) => !/^\s/.test(l)); // an item's indented lines are the item
    if (!fenced && last !== undefined && line.trim() && ITEM.test(line) !== ITEM.test(last) && !/^\s/.test(line)) close();
    if (!fenced && !line.trim()) close();
    else cur.push(line);
    if (fence) fenced = !fenced;
    if (fence && !fenced) close();
  }
  close();
  return blocks;
}

/** Where a document lives: its world and its path inside the world folder. */
export type DocBase = { worldId: string; path: string };

/** The URL an image source loads from, or null to show its alt text: https as written, a path
 * relative to the document resolved against its folder. Refused: other schemes, absolute paths,
 * backslashes, and anything that climbs out of the world folder. */
export function imageSrc(src: string, base?: DocBase): string | null {
  if (/^https:\/\//i.test(src)) return src;
  if (!base || /^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith("/") || src.includes("\\")) return null;
  const parts = base.path.split("/").slice(0, -1);
  for (const seg of src.split("/")) {
    if (seg === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else if (seg && seg !== ".") parts.push(seg);
  }
  return parts.length ? fileUrl(base.worldId, parts.join("/")) : null;
}

function block(b: string, key: number, base?: DocBase, code?: CodeBlock): ReactNode {
  const heading = /^(#{1,6})\s+(.*)$/s.exec(b);
  if (heading) {
    // The page owns h1 and h2; a document's own levels start at h3.
    const Tag = `h${Math.min((heading[1] ?? "#").length + 2, 6)}` as "h3" | "h4" | "h5" | "h6";
    return <Tag key={key}>{inline(heading[2] ?? "", base)}</Tag>;
  }
  if (b.startsWith("```")) {
    const body = b.replace(/^```[^\n]*\n?/, "").replace(/\n?```$/, "");
    return code ? code(/^```(\S*)/.exec(b)?.[1] ?? "", body, key) : <pre key={key}>{body}</pre>;
  }
  const lines = b.split("\n");
  if (lines.length > 1 && lines.every((l) => l.trim().startsWith("|")) && /^[\s|:-]+$/.test(lines[1] ?? "")) {
    const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    return (
      // A wide table scrolls inside itself, never the page.
      <div key={key} className="scroll-x">
        <table>
          <thead>
            <tr>{cells(lines[0] ?? "").map((c, i) => <th key={i}>{inline(c, base)}</th>)}</tr>
          </thead>
          <tbody>
            {lines.slice(2).map((l, r) => (
              <tr key={r}>{cells(l).map((c, i) => <td key={i}>{inline(c, base)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (lines.every((l) => /^[-*] /.test(l))) {
    return <ul key={key}>{lines.map((l, i) => <li key={i}>{inline(l.slice(2), base)}</li>)}</ul>;
  }
  if (lines.every((l) => /^\d+\. /.test(l))) {
    return <ol key={key}>{lines.map((l, i) => <li key={i}>{inline(l.replace(/^\d+\. /, ""), base)}</li>)}</ol>;
  }
  return <p key={key}>{inline(b, base)}</p>;
}

function inline(s: string, base?: DocBase): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`|!\[[^\]]*\]\([^)\s]+\)|\[[^\]]*\]\(https?:\/\/[^)\s]+\))/g).map((part, i) => {
    if (part.startsWith("**")) return <b key={i}>{part.slice(2, -2)}</b>;
    if (part.startsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    const image = /^!\[([^\]]*)\]\(([^)\s]+)\)$/.exec(part);
    if (image) {
      const src = imageSrc(image[2] ?? "", base);
      return src ? <img key={i} src={src} alt={image[1]} /> : image[1];
    }
    const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(part);
    if (link) {
      return (
        <a key={i} href={link[2]} target="_blank" rel="noreferrer">
          {link[1]}
        </a>
      );
    }
    return part;
  });
}
