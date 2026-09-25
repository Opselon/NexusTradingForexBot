/** Liquidity bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/liquidity",
    label: "Liquidity",
    icon: "≋",
    section: "MARKET & RESEARCH",
    legacyTab: "tab-liquidity",
  },
  lazy: featurePage(() => import("./ui/LiquidityPage")),
});
