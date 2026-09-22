/**
 * Position Decision Adviser feature — Layer-2 hold-score advisory console.
 *
 * Legacy parity note: this tab does NOT exist in Web/index.html. It is the
 * first feature that is new-web-only: the backend routes under
 * /api/position-adviser are consumed exclusively by this console. legacyTab
 * is left empty for that reason (registry parity mapping only applies to
 * features that replace a legacy tab).
 */
import { defineFeature, featurePage } from "@/app/featureModule";

export { positionAdviserApi } from "./api";
export * from "./api";
export * from "./model";

export default defineFeature({
  meta: {
    route: "/position-adviser",
    label: "Position Adviser",
    icon: "⚖",
    section: "SAFETY & GOVERNANCE",
    legacyTab: "",
    keywords:
      "position adviser hold score advisory layer-2 activation ladder paper live unload checkpoint scalers train",
  },
  lazy: featurePage(() => import("./ui/PositionAdviserPage")),
});
