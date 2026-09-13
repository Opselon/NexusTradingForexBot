/** Settings bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/config",
    label: "Settings",
    icon: "⚙",
    section: "PLATFORM",
    legacyTab: "tab-config",
  },
  lazy: featurePage(() => import("./ui/ConfigPage")),
});
