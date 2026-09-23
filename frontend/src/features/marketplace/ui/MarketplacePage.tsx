/**
 * Marketplace tab — legacy tab-marketplace parity (Web/marketplace.js, 6 panels).
 *
 * All traffic is the versioned platform: /api/v1/marketplace/* with the
 * `{data, meta, error}` envelope (unwrapped in features/marketplace/api.ts).
 * Sections: hero telemetry ribbon, anchor nav, packs grid (+guarded install),
 * seeds table (+research/enable/disable/repair POSTs behind confirms, detail
 * drawer with score history), rankings per dimension, repairs list, runtime
 * snapshot tiles.
 *
 * Honesty contract (unchanged from the legacy UI): every figure below is
 * served by the backend — nothing is derived client-side, a missing score
 * renders NOT_AVAILABLE (never a fake 0), and enablement outcomes
 * PENDING / DENIED / granted are distinct backend words.
 */

import { useI18n } from "@/stores/i18nStore";
import { useMktPacks, useMktSnapshot } from "../hooks";
import { PacksSection } from "./PacksSection";
import { RankingsSection } from "./RankingsSection";
import { RepairsSection, RuntimeSnapshotSection } from "./RepairsAndSnapshot";
import { SeedsSection } from "./SeedsSection";
import "./marketplace.css";

/** Static ids/icons only — labels are t()d inside the render (never cached). */
const SECTION_META: Array<{ id: string; icon: string }> = [
  { id: "mkt-packs", icon: "▦" },
  { id: "mkt-seeds", icon: "◈" },
  { id: "mkt-rankings", icon: "Σ" },
  { id: "mkt-repairs", icon: "⚒" },
  { id: "mkt-snapshot", icon: "⬢" },
];

export default function MarketplacePage() {
  const t = useI18n((s) => s.t);
  const packs = useMktPacks();
  const snapshot = useMktSnapshot();

  const packList = packs.data?.packs ?? [];
  const installedPacks = packList.filter((p) => p.installed).length;

  const sectionLabel = (id: string): string =>
    id === "mkt-packs"
      ? t("marketplace.nav.packs", "Packs")
      : id === "mkt-seeds"
        ? t("marketplace.nav.seeds", "Seeds")
        : id === "mkt-rankings"
          ? t("marketplace.nav.rankings", "Rankings")
          : id === "mkt-repairs"
            ? t("marketplace.nav.repairs", "Repairs")
            : t("marketplace.nav.runtime", "Runtime");

  return (
    <div className="mkt-container">
      <MarketplaceHero
        packCount={packList.length}
        installedPacks={installedPacks}
        snapshotVersion={snapshot.data?.version ?? null}
        enabledCount={(snapshot.data?.enabled_set ?? []).length}
        packsLoaded={packs.isSuccess}
      />
      <nav className="mkt-nav" aria-label={t("marketplace.nav.sections_aria", "Marketplace sections")}>
        {SECTION_META.map((s) => (
          <a key={s.id} className="mkt-nav-item" href={`#${s.id}`}>
            <span className="ico">{s.icon}</span>
            {sectionLabel(s.id)}
          </a>
        ))}
      </nav>
      <div id="mkt-packs">
        <PacksSection />
      </div>
      <div id="mkt-seeds">
        <SeedsSection />
      </div>
      <div id="mkt-rankings" className="grid cols-2">
        <RankingsSection />
        <RepairsSection />
      </div>
      <div id="mkt-snapshot">
        <RuntimeSnapshotSection />
      </div>
    </div>
  );
}

function MarketplaceHero({
  packCount,
  installedPacks,
  snapshotVersion,
  enabledCount,
  packsLoaded,
}: {
  packCount: number;
  installedPacks: number;
  snapshotVersion: number | string | null;
  enabledCount: number;
  packsLoaded: boolean;
}) {
  const t = useI18n((s) => s.t);
  const allInstalled = packsLoaded && packCount > 0 && installedPacks === packCount;
  return (
    <div className="mkt-page-hero">
      <div className="mkt-hero-left">
        <div className="mkt-hero-icon" aria-hidden="true">
          ◍
        </div>
        <div style={{ minWidth: 0 }}>
          <h1 className="mkt-hero-title">
            {t("nav.feature.marketplace", "MARKETPLACE")}
            <span className="mkt-hero-badge">{t("marketplace.hero.badge", "RESEARCH LAB")}</span>
          </h1>
          <div className="mkt-hero-sub">
            {t(
              "marketplace.hero.sub",
              "Discover, install, validate, score, repair and govern strategy seeds across isolated research persistence. Every state below is served by the v1 envelope API — the console never grants enablement, never invents a score.",
            )}
          </div>
        </div>
      </div>
      <div className="mkt-hero-right">
        <div className="mkt-ribbon" role="group" aria-label={t("marketplace.hero.telemetry_aria", "Marketplace telemetry")}>
          <span
            className={`mkt-live-dot ${allInstalled ? "" : "off"}`}
            title={
              allInstalled
                ? t("marketplace.hero.dot_all", "all available packs installed")
                : t("marketplace.hero.dot_partial", "pack catalog partially installed")
            }
          />
          <span className="mkt-ribbon-item">
            <span className="lab">{t("marketplace.nav.packs", "Packs")}</span>
            <span className="val">
              {installedPacks}/{packCount}
            </span>
          </span>
          <span className="mkt-ribbon-sep" />
          <span className="mkt-ribbon-item">
            <span className="lab">{t("marketplace.hero.lab_installed", "Installed")}</span>
            <span className={`val ${enabledCount ? "pos" : "dim"}`}>{enabledCount}</span>
          </span>
          <span className="mkt-ribbon-sep" />
          <span className="mkt-ribbon-item">
            <span className="lab">{t("marketplace.hero.lab_snapshot", "Snapshot")}</span>
            <span className={`val ${snapshotVersion === null ? "dim" : ""}`}>
              v{snapshotVersion ?? "—"}
            </span>
          </span>
          <span className="mkt-ribbon-sep" />
          <span className="mkt-ribbon-item">
            <span className="lab">API</span>
            <span className="val">v1</span>
          </span>
        </div>
      </div>
    </div>
  );
}
