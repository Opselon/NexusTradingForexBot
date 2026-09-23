/**
 * One-line connection-string entry: a postgresql:// URL parsed CLIENT-side
 * into the same discrete fields the discrete form edits (contract §0.1/§3.1).
 *
 * OWNER: lane B (db-provider-pro) — this lane owns future edits.
 * CONSUMES: `connectionUrl.ts` (the pure client mirror of Lane A's server
 *   helpers — mirrored BY CONTRACT §2.1, never imported across lanes), the
 *   parent's `values`/`set` so the URL field and the discrete form stay in
 *   sync (edit either, the other follows — a valid URL applies to the fields
 *   as it is typed; a discrete edit rewrites the URL).
 * PROVIDES: `ConnectionUrlField` — the URL input + its parse verdict line.
 * INVARIANTS:
 *   - a MALFORMED URL is refused client-side WITH the parse reason and its
 *     fields are never applied, so the bad string never reaches the wire
 *     (nothing in this component talks to the network at all);
 *   - the operator's text is echoed back masked (`user:***@host`) and a
 *     URL-carried password lands in the form's password field only (blank
 *     there still means "keep the stored secret");
 *   - an empty field is inert: it never clobbers the discrete form;
 *   - no `any`, no `@ts-expect-error`.
 * EXTEND: a new URL-carried field — teach `connectionUrl.ts` first, then
 *   apply it here.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { FieldValues } from "@/features/config/validation";
import { buildPgUrl, isUrlParseFailure, maskUrl, parsePgUrl, type ParsedPgConfig } from "./connectionUrl";

/** Discrete fields a URL populates (all live in the parent's form). */
const URL_KEYS = ["host", "port", "database", "username", "ssl_mode"] as const;

/**
 * The URL the current discrete fields represent — display only.
 * `buildPgUrl` never writes the password, so this string is safe to echo.
 * Empty while host/database are not filled yet (never a half-built URL).
 */
export function urlFromValues(values: FieldValues): string {
  const host = String(values.host ?? "");
  const database = String(values.database ?? "");
  if (!host || !database) return "";
  const cfg: ParsedPgConfig = {
    host,
    port: Number(values.port) > 0 ? Number(values.port) : 5432,
    database,
    username: String(values.username ?? ""),
    ssl_mode: String(values.ssl_mode ?? ""),
  };
  return buildPgUrl(cfg);
}

export function ConnectionUrlField({
  values,
  set,
  onPassword,
}: {
  /** The parent's discrete form values (the shared source of truth). */
  values: FieldValues;
  /** Apply parsed URL fields onto the parent's form. */
  set: (k: string, v: string | boolean) => void;
  /** Route a URL-carried password into the form's password field. */
  onPassword: (v: string) => void;
}) {
  /** The operator's in-progress text; null = mirror the discrete fields. */
  const [typed, setTyped] = useState<string | null>(null);
  const [focused, setFocused] = useState(false);

  const derived = useMemo(() => urlFromValues(values), [values]);

  /* A discrete edit rewrites the URL (the other side follows): any change to
   * the derived URL discards un-applied typed text. */
  const prevDerived = useRef(derived);
  useEffect(() => {
    if (prevDerived.current !== derived) {
      prevDerived.current = derived;
      setTyped(null);
    }
  }, [derived]);

  const shown = typed ?? derived;

  const onChange = (raw: string) => {
    if (raw.length === 0) {
      setTyped("");
      return;
    }
    const parsed = parsePgUrl(raw);
    if (isUrlParseFailure(parsed)) {
      // Refused here with the reason; the typed text stays local and the
      // discrete fields are untouched — nothing is sent anywhere.
      setTyped(raw);
      return;
    }
    for (const k of URL_KEYS) {
      set(k, String(parsed[k]));
    }
    if (typeof parsed.password === "string" && parsed.password.length > 0) {
      onPassword(parsed.password);
    }
    // Snap the input to the canonical URL of the fields just applied.
    setTyped(null);
  };

  const attempted = typed !== null && typed.trim().length > 0;
  const verdict = attempted ? parsePgUrl(typed) : null;
  const refused = verdict !== null && isUrlParseFailure(verdict) ? verdict : null;
  /* While the text has not seen a scheme separator it is incomplete, not
   * malformed — stay neutral until it actually looks like a URL. */
  const malformed = refused !== null && typed !== null && typed.includes("://") ? refused : null;

  return (
    <div className="dbcp-url">
      <div className="dbcp-url-head">
        <span className="dbcp-section-title" style={{ margin: 0 }}>
          connection string
        </span>
        <span className="dbcp-url-mode">parsed client-side · never sent whole</span>
        {attempted && (
          <button className="btn small ghost" onClick={() => setTyped(null)}>
            reset to fields
          </button>
        )}
      </div>
      <input
        className="dbcp-url-input"
        type="text"
        value={shown}
        spellCheck={false}
        autoComplete="off"
        aria-label="PostgreSQL connection URL"
        aria-invalid={malformed ? true : undefined}
        placeholder="postgresql://user@host:5432/database?sslmode=require"
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onChange={(e) => onChange(e.target.value)}
      />
      {malformed && !focused ? (
        <div className="dbcp-url-message bad" role="alert">
          refused client-side — {malformed.reason}
        </div>
      ) : refused ? (
        <div className="dbcp-url-message">
          {refused.reason} — keep typing, or edit the fields below (the URL stays local either way).
        </div>
      ) : (
        <div className="dbcp-hint">
          Type one URL instead of six fields; accepted schemes:{" "}
          <span className="dbcp-mono">postgresql</span>, <span className="dbcp-mono">postgres</span>,{" "}
          <span className="dbcp-mono">pgsql</span>. A malformed URL is refused here client-side and never sent to the
          backend.
        </div>
      )}
      {shown.length > 0 && <div className="dbcp-url-echo">echo: {maskUrl(shown)}</div>}
    </div>
  );
}
