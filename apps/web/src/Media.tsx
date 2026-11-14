import { useEffect, useRef, useState } from "react";

import { Icon } from "./Icon";
import { usePhone } from "./usePhone";
import { closes, follow, useSheet } from "./useSheet";

/** The deepest a picture zooms. */
export const MAX_SCALE = 5;

export type View = { s: number; x: number; y: number };

/** Keep a view zoomed in [1, MAX_SCALE] and panned no further than the picture's edges, for a
 * stage `w` by `h`; x and y are the picture's offset from the stage's centre. */
export function clamp({ s, x, y }: View, w: number, h: number): View {
  const k = Math.min(MAX_SCALE, Math.max(1, s));
  const mx = ((k - 1) * w) / 2;
  const my = ((k - 1) * h) / 2;
  return { s: k, x: Math.min(mx, Math.max(-mx, x)), y: Math.min(my, Math.max(-my, y)) };
}

/** `v` zoomed to scale `s` about point `p` (from the stage's centre), which stays put under the
 * finger. */
export function zoomAt(v: View, s: number, p: { x: number; y: number }): View {
  const r = s / v.s;
  return { s, x: p.x - (p.x - v.x) * r, y: p.y - (p.y - v.y) * r };
}

/** A double tap: 2x about the tapped point, or back to the whole picture. */
export function toggle(v: View, p: { x: number; y: number }): View {
  return v.s > 1 ? { s: 1, x: 0, y: 0 } : zoomAt(v, 2, p);
}

/** A picture or a video full screen on a modal `<dialog>`. A picture pinches and double-taps to
 * zoom and pans while zoomed; a drag down while it is whole closes it, as do Android's back,
 * Escape and the close button. A video plays with its own controls. Only the stage zooms: it is
 * `data-pinch`, so the page's pinch guard (pinch.ts) leaves it alone, and `touch-action: none`
 * keeps the page itself at scale 1. */
export function Viewer({ src, alt, video, onClose }: { src: string; alt: string; video?: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const shut = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!ref.current?.open) ref.current?.showModal();
    shut.current?.focus();
  }, []);
  const close = () => ref.current?.close();
  useSheet(ref, true, close, false);

  useEffect(() => {
    const el = stage.current;
    const pic = el?.querySelector("img");
    const dialog = ref.current;
    if (!el || !pic || !dialog) return;
    let v: View = { s: 1, x: 0, y: 0 };
    const draw = (swipe = 0) => {
      pic.style.transform = `translate(${v.x}px, ${v.y + swipe}px) scale(${v.s})`;
      el.dataset.scale = v.s.toFixed(2);
    };
    const at = (e: { clientX: number; clientY: number }) => {
      const r = el.getBoundingClientRect();
      return { x: e.clientX - r.left - r.width / 2, y: e.clientY - r.top - r.height / 2 };
    };
    const fit = (next: View) => (v = clamp(next, el.clientWidth, el.clientHeight));
    const down = new Map<number, { x: number; y: number }>();
    let pinch: { d: number; mid: { x: number; y: number }; v: View } | null = null;
    let drag: { x: number; y: number; t: number; v: View; moved: boolean } | null = null;
    let lastTap: { x: number; y: number; t: number } | null = null;
    const distance = () => {
      const [a, b] = [...down.values()];
      return { d: Math.hypot(a!.x - b!.x, a!.y - b!.y), mid: { x: (a!.x + b!.x) / 2, y: (a!.y + b!.y) / 2 } };
    };
    const onDown = (e: PointerEvent) => {
      el.setPointerCapture(e.pointerId);
      down.set(e.pointerId, at(e));
      el.setAttribute("data-gesture", "");
      if (down.size === 2) {
        pinch = { ...distance(), v };
        drag = null;
      } else if (down.size === 1) drag = { ...at(e), t: e.timeStamp, v, moved: false };
    };
    const onMove = (e: PointerEvent) => {
      if (!down.has(e.pointerId)) return;
      down.set(e.pointerId, at(e));
      if (pinch && down.size >= 2) {
        const { d, mid } = distance();
        const s = Math.min(MAX_SCALE, Math.max(1, (pinch.v.s * d) / Math.max(1, pinch.d)));
        const z = zoomAt(pinch.v, s, pinch.mid);
        fit({ s, x: z.x + mid.x - pinch.mid.x, y: z.y + mid.y - pinch.mid.y });
        return draw();
      }
      if (!drag) return;
      const p = at(e);
      const dx = p.x - drag.x;
      const dy = p.y - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 8) return;
      drag.moved = true;
      if (drag.v.s > 1) {
        fit({ ...drag.v, x: drag.v.x + dx, y: drag.v.y + dy });
        return draw();
      }
      // Whole: a drag down pulls the picture away and fades the dark behind it.
      const y = follow(dy);
      dialog.style.setProperty("--fade", String(Math.max(0, 1 - Math.max(0, y) / el.clientHeight)));
      draw(y);
    };
    const onUp = (e: PointerEvent) => {
      if (!down.delete(e.pointerId)) return;
      if (down.size) return void (pinch = null); // one finger of a pinch lifted: wait for the other
      el.removeAttribute("data-gesture");
      const was = drag;
      drag = pinch = null;
      if (!was) return;
      const p = at(e);
      if (!was.moved) {
        // A tap; a second within 300 ms near the first is a double tap.
        if (lastTap && e.timeStamp - lastTap.t < 300 && Math.hypot(p.x - lastTap.x, p.y - lastTap.y) < 40) {
          lastTap = null;
          fit(toggle(v, p));
          return draw();
        }
        lastTap = { ...p, t: e.timeStamp };
        return;
      }
      if (was.v.s > 1) return;
      const dy = p.y - was.y;
      if (e.type === "pointerup" && closes(dy, el.clientHeight, dy / Math.max(1, e.timeStamp - was.t))) return close();
      dialog.style.removeProperty("--fade");
      draw();
    };
    // Keep the browser from scrolling or zooming anything while a finger is on the stage.
    const touch = (e: TouchEvent) => e.preventDefault();
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    el.addEventListener("touchmove", touch, { passive: false });
    draw();
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
      el.removeEventListener("touchmove", touch);
    };
  }, []);

  return (
    <dialog ref={ref} className="viewer" aria-label={alt} onClose={onClose} data-testid="viewer">
      <div className="viewer-bar">
        <a className="viewer-btn" href={src} download aria-label={`Download ${alt}`}>
          <Icon name="download" size={20} />
        </a>
        <button ref={shut} type="button" className="viewer-btn" aria-label="Close" onClick={close}>
          <Icon name="x" size={20} />
        </button>
      </div>
      {video ? (
        <div className="viewer-stage">
          <video src={src} controls autoPlay playsInline aria-label={alt} />
        </div>
      ) : (
        <div ref={stage} className="viewer-stage" data-pinch data-scale="1" data-testid="viewer-stage">
          <img src={src} alt={alt} draggable={false} />
        </div>
      )}
    </dialog>
  );
}

/** A picture or video in a card, full width: a tall or wide picture is cropped to a sensible
 * height with an expand hint, and a tap opens it full screen in the Viewer. A video plays in
 * place, with a button to go full screen. */
export function Media({ src, alt, video, testId }: { src: string; alt: string; video?: boolean; testId?: string }) {
  const [open, setOpen] = useState(false);
  const phone = usePhone();
  return (
    <>
      {video ? (
        <span className="media media-video">
          <video controls preload="metadata" src={src} data-testid={testId} />
          <button type="button" className="media-expand" aria-label={`Full screen ${alt}`} onClick={() => setOpen(true)}>
            <Icon name="expand" size={20} />
          </button>
        </span>
      ) : (
        <button type="button" className="media media-image" aria-label={`Expand ${alt}`} onClick={() => setOpen(true)} data-testid={testId}>
          <img src={src} alt={alt} />
          <span className="media-hint" aria-hidden="true">
            <Icon name="expand" size={16} /> {phone ? "Tap" : "Click"} to expand
          </span>
        </button>
      )}
      {open && <Viewer src={src} alt={alt} video={video} onClose={() => setOpen(false)} />}
    </>
  );
}
