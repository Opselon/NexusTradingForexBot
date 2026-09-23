/**
 * PURPOSE:  Pack storefront grid — cover cards, install count meter and
 *           install command (POST /packs/{id}/install behind a confirm).
 * OWNER:    uiux-w6-marketplace
 * CONSUMES: ../hooks (useMktPacks, useInstallPack), ../model
 *           (validateInstallCount), ./storeViewModel (packMonogram),
 *           ./shared (asErrorText), ./marketplace-store.css
 * PROVIDES: PacksSection
 * INVARIANTS: install count validated to the backend rule (1..500) before
 *           sending; the 201 payload is echoed verbatim; the grid refetches
 *           instead of marking installed optimistically; Skeleton/ErrorState/
 *           EmptyState keep their existing strings; every control (Reload,
 *           Install, count input) keeps working.
 * EXTEND:   add a pack facet by reading MktPack fields only — never invent.
 */

import { useState } from "react";
import { ConfirmModal, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useInstallPack, useMktPacks } from "../hooks";
import { validateInstallCount } from "../model";
import { packMonogram } from "./storeViewModel";
import { asErrorText } from "./shared";
import "./marketplace.css";
import "./marketplace-store.css";
import "./marketplace-store-detail.css";

export function PacksSection() {
  const packs = useMktPacks();
  const install = useInstallPack();
  const [target, setTarget] = useState<string | null>(null);
  const [countRaw, setCountRaw] = useState("25");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const validation = validateInstallCount(countRaw);
  const list = packs.data?.packs ?? [];
  const installedCount = list.filter((p) => p.installed).length;
  // Derived scale for the count meter: page max seed_count (0-safe).
  const maxSeeds = list.reduce((m, p) => Math.max(m, p.seed_count || 0), 0);

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
      right={<button className="btn small ghost" onClick={() => packs.refetch()} disabled={packs.isFetching}>{packs.isFetching ? "loading…" : "Reload"}</button>}
    >
      {packs.isPending ? (
        <Skeleton count={4} height={64} />
      ) : packs.isError ? (
        <ErrorState message={asErrorText(packs.error)} onRetry={() => packs.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState message="Pack registry is empty." hint="The backend REGISTRY has no packs on this build." />
      ) : (
        <div className="mkt-store-packs">
          {list.map((p) => {
            const pct = maxSeeds > 0 ? Math.min(100, ((p.seed_count || 0) / maxSeeds) * 100) : 0;
            return (
              <div className={`mkt-store-pack ${p.installed ? "is-installed" : ""}`} key={p.id}>
                <div className="mkt-store-cover" aria-hidden="true">
                  <span className="monogram">{packMonogram(p)}</span>
                </div>
                <div className="mkt-store-pack-body">
                  <div>
                    <div className="name">{p.name}</div>
                    <div className="tiny faint inline-mono">{p.id} · {p.family}</div>
                  </div>
                  <div className="desc">{p.description || "—"}</div>
                  <div className="mkt-store-meter">
                    <span className="lab">
                      <span>seed_count</span>
                      <span className="val">{p.seed_count}</span>
                    </span>
                    <span
                      className="mkt-store-track"
                      role="img"
                      aria-label={`seed_count ${p.seed_count}, bar scaled to page max ${maxSeeds} (derived)`}
                      title={`raw seed_count ${p.seed_count} · bar scaled to page max ${maxSeeds} (derived)`}
                    >
                      <i style={{ width: `${pct}%` }} />
                    </span>
                  </div>
                  <div className="mkt-store-badge-rail">
                    <span className="mkt-family-tag">
                      <span className="swatch" aria-hidden="true" />
                      {p.family}
                    </span>
                    {p.installed ? (
                      <span className="mkt-store-installed-flag"><i aria-hidden="true" />installed</span>
                    ) : (
                      <span className="badge neutral">not installed</span>
                    )}
                    <button className="btn small primary" onClick={() => setTarget(p.id)} disabled={install.isPending}>
                      {install.isPending ? "installing…" : "Install"}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
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
          <div className="mkt-actions" style={{ marginTop: 10 }}>
            <label className="small muted" htmlFor="mkt-count">
              seeds to install
            </label>
            <input
              id="mkt-count"
              className={`input ${validation.error ? "invalid" : ""}`}
              style={{ width: 90 }}
              value={countRaw}
              onChange={(e) => setCountRaw(e.target.value)}
              inputMode="numeric"
              aria-invalid={!!validation.error}
            />
            <button className="btn small ghost" onClick={() => setCountRaw("25")}>
              default 25
            </button>
          </div>
          {validation.error && <div className="mkt-field-error" style={{ marginTop: 6 }}>{validation.error}</div>}
          <div className="tiny faint" style={{ marginTop: 8 }}>range enforced locally AND by the backend (VALIDATION_ERROR outside 1–500)</div>
        </ConfirmModal>
      )}
    </Panel>
  );
}
