/** Health bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/health",
    label: "Health",
    icon: "♥",
    section: "SAFETY & GOVERNANCE",
    legacyTab: "tab-health",
  },
  lazy: featurePage(() => import("./ui/HealthPage")),
});
