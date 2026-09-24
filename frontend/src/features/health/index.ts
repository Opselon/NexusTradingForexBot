/** Health bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    // CONTRACT #3 rename: root `/health` is the real backend health endpoint
    // (diagnostics_state_routes.py) and must keep winning at `/`. Old deep
    // links land on the new route via the <Navigate> alias in AppShell.
    route: "/system-health",
    label: "Health",
    icon: "♥",
    section: "SAFETY & GOVERNANCE",
    legacyTab: "tab-health",
  },
  lazy: featurePage(() => import("./ui/HealthPage")),
});
