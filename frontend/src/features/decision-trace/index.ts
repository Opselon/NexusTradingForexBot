/**
 * Decision Trace feature — runtime observability window.
 *
 * Registered 2026-09-23 (live decision-trace wave): exposes the observer
 * surface (src/nexus_scalp/web/trace_routes.py) as a full screen. The graph
 * is derived from real runtime events only — no hardcoded topology.
 */
import { defineFeature, featurePage } from "@/app/featureModule";

export * from "./api";
export * from "./types";

export default defineFeature({
  meta: {
    route: "/decision-trace",
    label: "Decision Trace",
    icon: "⌁",
    section: "OPERATIONS",
    legacyTab: "tab-decision-trace",
    keywords:
      "decision trace execution forensics topology observer latency model provenance regime gate rule risk mt5 gateway causal chain replay",
  },
  lazy: featurePage(() => import("./ui/DecisionTracePage")),
});
