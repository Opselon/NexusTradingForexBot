/** Factory bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/factory",
    label: "Factory",
    icon: "⚒",
    section: "MARKET & RESEARCH",
    legacyTab: "tab-factory",
  },
  lazy: featurePage(() => import("./ui/FactoryPage")),
});
