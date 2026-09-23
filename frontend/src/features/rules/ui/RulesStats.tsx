/**
 * RulesStats — the four summary cards above the matrix (total / enabled /
 * categories / bad parameters) plus the enabled-ratio donut.
 *
 * Honesty: every number is derived from the SAME rows the table renders
 * (backend payload mapped in model.ts) — no client-side fabrication, and a
 * zero "bad parameters" card renders dim instead of celebratory green.
 * Presentation only; no fetch, no commands.
 */

import { memo, type CSSProperties } from "react";
import type { RuleVO } from "../model";

/** Memoized stat cards: `all`/`cats` are memoized upstream, so the shell's 1s
 *  tick skips the three array scans + donut rebuild entirely. */
export const RulesStats = memo(function RulesStats({ all, cats }: { all: RuleVO[]; cats: string[] }) {
  const total = all.length;
  const enabled = all.filter((r) => r.enabled).length;
  const broken = all.filter((r) => r.paramsError).length;
  const pct = total > 0 ? Math.round((enabled / total) * 100) : 0;

  return (
    <div className="rl-stats">
      <div className="rl-stat t-blue">
        <span className="ico" aria-hidden="true">§</span>
        <div className="body">
          <div className="k">total rules</div>
          <div className="v">{total}</div>
          <div className="s">rows from /api/rules</div>
        </div>
      </div>

      <div className="rl-stat t-green">
        <span className="ico" aria-hidden="true">✓</span>
        <div className="body">
          <div className="k">enabled</div>
          <div className="v">
            {enabled}
            <i>/{total}</i>
          </div>
          <div className="s">{total - enabled} disabled</div>
        </div>
        <div
          className="rl-donut"
          style={{ "--rl-p": pct } as CSSProperties}
          role="img"
          aria-label={`${pct}% of rules enabled`}
          title={`${enabled} of ${total} rules enabled`}
        >
          <b>{pct}%</b>
        </div>
      </div>

      <div className="rl-stat t-violet">
        <span className="ico" aria-hidden="true">#</span>
        <div className="body">
          <div className="k">categories</div>
          <div className="v">{cats.length}</div>
          <div className="s">{cats.slice(0, 3).join(" · ") || "—"}</div>
        </div>
      </div>

      <div className="rl-stat t-amber">
        <span className="ico" aria-hidden="true">!</span>
        <div className="body">
          <div className="k">bad parameters</div>
          <div className={`v ${broken > 0 ? "neg" : "dim"}`}>{broken}</div>
          <div className="s">stored JSON unparseable</div>
        </div>
      </div>
    </div>
  );
});
