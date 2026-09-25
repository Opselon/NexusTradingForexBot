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

/** Shared money-path escalation gate (Lane P): any feature that transitions
 *  the engine into LIVE must route through this typed confirm, never a plain
 *  click. Exported here so control-center (Lane X) can adopt it without
 *  reaching into another lane's internals. */
export { TypedConfirmModal, modeChangeSpec } from "./ui/TypedConfirmModal";
