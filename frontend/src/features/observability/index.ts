/** Observability (Log Center) — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/observability",
    label: "Log Center",
    icon: "\u2261",
    section: "OPERATIONS",
    legacyTab: "tab-logs",
  },
  lazy: featurePage(() => import("./ui/ObservabilityPage")),
});
