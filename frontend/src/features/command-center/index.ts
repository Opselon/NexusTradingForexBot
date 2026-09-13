/** Command Center bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/command-center",
    label: "Command Center",
    icon: "◎",
    section: "OPERATIONS",
    legacyTab: "tab-command-center",
  },
  lazy: featurePage(() => import("./ui/CommandCenterPage")),
});
