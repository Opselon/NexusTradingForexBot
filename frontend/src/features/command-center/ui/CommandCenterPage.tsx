/**
 * Command Center — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function CommandCenterPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Command Center" subtitle="Parity build in progress — legacy tab tab-command-center">
      <LoadingState label="Command Center module loading…" />
    </Panel>
  );
}
