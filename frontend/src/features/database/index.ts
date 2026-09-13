/** Database bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/database",
    label: "Database",
    icon: "◲",
    section: "PLATFORM",
    legacyTab: "tab-database",
  },
  lazy: featurePage(() => import("./ui/DatabasePage")),
});
