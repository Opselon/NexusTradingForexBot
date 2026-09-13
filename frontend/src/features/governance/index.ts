/** Governance bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/governance",
    label: "Governance",
    icon: "⚖",
    section: "SAFETY & GOVERNANCE",
    legacyTab: "tab-governance",
  },
  lazy: featurePage(() => import("./ui/GovernancePage")),
});
