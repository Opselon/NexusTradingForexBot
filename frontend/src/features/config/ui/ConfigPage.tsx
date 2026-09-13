/**
 * Settings — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function ConfigPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Settings" subtitle="Parity build in progress — legacy tab tab-config">
      <LoadingState label="Settings module loading…" />
    </Panel>
  );
}
