/**
 * ReplayControls — surgical backward stepping over recorded events (§36).
 *
 * Plays an immutable, already-recorded trace event-by-event at fixed
 * intervals. NEVER rewinds the engine, NEVER resends commands (§36).
 * Speeds are display cadence only: 0.5×/1×/2×/4× of a 400ms base step.
 */

import { memo, useEffect, useRef } from "react";

interface Props {
  active: boolean;
  index: number;
  total: number;
  speed: number;
  playing: boolean;
  onStart: () => void;
  onStop: () => void;
  onStep: (delta: number) => void;
  onSpeed: (speed: number) => void;
  onPlaying: (playing: boolean) => void;
}

const SPEEDS = [0.5, 1, 2, 4];
const BASE_STEP_MS = 400;

export const ReplayControls = memo(function ReplayControls({
  active,
  index,
  total,
  speed,
  playing,
  onStart,
  onStop,
  onStep,
  onSpeed,
  onPlaying,
}: Props) {
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!playing || !active || total === 0) return undefined;
    timer.current = setInterval(() => {
      if (index >= total - 1) {
        onPlaying(false);
        return;
      }
      onStep(1);
    }, BASE_STEP_MS / speed);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [playing, active, total, index, speed, onStep, onPlaying]);

  if (!active) {
    return (
      <div className="dt-replay">
        <button className="dt-replay-btn" onClick={onStart} title="Replay this trace step-by-step (recorded events only — the engine is never rewound)">
          ⟳ Replay trace
        </button>
      </div>
    );
  }

  return (
    <div className="dt-replay active" role="group" aria-label="Replay controls">
      <button className="dt-replay-btn" onClick={() => onStep(-1)} disabled={index <= 0} aria-label="Previous event">⏮</button>
      <button
        className="dt-replay-btn play"
        onClick={() => onPlaying(!playing)}
        aria-label={playing ? "Pause replay" : "Play replay"}
      >
        {playing ? "⏸" : "⏵"}
      </button>
      <button className="dt-replay-btn" onClick={() => onStep(1)} disabled={index >= total - 1} aria-label="Next event">⏭</button>
      <span className="dt-replay-pos">
        {total ? `${index + 1} / ${total}` : "0 / 0"} events
      </span>
      <span className="dt-replay-speeds" role="group" aria-label="Speed">
        {SPEEDS.map((s) => (
          <button
            key={s}
            className={`dt-replay-speed ${speed === s ? "active" : ""}`}
            onClick={() => onSpeed(s)}
            aria-pressed={speed === s}
          >
            {s}×
          </button>
        ))}
      </span>
      <button className="dt-replay-btn exit" onClick={onStop} aria-label="Exit replay">✕ Exit</button>
    </div>
  );
});
