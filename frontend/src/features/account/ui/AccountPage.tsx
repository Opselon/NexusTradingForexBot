/**
 * Accounting — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function AccountPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="Accounting" subtitle="Parity build in progress — legacy tab tab-account">
      <LoadingState label="Accounting module loading…" />
    </Panel>
  );
}
