/**
 * Packs grid + install command (POST /packs/{id}/install behind a confirm).
 *
 * The install count input is validated to the backend rule (integer 1..500)
 * BEFORE sending, and the result is whatever the 201 payload says — the grid
 * then refetches rather than marking the pack installed optimistically.
 */

import { useState } from "react";
import { ConfirmModal, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useInstallPack, useMktPacks } from "../hooks";
import { validateInstallCount } from "../model";
import { asErrorText } from "./shared";
import "./marketplace.css";

export function PacksSection() {
  const packs = useMktPacks();
  const install = useInstallPack();
  const [target, setTarget] = useState<string | null>(null);
  const [countRaw, setCountRaw] = useState("25");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const validation = validateInstallCount(countRaw);
  const list = packs.data?.packs ?? [];
  const installedCount = list.filter((p) => p.installed).length;

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
        <div className="mkt-packs">
          {list.map((p) => (
            <div className={`mkt-pack ${p.installed ? "is-installed" : ""}`} key={p.id}>
              <div className="name">{p.name}</div>
              <div className="desc">{p.description || "—"}</div>
              <div className="foot">
                <span className="mkt-family-tag">
                  <span className="swatch" aria-hidden="true" />
                  {p.family}
                </span>
                {p.installed ? (
                  <span className="badge good">INSTALLED · {p.seed_count}</span>
                ) : (
                  <span className="badge neutral">not installed</span>
                )}
                <button className="btn small primary" onClick={() => setTarget(p.id)} disabled={install.isPending}>
                  Install
                </button>
              </div>
            </div>
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
