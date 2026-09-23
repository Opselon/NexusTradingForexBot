/**
 * Packs grid + install command (POST /packs/{id}/install behind a confirm).
 *
 * The install count input is validated to the backend rule (integer 1..500)
 * BEFORE sending, and the result is whatever the 201 payload says — the grid
 * then refetches rather than marking the pack installed optimistically.
 * Model validation returns a CODE; the human message is t()d at render so it
 * follows the active language (never baked into module state).
 */

import { useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { ConfirmModal, EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useInstallPack, useMktPacks } from "../hooks";
import { validateInstallCount } from "../model";
import { asErrorText } from "./shared";
import "./marketplace.css";

type PackNote = { ok: true; pack: string; payload: string } | { ok: false; err: unknown };

export function PacksSection() {
  const t = useI18n((s) => s.t);
  const packs = useMktPacks();
  const install = useInstallPack();
  const [target, setTarget] = useState<string | null>(null);
  const [countRaw, setCountRaw] = useState("25");
  const [note, setNote] = useState<PackNote | null>(null);

  const validation = validateInstallCount(countRaw);
  const list = packs.data?.packs ?? [];
  const installedCount = list.filter((p) => p.installed).length;

  const countErrorText =
    validation.error === null
      ? null
      : validation.error === "required"
        ? t("marketplace.install.count_required", "count is required (1–500)")
        : validation.error === "not_whole"
          ? t("marketplace.install.count_whole", "count must be a whole number")
          : t("marketplace.install.count_range", "count must be between 1 and 500 (backend VALIDATION_ERROR)");

  const confirmInstall = (): void => {
    if (!target || validation.value === null) return;
    install.mutate(
      { packId: target, count: validation.value },
      {
        onSuccess: (res) => {
          setNote({ ok: true, pack: target, payload: JSON.stringify(res) });
          setTarget(null);
        },
        onError: (e) => setNote({ ok: false, err: e }),
      },
    );
  };

  return (
    <Panel
      title={t("marketplace.packs.title", "Seed packs ({i}/{n} installed)", { i: installedCount, n: list.length })}
      right={
        <button className="btn small ghost" onClick={() => packs.refetch()} disabled={packs.isFetching}>
          {packs.isFetching ? t("marketplace.packs.loading", "loading…") : t("common.refresh", "Reload")}
        </button>
      }
    >
      {packs.isPending ? (
        <Skeleton count={4} height={64} />
      ) : packs.isError ? (
        <ErrorState message={asErrorText(packs.error, t)} onRetry={() => packs.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState
          message={t("marketplace.packs.empty", "Pack registry is empty.")}
          hint={t("marketplace.packs.empty_hint", "The backend REGISTRY has no packs on this build.")}
        />
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
                  <span className="badge neutral">{t("marketplace.packs.not_installed", "not installed")}</span>
                )}
                <button className="btn small primary" onClick={() => setTarget(p.id)} disabled={install.isPending}>
                  {t("marketplace.packs.install", "Install")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {note && (
        <div className={`cmd-result ${note.ok ? "ok" : "fail"}`}>
          {note.ok ? "✓" : "✕"}{" "}
          {note.ok ? (
            <>
              {t("marketplace.packs.note_ok_a", "Install accepted for {p} — backend returned", { p: note.pack })}{" "}
              <span dir="ltr" className="inline-mono">
                {note.payload}
              </span>
              {t("marketplace.packs.note_ok_b", ". Catalog refreshed.")}
            </>
          ) : (
            <>
              {t("marketplace.packs.note_err", "Install refused:")}{" "}
              <span dir="ltr" className="inline-mono">
                {asErrorText(note.err, t)}
              </span>
            </>
          )}
        </div>
      )}

      {target && (
        <ConfirmModal
          title={t("marketplace.packs.confirm_title", "Install pack \"{p}\"", { p: target })}
          danger={false}
          confirmLabel={
            validation.value === null
              ? t("marketplace.install.fix_count", "Fix count")
              : t("marketplace.install.confirm", "Install {n} seeds", { n: validation.value ?? "" })
          }
          busy={install.isPending}
          onConfirm={confirmInstall}
          onCancel={() => setTarget(null)}
        >
          <div>{t("marketplace.packs.confirm_body", "Generates seed specs from the pack and stores them (idempotent per pack). The backend validates the count and may refuse.")}</div>
          <div className="mkt-actions" style={{ marginTop: 10 }}>
            <label className="small muted" htmlFor="mkt-count">
              {t("marketplace.packs.count_label", "seeds to install")}
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
              {t("marketplace.packs.default_25", "default 25")}
            </button>
          </div>
          {validation.error && countErrorText && (
            <div className="mkt-field-error" style={{ marginTop: 6 }}>
              {countErrorText}
            </div>
          )}
          <div className="tiny faint" style={{ marginTop: 8 }}>
            {t("marketplace.install.range_hint", "range enforced locally AND by the backend (VALIDATION_ERROR outside 1–500)")}
          </div>
        </ConfirmModal>
      )}
    </Panel>
  );
}
