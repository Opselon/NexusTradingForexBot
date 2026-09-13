/** Accounting bounded context — public surface (UI_WAVE_SPEC layering). */
import { defineFeature, featurePage } from "@/app/featureModule";

export default defineFeature({
  meta: {
    route: "/account",
    label: "Accounting",
    icon: "▦",
    section: "OPERATIONS",
    legacyTab: "tab-account",
  },
  lazy: featurePage(() => import("./ui/AccountPage")),
});
