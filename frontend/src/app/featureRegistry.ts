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

import type { FeatureComponent, FeatureModule } from "@/app/featureModule";
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
import modelStudioMeta from "@/features/model-studio";
import positionAdviserMeta from "@/features/position-adviser";
import dependencyMeta from "@/features/dependency";
import provisioningMeta from "@/features/provisioning";

export type FeatureSectionName = "OPERATIONS" | "MARKET & RESEARCH" | "SAFETY & GOVERNANCE" | "PLATFORM";

export interface FeatureMeta {
  /** Sidebar-visible metadata. */
  route: string;
  label: string;
  icon: string;
  section: FeatureSectionName;
  /** Legacy parity: which Web/index.html tab id this feature replaces. */
  legacyTab: string;
  /** i18n key for the sidebar/palette label (optional; falls back to label). */
  labelKey?: string;
  /** Search keywords for the command palette. */
  keywords?: string;
}

export interface RegisteredFeature extends FeatureMeta {
  lazy: LazyExoticComponent<FeatureComponent>;
}

/** Sidebar render order for the registry sections. */
export const FEATURE_SECTION_ORDER: FeatureSectionName[] = [
  "OPERATIONS",
  "MARKET & RESEARCH",
  "SAFETY & GOVERNANCE",
  "PLATFORM",
];

/**
 * One entry per legacy tab not yet covered by the 7 legacy React pages.
 * `toFeature()` flattens the module's `meta` (features export `{meta, lazy}`)
 * — the registry stays the only place that knows the module shape.
 */
function toFeature(mod: FeatureModule): RegisteredFeature {
  return { ...mod.meta, lazy: mod.lazy };
}

const FEATURES: RegisteredFeature[] = [
  newsMeta,
  aiAnalysisMeta,
  modelStudioMeta,
  researchMeta,
  marketplaceMeta,
  factoryMeta,
  accountMeta,
  healthMeta,
  rulesMeta,
  configMeta,
  debugMeta,
  governanceMeta,
  liquidityMeta,
  incidentsMeta,
  commandCenterMeta,
  controlCenterMeta,
  databaseMeta,
  // Registered 2026-09-22 (pro-UIUX wave): three complete defineFeature
  // modules were built but never wired, leaving their screens unreachable.
  positionAdviserMeta,
  dependencyMeta,
  provisioningMeta,
].map(toFeature);

export const FEATURE_REGISTRY: RegisteredFeature[] = FEATURES;

/** Grouped by section, in FEATURE_SECTION_ORDER (not first-seen order). */
export const FEATURE_SECTIONS: Array<{ section: FeatureSectionName; items: RegisteredFeature[] }> =
  FEATURE_SECTION_ORDER.map((section) => ({
    section,
    items: FEATURES.filter((f) => f.section === section),
  })).filter((bucket) => bucket.items.length > 0);

/** i18n key for a feature's nav label (lanes can translate per feature). */
export function featureLabelKey(route: string): string {
  return `nav.feature.${route.replace(/^\//, "") || "home"}`;
}

/** Lookup by route (palette + deep-link validation). */
export function findFeature(route: string): RegisteredFeature | undefined {
  return FEATURES.find((f) => f.route === route);
}
