import { expect, test } from "vitest";
import { userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";

import "./index.css";

import { clamp, MAX_SCALE, Media, toggle, zoomAt } from "./Media";

// A tall picture, 40 by 200, like a contact sheet.
const TALL = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="200"><rect width="40" height="200" fill="teal"/></svg>')}`;

test("zoom keeps the point under the finger, clamps to the picture, and a double tap toggles 2x", () => {
  expect(zoomAt({ s: 1, x: 0, y: 0 }, 2, { x: 10, y: -20 })).toEqual({ s: 2, x: -10, y: 20 });
  expect(clamp({ s: 9, x: 1e4, y: -1e4 }, 100, 200)).toEqual({ s: MAX_SCALE, x: 200, y: -400 });
  expect(clamp({ s: 0.5, x: 30, y: 30 }, 100, 200)).toEqual({ s: 1, x: 0, y: 0 });
  expect(toggle({ s: 1, x: 0, y: 0 }, { x: 0, y: 0 })).toEqual({ s: 2, x: 0, y: 0 });
  expect(toggle({ s: 3, x: 5, y: 5 }, { x: 0, y: 0 })).toEqual({ s: 1, x: 0, y: 0 });
});

test("a picture shows full width with a hint; a tap opens it full screen, a double tap zooms, close shuts it", async () => {
  const screen = await render(
    <div style={{ width: 360 }}>
      <Media src={TALL} alt="contact_sheet" />
    </div>,
  );
  const open = screen.getByRole("button", { name: "Expand contact_sheet" });
  await expect.element(open).toBeVisible();
  expect(open.element().getBoundingClientRect().width).toBe(360);
  // Cropped to a sensible height, not shrunk to a thin letterbox.
  expect(open.element().querySelector("img")!.getBoundingClientRect().height).toBeLessThanOrEqual(28 * 16);
  expect(open.element().textContent).toContain("to expand");

  await open.click();
  const viewer = screen.getByRole("dialog", { name: "contact_sheet" });
  await expect.element(viewer).toBeVisible();
  expect((viewer.element() as HTMLDialogElement).matches(":modal")).toBe(true);
  await expect.element(viewer.getByRole("button", { name: "Close" })).toHaveFocus();
  await expect.element(viewer.getByRole("link", { name: "Download contact_sheet" })).toHaveAttribute("href", TALL);

  const stage = screen.getByTestId("viewer-stage");
  await expect.element(stage).toHaveAttribute("data-scale", "1.00");
  await userEvent.dblClick(stage);
  await expect.element(stage).toHaveAttribute("data-scale", "2.00");
  await userEvent.dblClick(stage);
  await expect.element(stage).toHaveAttribute("data-scale", "1.00");

  await viewer.getByRole("button", { name: "Close" }).click();
  await expect.element(viewer).not.toBeInTheDocument();
  // Escape closes it too.
  await open.click();
  await expect.element(screen.getByRole("dialog", { name: "contact_sheet" })).toBeVisible();
  await userEvent.keyboard("{Escape}");
  await expect.element(screen.getByRole("dialog", { name: "contact_sheet" })).not.toBeInTheDocument();
});

test("a video plays in place with its controls and goes full screen with them", async () => {
  const screen = await render(<Media video src="/short.mp4" alt="short" testId="player" />);
  const player = screen.getByTestId("player");
  expect((player.element() as HTMLVideoElement).controls).toBe(true);
  await screen.getByRole("button", { name: "Full screen short" }).click();
  const viewer = screen.getByRole("dialog", { name: "short" });
  await expect.element(viewer).toBeVisible();
  const big = viewer.element().querySelector("video")!;
  expect(big.controls).toBe(true);
  expect(big.getAttribute("src")).toBe("/short.mp4");
});
