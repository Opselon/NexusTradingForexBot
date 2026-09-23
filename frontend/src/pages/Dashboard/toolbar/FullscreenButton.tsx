import { useEffect, useState, type RefObject } from "react";

/** Wave-2 LANE D slot: fullscreen toggle for the chart stage. Active state
 *  is derived from document.fullscreenElement via the fullscreenchange event
 *  (initial value read from the document too), so ESC / browser-initiated
 *  exits stay in sync — no promise-chain race between click and state. */
export function FullscreenButton({ targetRef }: { targetRef: RefObject<HTMLDivElement | null> }) {
  const [active, setActive] = useState(false);

  useEffect(() => {
    const sync = () => setActive(document.fullscreenElement !== null);
    sync(); // initial state straight from the document
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  return (
    <button
      className={`btn small ${active ? "primary" : "ghost"}`}
      title={active ? "exit fullscreen" : "fullscreen chart"}
      aria-label={active ? "exit fullscreen" : "fullscreen chart"}
      onClick={() => {
        if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
        else void targetRef.current?.requestFullscreen().catch(() => undefined);
      }}
    >
      ⛶
    </button>
  );
}
