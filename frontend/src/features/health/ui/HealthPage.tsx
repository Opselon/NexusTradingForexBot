/**
 * Health — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function HealthPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Health" subtitle="Parity build in progress — legacy tab tab-health">
      <LoadingState label="Health module loading…" />
    </Panel>
  );
}
