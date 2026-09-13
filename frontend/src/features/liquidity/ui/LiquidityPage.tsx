/**
 * Liquidity — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function LiquidityPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Liquidity" subtitle="Parity build in progress — legacy tab tab-liquidity">
      <LoadingState label="Liquidity module loading…" />
    </Panel>
  );
}
