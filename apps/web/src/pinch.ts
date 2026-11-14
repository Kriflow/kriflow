/** No pinch zoom: Android honours the viewport's user-scalable=no, iOS ignores it, so stop the
 * gesture here. Double-tap zoom is off in CSS (touch-action: manipulation). An element marked
 * `data-pinch` zooms itself and keeps the gesture: the full-screen media Viewer's stage, which has
 * `touch-action: none`, so the page stays at scale 1 while the picture zooms. */
const keeps = (e: Event) => e.target instanceof Element && e.target.closest("[data-pinch]") !== null;

export function stopPinch(doc: Document = document) {
  doc.addEventListener("gesturestart", (e) => {
    if (!keeps(e)) e.preventDefault();
  });
  doc.addEventListener(
    "touchmove",
    (e) => {
      if (e.touches.length > 1 && !keeps(e)) e.preventDefault();
    },
    { passive: false },
  );
}
