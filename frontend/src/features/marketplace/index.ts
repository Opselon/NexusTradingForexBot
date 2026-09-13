/** Marketplace bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/marketplace",
    label: "Marketplace",
    icon: "◍",
    section: "MARKET & RESEARCH",
    legacyTab: "tab-marketplace",
  },
  lazy: featurePage(() => import("./ui/MarketplacePage")),
});
