/**
 * AI Analysis — feature page (skeleton; the owning lane builds it out).
 * Presentation-only: data comes from ../useCases via TanStack Query hooks.
 */

import { Panel, LoadingState } from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";

export default function AiAnalysisPage(props: ShellPageProps) {
  void props;
  return (
    <Panel title="AI Analysis" subtitle="Parity build in progress — legacy tab tab-ai-analysis">
      <LoadingState label="AI Analysis module loading…" />
    </Panel>
  );
}
