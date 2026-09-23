/**
 * RulesStats — the four summary cards above the matrix (total / enabled /
 * categories / bad parameters) plus the enabled-ratio donut.
 *
 * Honesty: every number is derived from the SAME rows the table renders
 * (backend payload mapped in model.ts) — no client-side fabrication, and a
 * zero "bad parameters" card renders dim instead of celebratory green.
 * Presentation only; no fetch, no commands.
 */

import type { CSSProperties } from "react";
import { useI18n } from "@/stores/i18nStore";
import type { RuleVO } from "../model";

export function RulesStats({ all, cats }: { all: RuleVO[]; cats: string[] }) {
  const total = all.length;
  const enabled = all.filter((r) => r.enabled).length;
  const broken = all.filter((r) => r.paramsError).length;
  const pct = total > 0 ? Math.round((enabled / total) * 100) : 0;
  const t = useI18n((s) => s.t);

  return (
    <div className="rl-stats">
      <div className="rl-stat t-blue">
        <span className="ico" aria-hidden="true">§</span>
        <div className="body">
          <div className="k">{t("rules.metric.total_rules", "total rules")}</div>
          <div className="v">{total}</div>
          <div className="s">{t("rules.metric.rows_from", "rows from /api/rules")}</div>
        </div>
      </div>

      <div className="rl-stat t-green">
        <span className="ico" aria-hidden="true">✓</span>
        <div className="body">
          <div className="k">{t("rules.metric.enabled", "enabled")}</div>
          <div className="v">
            {enabled}
            <i>/{total}</i>
          </div>
          <div className="s">{t("rules.metric.enabled_sub", "{n} disabled", { n: total - enabled })}</div>
        </div>
        <div
          className="rl-donut"
          style={{ "--rl-p": pct } as CSSProperties}
          role="img"
          aria-label={t("rules.metric.donut_a11y", "{pct}% of rules enabled", { pct })}
          title={t("rules.metric.donut_title", "{enabled} of {total} rules enabled", { enabled, total })}
        >
          <b>{pct}%</b>
        </div>
      </div>

      <div className="rl-stat t-violet">
        <span className="ico" aria-hidden="true">#</span>
        <div className="body">
          <div className="k">{t("rules.metric.categories", "categories")}</div>
          <div className="v">{cats.length}</div>
          <div className="s">{cats.slice(0, 3).join(" · ") || "—"}</div>
        </div>
      </div>

      <div className="rl-stat t-amber">
        <span className="ico" aria-hidden="true">!</span>
        <div className="body">
          <div className="k">{t("rules.metric.bad_params", "bad parameters")}</div>
          <div className={`v ${broken > 0 ? "neg" : "dim"}`}>{broken}</div>
          <div className="s">{t("rules.metric.bad_params_sub", "stored JSON unparseable")}</div>
        </div>
      </div>
    </div>
  );
}
