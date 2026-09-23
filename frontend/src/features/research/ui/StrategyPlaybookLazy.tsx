/**
 * StrategyPlaybookLazy — React.lazy boundary for the strategy handbook.
 *
 * PURPOSE: fetch the playbook/handbook module (StrategyPlaybook.tsx + the
 * prose corpus under ../handbook/content.ts) only when an operator actually
 * opens the Playbook tab or the drawer's Playbook section, instead of shipping
 * it inside the ResearchPage route chunk (wave-7 load work, contract 2.4).
 *
 * OWNER: lane D (features/research) — future edits here and to the playbook
 * split belong to lane D; vite.config.ts remains LEAD-owned.
 *
 * CONSUMES: ./StrategyPlaybook (default export, dynamic import below).
 *
 * PROVIDES: default export with StrategyPlaybook's exact props
 * ({ focusId?, compact? }) — call sites keep their markup unchanged.
 *
 * INVARIANTS:
 *  - once the chunk resolves, the rendered playbook is byte-identical to the
 *    eager version (same component, same props, no wrapper DOM: Suspense and
 *    the outer div both render children directly);
 *  - the fallback mirrors this page's other pending states (Skeleton), so the
 *    first open shows a transient loading skeleton only while the chunk
 *    downloads — React caches the resolved module, later opens are instant;
 *  - memo() with stable primitives props ({focusId, compact}) keeps parent
 *    re-renders (query ticks) from re-walking the Suspense boundary;
 *  - never add a prefetch here: it would pull the prose back into first-load
 *    network cost and defeat the split.
 *
 * EXTEND: only if the playbook gains props — keep them primitive/stable or
 * the memo no longer holds; deeper splitting belongs in ../handbook/content.
 */
import { Suspense, lazy, memo } from "react";
import { Skeleton } from "@/components/primitives";

const StrategyPlaybook = lazy(() => import("./StrategyPlaybook"));

type Props = {
  /** Entry id to expand + scroll to on mount (cross-links, drawer deep-links). */
  focusId?: string;
  /** Hide the TOC sidebar (used inside the strategy drawer). */
  compact?: boolean;
};

function StrategyPlaybookLazy({ focusId, compact }: Props) {
  return (
    <Suspense fallback={<Skeleton count={3} />}>
      <StrategyPlaybook focusId={focusId} compact={compact} />
    </Suspense>
  );
}

export default memo(StrategyPlaybookLazy);
