import { DDManager } from 'gridstack';

/**
 * Scrolling the page while a widget is dragged, in place of gridstack's own (turned off in Board.tsx
 * with `draggable.scroll: false`). gridstack scrolls only once the dragged widget is cut off by the
 * window's edge, which a tall widget is from the moment it is picked up (the page ran away downwards)
 * and the top one almost never is under the sticky edit bar (it never scrolled up). Here the pointer
 * alone decides: near the window's bottom, or just under the edit bar, the page scrolls that way,
 * faster the closer it gets, and stops as soon as the pointer leaves the edge.
 */

// How close to an edge the pointer starts the scroll, and the fastest it goes (pixels a frame).
const EDGE = 64;
const FASTEST = 22;
const SLOWEST = 4;

let pointerX = 0;
let pointerY = -1;
// Where the drag began: the page scrolls toward an edge only once the pointer has headed for it.
let startY = -1;
const HEADED = 10;
let frame = 0;
// Set while this file tells gridstack about a move of its own, so that move does not start a second loop.
let nudging = false;

/** The top of the room the board shows in: under the edit bar while it is stuck to the top. */
function topEdge() {
  const bar = document.querySelector('.board-editbar');
  const bottom = bar ? bar.getBoundingClientRect().bottom : 0;
  return Math.max(0, Math.min(bottom, window.innerHeight / 3));
}

function speed() {
  const top = topEdge();
  const bottom = window.innerHeight;
  const ramp = (depth: number) => SLOWEST + (FASTEST - SLOWEST) * Math.min(1, depth / EDGE);
  if (pointerY < top + EDGE && (startY - pointerY >= HEADED || pointerY <= top + HEADED))
    return -ramp(top + EDGE - pointerY);
  if (pointerY > bottom - EDGE && (pointerY - startY >= HEADED || pointerY >= bottom - HEADED))
    return ramp(pointerY - (bottom - EDGE));
  return 0;
}

function tick() {
  frame = 0;
  if (!DDManager.dragElement || pointerY < 0) return;
  const step = speed();
  if (!step) return;
  const before = window.scrollY;
  window.scrollBy(0, step);
  if (window.scrollY === before) return; // at the top or the bottom of the page
  // gridstack places the widget from pointer moves; after the page moved under a still pointer,
  // tell it the pointer is (relative to the board) somewhere new.
  nudging = true;
  try {
    document.dispatchEvent(
      new MouseEvent('mousemove', {
        clientX: pointerX,
        clientY: pointerY,
        bubbles: true,
        cancelable: true,
        view: window,
      }),
    );
  } finally {
    nudging = false;
  }
  frame = requestAnimationFrame(tick);
}

function follow(event: MouseEvent) {
  if (nudging) return;
  if (!DDManager.dragElement) {
    pointerY = -1;
    startY = -1;
    return;
  }
  if (startY < 0) startY = event.clientY;
  pointerX = event.clientX;
  pointerY = event.clientY;
  if (!frame) frame = requestAnimationFrame(tick);
}

function stop() {
  pointerY = -1;
  startY = -1;
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
}

if (typeof document !== 'undefined') {
  // gridstack turns touch moves into mouse moves too, so this hears both.
  document.addEventListener('mousemove', follow, { capture: true, passive: true });
  document.addEventListener('mouseup', stop, true);
  document.addEventListener('touchend', stop, true);
}
