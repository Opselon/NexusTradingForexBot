import { useEffect, useRef, useState, type RefObject } from "react";
import "./chartControls.css";

/** Wave-2 LANE D slot: PNG snapshot of the chart canvas. Core export logic
 *  unchanged; after a successful download the label flips to "✓ saved" for
 *  1.2s (timer cleared on unmount). */
export function SnapshotButton({ canvasRef }: { canvasRef: RefObject<HTMLCanvasElement | null> }) {
  const [saved, setSaved] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  return (
    <button
      className={saved ? "flash" : "btn small ghost"}
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
          setSaved(true);
          if (timerRef.current !== null) window.clearTimeout(timerRef.current);
          timerRef.current = window.setTimeout(() => {
            setSaved(false);
            timerRef.current = null;
          }, 1200);
        }, "image/png");
      }}
    >
      {saved ? "✓ saved" : "⤓ PNG"}
    </button>
  );
}
