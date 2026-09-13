/**
 * Database — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function DatabasePage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Database" subtitle="Parity build in progress — legacy tab tab-database">
      <LoadingState label="Database module loading…" />
    </Panel>
  );
}
