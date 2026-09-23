/** Wave-2 LANE B slot: SMC overlay visibility popover (scaffold ships the
 *  bare button; lane B adds the checkbox menu wired to useChartSettings()
 *  setOverlayVisible — engine-computed values, visibility only, lane-09 safe). */
export function ChartOverlaysButton() {
  return (
    <button className="btn small ghost" title="SMC overlay visibility (zones / BOS / midlines / liquidity / order lines)">
      ◫ SMC
    </button>
  );
}
