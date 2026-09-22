/** Dependency Intelligence feature — static-analysis graph console. */
import { defineFeature, featurePage } from "@/app/featureModule";

export { dependencyApi } from "./api";
export * from "./api";

export default defineFeature({
  meta: {
    route: "/dependency",
    label: "Dependency",
    icon: "⌬",
    section: "PLATFORM",
    legacyTab: "tab-dependency",
    keywords:
      "dependency graph circular import cycle architecture violation di registration hotspot centrality impact blast radius static analysis",
  },
  lazy: featurePage(() => import("./ui/DependencyPage")),
});
