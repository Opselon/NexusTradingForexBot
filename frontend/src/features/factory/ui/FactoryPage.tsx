/**
 * Factory — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function FactoryPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Factory" subtitle="Parity build in progress — legacy tab tab-factory">
      <LoadingState label="Factory module loading…" />
    </Panel>
  );
}
