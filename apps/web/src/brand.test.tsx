import { expect, test } from "vitest";

import index from "../index.html?raw";
import manifestText from "../public/manifest.webmanifest?raw";
import sw from "../sw.js?raw";
import { APP } from "./skin";

const manifest = JSON.parse(manifestText) as { name: string; short_name: string; icons: { src: string }[] };
// What public/ holds, served at the root.
const served = new Set(Object.keys(import.meta.glob("../public/*")).map((f) => f.replace("../public", "")));

test("the manifest, page and service worker name the app as the skin does", () => {
  expect([manifest.name, manifest.short_name]).toEqual([APP, APP]);
  expect(index).toContain(`<title>${APP}</title>`);
  expect(index).toContain(`name="apple-mobile-web-app-title" content="${APP}"`);
  expect(sw).toContain(`data.title || "${APP}"`);
});

test("every icon the manifest, page and service worker point at exists", () => {
  const linked = [...index.matchAll(/<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"/g)].map((m) => m[1] ?? "");
  const pushed = [...sw.matchAll(/(?:icon|badge): "([^"]+)"/g)].map((m) => m[1] ?? "");
  expect(linked).toEqual(["/favicon.svg", "/favicon-32.png", "/apple-touch-icon.png"]);
  expect(pushed).toEqual(["/icon-192.png", "/badge-96.png"]);
  for (const src of [...manifest.icons.map((i) => i.src), ...linked, ...pushed, "/mark.svg"]) {
    expect(served.has(src), src).toBe(true);
  }
});
