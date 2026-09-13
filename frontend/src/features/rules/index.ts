/** Rules bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/rules",
    label: "Rules",
    icon: "§",
    section: "SAFETY & GOVERNANCE",
    legacyTab: "tab-rules",
  },
  lazy: featurePage(() => import("./ui/RulesPage")),
});
