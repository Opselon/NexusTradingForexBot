/** Incidents bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/incidents",
    label: "Incidents",
    icon: "⚠",
    section: "SAFETY & GOVERNANCE",
    legacyTab: "tab-incidents",
  },
  lazy: featurePage(() => import("./ui/IncidentsPage")),
});
