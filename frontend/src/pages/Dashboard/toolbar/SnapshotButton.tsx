import type { RefObject } from "react";

/** Wave-2 LANE D slot: PNG snapshot of the chart canvas (scaffold ships the
 *  working core; lane D adds feedback flash + filename polish). */
export function SnapshotButton({ canvasRef }: { canvasRef: RefObject<HTMLCanvasElement | null> }) {
  return (
    <button
      className="btn small ghost"
      title="download chart snapshot (PNG)"
      onClick={() => {
        const cv = canvasRef.current;
        if (!cv) return;
        cv.toBlob((blob) => {
          if (!blob) return;
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `nse-chart-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
          a.click();
          URL.revokeObjectURL(url);
        }, "image/png");
      }}
    >
      ⤓ PNG
    </button>
  );
}
