/** Research bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/research",
    label: "Research",
    icon: "⚗",
    section: "MARKET & RESEARCH",
    legacyTab: "tab-research",
  },
  lazy: featurePage(() => import("./ui/ResearchPage")),
});
