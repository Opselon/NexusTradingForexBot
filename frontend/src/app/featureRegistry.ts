/**
 * Feature registry — the single composition point for lazy-loaded feature
 * pages (Clean Architecture: `app/` is the composition root; features declare
 * themselves via `index.ts` meta, the shell only consumes the registry).
 *
 * Adding a feature = drop a folder under `src/features/` with an `index.ts`
 * exporting `meta` + `lazy`, then register it here (one line). AppShell reads
 * NAV from this file; routes render under a shared Suspense boundary.
 *
 * Ordering here == sidebar order == Alt+<n> order (after the 7 legacy pages).
 */

import type { FeatureComponent } from "@/app/featureModule";
import type { LazyExoticComponent } from "react";
import researchMeta from "@/features/research";
import aiAnalysisMeta from "@/features/ai-analysis";
import marketplaceMeta from "@/features/marketplace";
import newsMeta from "@/features/news";
import rulesMeta from "@/features/rules";
import configMeta from "@/features/config";
import debugMeta from "@/features/debug";
import healthMeta from "@/features/health";
import accountMeta from "@/features/account";
import databaseMeta from "@/features/database";
import governanceMeta from "@/features/governance";
import liquidityMeta from "@/features/liquidity";
import incidentsMeta from "@/features/incidents";
import commandCenterMeta from "@/features/command-center";
import controlCenterMeta from "@/features/control-center";
import factoryMeta from "@/features/factory";

export interface FeatureMeta {
  /** Sidebar-visible metadata. */
  route: string;
  label: string;
  icon: string;
  section: "OPERATIONS" | "MARKET & RESEARCH" | "SAFETY & GOVERNANCE" | "PLATFORM";
  /** Legacy parity: which Web/index.html tab id this feature replaces. */
  legacyTab: string;
}

export interface RegisteredFeature extends FeatureMeta {
  lazy: LazyExoticComponent<FeatureComponent>;
}

/** One entry per legacy tab not yet covered by the 7 legacy React pages. */
const FEATURES: RegisteredFeature[] = [
  { ...newsMeta, lazy: newsMeta.lazy },
  { ...aiAnalysisMeta, lazy: aiAnalysisMeta.lazy },
  { ...researchMeta, lazy: researchMeta.lazy },
  { ...marketplaceMeta, lazy: marketplaceMeta.lazy },
  { ...factoryMeta, lazy: factoryMeta.lazy },
  { ...accountMeta, lazy: accountMeta.lazy },
  { ...healthMeta, lazy: healthMeta.lazy },
  { ...rulesMeta, lazy: rulesMeta.lazy },
  { ...configMeta, lazy: configMeta.lazy },
  { ...debugMeta, lazy: debugMeta.lazy },
  { ...governanceMeta, lazy: governanceMeta.lazy },
  { ...liquidityMeta, lazy: liquidityMeta.lazy },
  { ...incidentsMeta, lazy: incidentsMeta.lazy },
  { ...commandCenterMeta, lazy: commandCenterMeta.lazy },
  { ...controlCenterMeta, lazy: controlCenterMeta.lazy },
  { ...databaseMeta, lazy: databaseMeta.lazy },
];

export const FEATURE_REGISTRY = FEATURES;

/** Sidebar sections in render order (legacy pages prepended by AppShell). */
export const FEATURE_SECTIONS: Array<{ section: FeatureMeta["section"]; items: RegisteredFeature[] }> = FEATURES.reduce(
  (acc, f) => {
    let bucket = acc.find((b) => b.section === f.section);
    if (!bucket) {
      bucket = { section: f.section, items: [] };
      acc.push(bucket);
    }
    bucket.items.push(f);
    return acc;
  },
  [] as Array<{ section: FeatureMeta["section"]; items: RegisteredFeature[] }>,
);
