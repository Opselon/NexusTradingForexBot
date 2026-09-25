/** Control Center bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/control-center",
    label: "Control Center",
    icon: "✜",
    section: "OPERATIONS",
    legacyTab: "tab-control-center",
  },
  lazy: featurePage(() => import("./ui/ControlCenterPage")),
});
