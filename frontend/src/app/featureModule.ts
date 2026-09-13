/**
 * Feature module factory (composition root helper).
 *
 * Every legacy tab becomes a `features/<name>/` folder (Clean Architecture):
 *   model.ts (VOs+invariants) -> useCases.ts (application) -> ui/Page.tsx (presentation)
 * with `index.ts` exporting its `meta` and using this helper for the route.
 *
 * WHY A HELPER: the shell passes a uniform `ShellPageProps` envelope
 * (snapshot + nowMs); feature pages declare the props they actually use.
 * The single contained cast here is the one accepted seam — pages stay
 * fully typed inside their own module, and `tsc` still verifies the
 * factory import path and every prop usage inside the page.
 */

import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { EngineSnapshot } from "@/types/domain";

/** Uniform props the AppShell passes to every feature page. */
export interface ShellPageProps {
  snapshot?: EngineSnapshot;
  nowMs?: number;
}

export type FeatureComponent = ComponentType<ShellPageProps>;

/** Lazy factory for `() => import("./ui/XPage")` with the shell contract. */
export function featurePage(
  factory: () => Promise<{ default: ComponentType<never> }>,
): LazyExoticComponent<FeatureComponent> {
  return lazy(factory as unknown as () => Promise<{ default: FeatureComponent }>);
}

export function defineFeature<T extends FeatureModule>(meta: T): T {
  return meta;
}

/** Contract every `features/<name>/index.ts` satisfies. */
export interface FeatureModule {
  meta: {
    route: string;
    label: string;
    icon: string;
    section: "OPERATIONS" | "MARKET & RESEARCH" | "SAFETY & GOVERNANCE" | "PLATFORM";
    legacyTab: string;
  };
  lazy: LazyExoticComponent<FeatureComponent>;
}
