import type { RefObject } from "react";
import { useState } from "react";

/** Wave-2 LANE D slot: fullscreen toggle for the chart stage. */
export function FullscreenButton({ targetRef }: { targetRef: RefObject<HTMLDivElement | null> }) {
  const [active, setActive] = useState(false);
  return (
    <button
      className={`btn small ${active ? "primary" : "ghost"}`}
      title={active ? "exit fullscreen" : "fullscreen chart"}
      onClick={() => {
        const el = targetRef.current;
        if (!el) return;
        if (document.fullscreenElement) void document.exitFullscreen().then(() => setActive(false));
        else void el.requestFullscreen().then(() => setActive(true)).catch(() => undefined);
      }}
    >
      ⛶
    </button>
  );
}
