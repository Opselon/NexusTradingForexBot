/** Debug bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/debug",
    label: "Debug",
    icon: "⌕",
    section: "PLATFORM",
    legacyTab: "tab-debug",
  },
  lazy: featurePage(() => import("./ui/DebugPage")),
});
