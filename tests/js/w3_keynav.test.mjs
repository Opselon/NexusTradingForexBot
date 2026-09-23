/**
 * Wave-3 Lane B — keyboardNav.ts (navForKey / isTypingFocus / hook).
 *
 * WHY: PriceChart's stage advertises "arrow keys pan the view" in its
 * aria-label but no listener existed. These tests pin the contract:
 * one-bar pans (Shift=10), viewport PageUp/PageDown, Home/End oldest/newest,
 * L follow-live, form fields never lose their keys, ctrl/meta/alt ignored,
 * and no-ops return null so the handler can skip setView.
 * Run: npx node --test tests/js/w3_keynav.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  navForKey,
  isTypingFocus,
  NAV_SHIFT_STEP,
  useChartKeyboardNav,
} from "../../frontend/src/pages/Dashboard/chart/keyboardNav.ts";

/* Fixture mirrors PriceChart's view math (PriceChart.tsx L78-80, L184-208):
 * barCount = len + maxFutureFor(visible); followLive effLeft = len - visible;
 * leftMax = len + maxFuture(visible) - visible; drag/wheel recompute
 * followLive as (clamped left === len - visible). */
const VIS = 180;
const LEN = 300;
const FUTURE = Math.floor(VIS * 0.4); // 72
const BAR_COUNT = LEN + FUTURE; // 372 — what the hook receives
const LIVE_LEFT = LEN - VIS; // 120
const LEFT_MAX = LEN + FUTURE - VIS; // 192

const liveView = { visible: VIS, left: 0, followLive: true }; // stale left; effLeft = LIVE_LEFT
const panned = { visible: VIS, left: 50, followLive: false };

/** Run one key through navForKey, apply the updater, return next view or null. */
const press = (view, ev, barCount = BAR_COUNT) => {
  const apply = navForKey(ev, view, barCount);
  return apply ? apply(view) : null;
};

test("ArrowLeft pans one bar off the live edge and unfollows", () => {
  assert.deepEqual(press(liveView, { key: "ArrowLeft" }), {
    visible: VIS, left: LIVE_LEFT - 1, followLive: false,
  });
  assert.equal(NAV_SHIFT_STEP, 10);
});

test("Shift+ArrowLeft pans ten bars", () => {
  assert.deepEqual(press(liveView, { key: "ArrowLeft", shiftKey: true }), {
    visible: VIS, left: LIVE_LEFT - 10, followLive: false,
  });
});

test("ArrowRight steps forward from a panned window without touching zoom", () => {
  const before = { ...panned };
  assert.deepEqual(press(panned, { key: "ArrowRight" }),
    { visible: VIS, left: 51, followLive: false });
  assert.deepEqual(panned, before, "input view must not be mutated");
});

test("ArrowRight from live matches drag: steps past the live edge", () => {
  assert.deepEqual(press(liveView, { key: "ArrowRight" }),
    { visible: VIS, left: LIVE_LEFT + 1, followLive: false });
});

test("ArrowLeft at the oldest slot is a no-op (null, no setView)", () => {
  assert.equal(press({ visible: VIS, left: 0, followLive: false },
    { key: "ArrowLeft" }), null);
});

test("PageDown jumps a viewport and clamps at the future edge", () => {
  assert.deepEqual(press(liveView, { key: "PageDown" }),
    { visible: VIS, left: LEFT_MAX, followLive: false });
});

test("PageUp jumps back a viewport to the oldest slot", () => {
  assert.deepEqual(press(liveView, { key: "PageUp" }),
    { visible: VIS, left: 0, followLive: false });
});

test("Home snaps to the oldest bar", () => {
  assert.deepEqual(press(panned, { key: "Home" }),
    { visible: VIS, left: 0, followLive: false });
});

test("End snaps to the newest bar and re-pins live", () => {
  assert.deepEqual(press(panned, { key: "End" }),
    { visible: VIS, left: LIVE_LEFT, followLive: true });
});

test("L and l both re-pins followLive at the live edge", () => {
  for (const key of ["L", "l"]) {
    assert.deepEqual(press(panned, { key }),
      { visible: VIS, left: LIVE_LEFT, followLive: true });
  }
});

test("pan back then ArrowRight returns to live (drag parity)", () => {
  const back = press(liveView, { key: "ArrowLeft" });
  assert.deepEqual(press(back, { key: "ArrowRight" }),
    { visible: VIS, left: LIVE_LEFT, followLive: true });
});

test("ctrl/meta/alt modifiers are ignored entirely", () => {
  for (const mod of ["ctrlKey", "metaKey", "altKey"]) {
    assert.equal(navForKey({ key: "ArrowLeft", [mod]: true }, liveView, BAR_COUNT),
      null, `${mod} must be ignored`);
  }
});

test("unhandled keys are ignored (Escape belongs to other lanes)", () => {
  for (const key of ["a", "Escape", "Shift", "Enter", ""]) {
    assert.equal(navForKey({ key }, liveView, BAR_COUNT), null, key);
  }
});

test("Shift only scales arrows, not PageUp or L", () => {
  assert.deepEqual(press(liveView, { key: "PageUp", shiftKey: true }),
    press(liveView, { key: "PageUp" }));
  assert.deepEqual(press(panned, { key: "L", shiftKey: true }),
    press(panned, { key: "L" }));
});

test("short data (fewer bars than slots) makes keys inert", () => {
  const barCount = 10 + FUTURE; // len = 10
  for (const key of ["ArrowRight", "ArrowLeft", "Home", "End", "PageDown"]) {
    assert.equal(press({ visible: VIS, left: 0, followLive: true },
      { key }, barCount), null, key);
  }
});

test("no-op at the future edge returns null", () => {
  assert.equal(press({ visible: VIS, left: LEFT_MAX, followLive: false },
    { key: "ArrowRight" }), null);
});

test("degenerate view or barCount is refused", () => {
  assert.equal(navForKey({ key: "ArrowLeft" }, { visible: 0, left: 0, followLive: false }, BAR_COUNT), null);
  assert.equal(navForKey({ key: "ArrowLeft" }, liveView, 0), null);
  assert.equal(navForKey({ key: "ArrowLeft" }, liveView, NaN), null);
});

test("isTypingFocus: form controls and editable regions win", () => {
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    assert.equal(isTypingFocus({ tagName }), true, tagName);
  }
  assert.equal(isTypingFocus({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isTypingFocus({ tagName: "SPAN", isContentEditable: false }), false);
  assert.equal(isTypingFocus({ tagName: "DIV" }), false);
  assert.equal(isTypingFocus({ tagName: "BODY" }), false);
  assert.equal(isTypingFocus(null), false);
});

test("hook is exported (react resolvable from chart/ under node --test)", () => {
  assert.equal(typeof useChartKeyboardNav, "function");
  assert.equal(typeof navForKey, "function");
  assert.equal(typeof isTypingFocus, "function");
});
