/** News bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/news",
    label: "News",
    icon: "≈",
    section: "MARKET & RESEARCH",
    legacyTab: "tab-news",
  },
  lazy: featurePage(() => import("./ui/NewsPage")),
});
