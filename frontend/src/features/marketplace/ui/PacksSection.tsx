/**
 * Packs grid + install command (POST /packs/{id}/install behind a confirm).
 *
 * The install count input is validated to the backend rule (integer 1..500)
 * BEFORE sending, and the result is whatever the 201 payload says — the grid
 * then refetches rather than marking the pack installed optimistically.
 */

import { memo, useCallback, useMemo, useState } from "react";
import { ConfirmModal, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useInstallPack, useMktPacks } from "../hooks";
import { validateInstallCount } from "../model";
import type { MktPack } from "../types";
import { FreshnessNote, asErrorText, requestIdOf } from "./shared";
import "./marketplace.css";

/** Constant style objects hoisted out of the render (no per-render allocs). */
const COUNT_INPUT_STYLE = { width: 90 } as const;
const COUNT_ROW_STYLE = { marginTop: 10 } as const;
const FIELD_ERR_STYLE = { marginTop: 6 } as const;
const RANGE_NOTE_STYLE = { marginTop: 8 } as const;

/** One pack card. Memoized so keystrokes in the install-count dialog and the
 *  note banner do not re-render the whole grid. */
const PackCard = memo(function PackCard({
  pack,
  busy,
  onInstall,
}: {
  pack: MktPack;
  busy: boolean;
  onInstall: (packId: string) => void;
}) {
  return (
    <div className={`mkt-pack ${pack.installed ? "is-installed" : ""}`} key={pack.id}>
      <div className="name">{pack.name}</div>
      <div className="desc">{pack.description || "—"}</div>
      <div className="foot">
        <span className="mkt-family-tag">
          <span className="swatch" aria-hidden="true" />
          {pack.family}
        </span>
        {pack.installed ? (
          <span className="badge good">INSTALLED · {pack.seed_count}</span>
        ) : (
          <span className="badge neutral">not installed</span>
        )}
        <button className="btn small primary" onClick={() => onInstall(pack.id)} disabled={busy}>
          Install
        </button>
      </div>
    </div>
  );
});

export function PacksSection() {
  const packs = useMktPacks();
  const install = useInstallPack();
  const [target, setTarget] = useState<string | null>(null);
  const [countRaw, setCountRaw] = useState("25");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const validation = validateInstallCount(countRaw);
  const list = packs.data?.packs ?? [];
  // Derived once per data change, not once per keystroke/render.
  const installedCount = useMemo(() => list.filter((p) => p.installed).length, [list]);
  const openInstall = useCallback((packId: string) => setTarget(packId), []);

  const confirmInstall = (): void => {
    if (!target || validation.value === null) return;
    install.mutate(
      { packId: target, count: validation.value },
      {
        onSuccess: (res) => {
          setNote({ ok: true, text: `Install accepted for ${target} — backend returned ${JSON.stringify(res)}. Catalog refreshed.` });
          setTarget(null);
        },
        onError: (e) => setNote({ ok: false, text: `Install refused: ${asErrorText(e)}` }),
      },
    );
  };

  return (
    <Panel
      title={`Seed packs (${installedCount}/${list.length} installed)`}
      right={
        <>
          <button className="btn small ghost" onClick={() => packs.refetch()} disabled={packs.isFetching}>{packs.isFetching ? "loading…" : "Reload"}</button>
          <FreshnessNote updatedAtMs={packs.dataUpdatedAt ?? null} label="packs" />
        </>
      }
    >
      {packs.isPending ? (
        <Skeleton count={4} height={64} />
      ) : packs.isError ? (
        <ErrorState
          message={asErrorText(packs.error)}
          requestId={requestIdOf(packs.error)}
          onRetry={() => packs.refetch()}
        />
      ) : list.length === 0 ? (
        <EmptyState
          message="Pack registry is empty."
          hint="GET /api/v1/marketplace/packs returned no packs on this build."
        />
      ) : (
        <div className="mkt-packs">
          {list.map((p) => (
            <PackCard key={p.id} pack={p} busy={install.isPending} onInstall={openInstall} />
          ))}
        </div>
      )}
      {note && <div className={`cmd-result ${note.ok ? "ok" : "fail"}`}>{note.ok ? "✓" : "✕"} {note.text}</div>}

      {target && (
        <ConfirmModal
          title={`Install pack "${target}"`}
          danger={false}
          confirmLabel={validation.value === null ? "Fix count" : `Install ${validation.value ?? ""} seeds`}
          busy={install.isPending}
          onConfirm={confirmInstall}
          onCancel={() => setTarget(null)}
        >
          <div>
            Generates seed specs from the pack and stores them (idempotent per pack). The backend validates the count and may refuse.
          </div>
          <div className="mkt-actions" style={COUNT_ROW_STYLE}>
            <label className="small muted" htmlFor="mkt-count">
              seeds to install
            </label>
            <input
              id="mkt-count"
              className={`input ${validation.error ? "invalid" : ""}`}
              style={COUNT_INPUT_STYLE}
              value={countRaw}
              onChange={(e) => setCountRaw(e.target.value)}
              inputMode="numeric"
              aria-invalid={!!validation.error}
            />
            <button className="btn small ghost" onClick={() => setCountRaw("25")}>
              default 25
            </button>
          </div>
          {validation.error && <div className="mkt-field-error" style={FIELD_ERR_STYLE}>{validation.error}</div>}
          <div className="tiny faint" style={RANGE_NOTE_STYLE}>range enforced locally AND by the backend (VALIDATION_ERROR outside 1–500)</div>
        </ConfirmModal>
      )}
    </Panel>
  );
}
