/**
 * Control Center — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function ControlCenterPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Control Center" subtitle="Parity build in progress — legacy tab tab-control-center">
      <LoadingState label="Control Center module loading…" />
    </Panel>
  );
}
