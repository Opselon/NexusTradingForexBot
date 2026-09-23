/**
 * RulesStats — the four summary cards above the matrix (total / enabled /
 * categories / bad parameters) plus the enabled-ratio donut.
 *
 * Honesty: every number is derived from the SAME rows the table renders
 * (backend payload mapped in model.ts) — no client-side fabrication, and a
 * zero "bad parameters" card renders dim instead of celebratory green.
 * Presentation only; no fetch, no commands.
 */

import { useMemo, type CSSProperties } from "react";
import type { RuleVO } from "../model";

export function RulesStats({ all, cats }: { all: RuleVO[]; cats: string[] }) {
  // perf(7): two filter passes over the payload rows — memoized per `all`
  // identity so unrelated page re-renders don't re-scan the whole matrix.
  const { total, enabled, broken } = useMemo(() => {
    const en = all.filter((r) => r.enabled).length;
    return { total: all.length, enabled: en, broken: all.filter((r) => r.paramsError).length };
  }, [all]);
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
}
