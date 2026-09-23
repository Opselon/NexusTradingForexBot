/**
 * PURPOSE:  Glowing state pill (dot + label + backend word) for the hero strip and action rack.
 * OWNER:    uiux-wave5-control
 * CONSUMES: ./tones (GlowTone)
 * PROVIDES: StatusGlow
 * INVARIANTS: renders only the caller's backend-derived word — never infers
 *             state; the dot pulse is decorative and gated by
 *             prefers-reduced-motion in ui/control-center.css.
 * EXTEND:   pass a new GlowTone + matching CSS tone rule; no layout changes.
 */
import type { GlowTone } from "./tones";

export function StatusGlow({
  label,
  tone,
  text,
  title,
}: {
  /** short mono key, e.g. ENGINE / MODE / TICK */
  label: string;
  tone: GlowTone;
  /** the backend's own word (RUNNING / STOPPED / LIVE / …) */
  text: string;
  /** which backend fields this pill mirrors */
  title?: string;
}) {
  return (
    <span className={`ctl-glow ctl-glow--${tone}`} title={title ?? `${label} ${text}`}>
      <span className="ctl-dot" aria-hidden="true" />
      <span className="ctl-glow-label">{label}</span>
      <span className="ctl-glow-text">{text}</span>
    </span>
  );
}
