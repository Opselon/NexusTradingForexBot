/**
 * Marketplace — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function MarketplacePage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Marketplace" subtitle="Parity build in progress — legacy tab tab-marketplace">
      <LoadingState label="Marketplace module loading…" />
    </Panel>
  );
}
