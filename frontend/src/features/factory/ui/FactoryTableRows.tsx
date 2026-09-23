/**
 * PURPOSE:  Memoized leaf row components for the Factory page tables so the
 *           20s status poll and filter keystrokes stop re-rendering up to
 *           100 rows per tab.
 * OWNER:    LANE-5 (perf wave) — features/factory/** only.
 * CONSUMES: ../model (Row/GenerationVo/str/num), ../useCases (factoryUseCases),
 *           ../../research/ui/lane5Kit (StatusPill — read-only import).
 * PROVIDES: AskFn type + GenerationRow, CandidateRow, BenchmarkRow, FailureRow,
 *           RankingRow, EventRow (React.memo leaf components).
 * INVARANTS:Every cell renders a backend value verbatim; no row invents a
 *           value and no row fetches — commands still travel through the
 *           page's confirm flow (onAsk), never fire directly.
 * EXTEND:   Add new factory table rows here; keep props primitives/stable so
 *           memo stays effective.
 */

import { memo } from "react";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { FactoryCommandDto, GenerationVo, Row } from "../model";
import { num, str } from "../model";
import { factoryUseCases } from "../useCases";
import { StatusPill } from "../../research/ui/lane5Kit";

/** Confirm-flow dispatcher handed down from the page (stable via useCallback). */
export type AskFn = (label: string, danger: boolean, run: () => Promise<FactoryCommandDto>) => void;

/** One generation row with its guarded "complete" command. */
export const GenerationRow = memo(function GenerationRow({
  g,
  busy,
  onAsk,
}: {
  g: GenerationVo;
  busy: boolean;
  onAsk: AskFn;
}) {
  return (
    <tr>
      <td className="inline-mono tiny" title={g.id}>
        {g.number !== null ? `#${g.number} ` : ""}
        {g.id.slice(0, 14)}
      </td>
      <td className="tiny">{g.mode}</td>
      <td>
        <StatusPill status={g.state} />
      </td>
      <td className="num tiny">{g.size ?? "—"}</td>
      <td className="tiny">{formatDateTime(g.createdAt)}</td>
      <td>
        <button
          className="btn small ghost"
          disabled={busy || g.state === "COMPLETED"}
          onClick={() => onAsk(`Complete ${g.id.slice(0, 10)}`, false, () => factoryUseCases.complete(g.id))}
        >
          complete
        </button>
      </td>
    </tr>
  );
});

/** One candidate row with its guarded "evaluate" command. */
export const CandidateRow = memo(function CandidateRow({
  c,
  busy,
  onAsk,
}: {
  c: Row;
  busy: boolean;
  onAsk: AskFn;
}) {
  const cid = str(c.candidate_id) ?? str(c.id) ?? "";
  return (
    <tr>
      <td className="inline-mono tiny">{cid.slice(0, 16) || "—"}</td>
      <td className="inline-mono tiny">{str(c.generation_id)?.slice(0, 10) ?? "—"}</td>
      <td>
        <StatusPill status={str(c.lifecycle) ?? str(c.status)} />
      </td>
      <td className="num tiny">{num(c.score) === null ? "—" : formatNumber(num(c.score)!, 3)}</td>
      <td>
        <button
          className="btn small ghost"
          disabled={busy || !cid}
          onClick={() => onAsk(`Evaluate ${cid.slice(0, 10)}`, false, () => factoryUseCases.evaluate(cid))}
        >
          evaluate
        </button>
      </td>
    </tr>
  );
});

/** One benchmark row (read-only backend metrics). */
export const BenchmarkRow = memo(function BenchmarkRow({ b }: { b: Row }) {
  return (
    <tr>
      <td className="inline-mono tiny">{str(b.candidate_id)?.slice(0, 14) ?? "—"}</td>
      <td className="num tiny">{num(b.coverage) === null ? "—" : `${formatNumber(num(b.coverage)!, 1)}%`}</td>
      <td>
        <StatusPill status={str(b.decision_label) ?? str(b.decision)} />
      </td>
      <td className="tiny">{str(b.oos_status) ?? "—"}</td>
      <td className="tiny">{str(b.robustness_status) ?? "—"}</td>
    </tr>
  );
});

/** One failure-ledger row. */
export const FailureRow = memo(function FailureRow({ f }: { f: Row }) {
  return (
    <tr>
      <td className="tiny">{formatDateTime(str(f.created_at) ?? str(f.at))}</td>
      <td className="tiny">{str(f.stage) ?? str(f.kind) ?? "—"}</td>
      <td className="tiny muted" title={str(f.reason) ?? ""}>
        {(str(f.reason) ?? "—").slice(0, 60)}
      </td>
    </tr>
  );
});

/** One ranked survivor row (rank = 1-based display position). */
export const RankingRow = memo(function RankingRow({ r, rank }: { r: Row; rank: number }) {
  return (
    <tr>
      <td className="num tiny">{rank}</td>
      <td className="inline-mono tiny">{str(r.strategy_id)?.slice(0, 16) ?? "—"}</td>
      <td>
        <StatusPill status={str(r.lifecycle)} />
      </td>
      <td className="num tiny">{formatNumber(num(r.score) ?? num(r.value) ?? NaN, 3)}</td>
    </tr>
  );
});

/** One event-console row. */
export const EventRow = memo(function EventRow({ e }: { e: Row }) {
  return (
    <tr>
      <td className="tiny">{formatDateTime(str(e.created_at) ?? str(e.at))}</td>
      <td className="small">{str(e.event_type) ?? str(e.kind) ?? "—"}</td>
      <td className="tiny muted" title={str(e.payload) ?? str(e.detail) ?? ""}>
        {(str(e.detail) ?? str(e.message) ?? "—").slice(0, 80)}
      </td>
    </tr>
  );
});
