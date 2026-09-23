/**
 * Wave-3 LANE B seam: TF-aware time axis ticks.
 *
 * `paintChart` today draws 4 fixed labels from shown bar timestamps. This
 * module computes a denser, non-colliding, timeframe-aware tick set over the
 * VISIBLE window only: M1/M5/M10 -> "HH:MM"; H1/H4 -> "MM-DD HH:MM"; D1/W1 ->
 * "MM-DD" (+ "YY" for W1 when the year changes). Labels are deduped by text
 * and spaced so they never overlap the price axis or each other.
 */
export interface TimeTick {
  /** Slot index (window-relative) the label anchors at. */
  slot: number;
  label: string;
}

export function niceTimeTicks(
  _startTime: string | null,
  _endTime: string | null,
  _slotW: number,
  _maxLabels = 8,
): TimeTick[] {
  /* LANE B implements. */
  return [];
}
