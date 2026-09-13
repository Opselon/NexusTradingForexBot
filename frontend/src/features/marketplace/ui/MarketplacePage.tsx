/**
 * Marketplace tab — legacy tab-marketplace parity (Web/marketplace.js, 6 panels).
 *
 * All traffic is the versioned platform: /api/v1/marketplace/* with the
 * `{data, meta, error}` envelope (unwrapped in features/marketplace/api.ts).
 * Sections: packs grid (+guarded install), seeds table (+research/enable/
 * disable/repair POSTs behind confirms, detail drawer with score history),
 * rankings per dimension, repairs list, runtime snapshot panel.
 */

import { PacksSection } from "./PacksSection";
import { RankingsSection } from "./RankingsSection";
import { RepairsSection, RuntimeSnapshotSection } from "./RepairsAndSnapshot";
import { SeedsSection } from "./SeedsSection";
import "./marketplace.css";

export default function MarketplacePage() {
  return (
    <div>
      <div className="page-head">
        <h1>Strategy Marketplace & Research Lab</h1>
        <span className="crumb">legacy tab-marketplace</span>
        <span className="desc">
          discover · install · validate · score · repair · govern seeds — isolated marketplace.db, v1 envelope API
        </span>
      </div>
      <PacksSection />
      <SeedsSection />
      <div className="grid cols-2">
        <RankingsSection />
        <RepairsSection />
      </div>
      <RuntimeSnapshotSection />
    </div>
  );
}
