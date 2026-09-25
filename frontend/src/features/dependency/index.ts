/** Dependency Intelligence feature — static-analysis graph console. */
import { defineFeature, featurePage } from "@/app/featureModule";

export { dependencyApi } from "./api";
export * from "./api";

export default defineFeature({
  meta: {
    // CONTRACT #3 rename: root `/dependency` (+ /dependency.html) stays the
    // legacy dependency dashboard on the backend; the React page moves to
    // `/dependencies` and old deep links land via the AppShell <Navigate> alias.
    route: "/dependencies",
    label: "Dependency",
    icon: "⌬",
    section: "PLATFORM",
    legacyTab: "tab-dependency",
    keywords:
      "dependency graph circular import cycle architecture violation di registration hotspot centrality impact blast radius static analysis",
  },
  lazy: featurePage(() => import("./ui/DependencyPage")),
});
