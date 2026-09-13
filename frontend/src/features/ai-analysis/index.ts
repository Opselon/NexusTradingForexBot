/** AI Analysis bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/ai-analysis",
    label: "AI Analysis",
    icon: "λ",
    section: "MARKET & RESEARCH",
    legacyTab: "tab-ai-analysis",
  },
  lazy: featurePage(() => import("./ui/AiAnalysisPage")),
});
