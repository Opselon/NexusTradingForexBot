/**
 * Provisioning feature — first-run model preparation console.
 *
 * Legacy parity: replaces Web/first_setup.html (served at /first_setup.html).
 * Covers the same five backend endpoints of provisioning_routes.py:
 * status / environment / environment/install / official / train start+progress
 * + cancel.
 */
import { defineFeature, featurePage } from "@/app/featureModule";

export { provisioningApi } from "./api";
export * from "./api";
export * from "./model";

export default defineFeature({
  meta: {
    route: "/provisioning",
    label: "Model Setup",
    icon: "✦",
    section: "PLATFORM",
    legacyTab: "first_setup.html",
    keywords:
      "first setup provisioning model preparation environment pytorch cuda training run folds epochs official model install dataset import broker history",
  },
  lazy: featurePage(() => import("./ui/ProvisioningPage")),
});
