/**
 * Governance — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function GovernancePage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Governance" subtitle="Parity build in progress — legacy tab tab-governance">
      <LoadingState label="Governance module loading…" />
    </Panel>
  );
}
