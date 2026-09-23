/**
 * Research — StrategyPlaybook: the full-text strategy handbook (docs).
 *
 * Static documentation compiled from backend source (handbook/*). It is
 * deliberately NOT data: no live numbers, no invented metrics — the tabs
 * around it render backend responses. This component renders prose,
 * verbatim constants tables, and FAQs with search + cross-links.
 *
 * UX contract: keyboard-reachable (buttons/inputs), aria-labelled search,
 * expandable entries animate via CSS grid-rows (no JS height math), and
 * reduced-motion freezes all of it (research.css guard).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { EmptyState } from "@/components/primitives";
import { getEntry, queryHandbook, type HandbookEntry } from "../handbook";

type Props = {
  /** Entry id to expand + scroll to on mount (cross-links, drawer deep-links). */
  focusId?: string;
  /** Hide the TOC sidebar (used inside the strategy drawer). */
  compact?: boolean;
};

export default function StrategyPlaybook({ focusId, compact }: Props) {
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<string | undefined>(focusId);
  const nodeRefs = useRef(new Map<string, HTMLElement>());

  const groups = useMemo(() => queryHandbook(query), [query]);
  const total = groups.reduce((n, g) => n + g.entries.length, 0);

  // Deep-link: expand the focused entry and bring it into view.
  useEffect(() => {
    if (!focusId) return;
    setOpenId(focusId);
    const t = window.setTimeout(() => {
      nodeRefs.current.get(focusId)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
    return () => window.clearTimeout(t);
  }, [focusId]);

  const toggle = (id: string) => setOpenId((cur) => (cur === id ? undefined : id));

  const jump = (id: string) => {
    if (query && !getEntry(id)) setQuery(""); // cross-link may be filtered out — clear search first
    setOpenId(id);
    window.setTimeout(() => {
      nodeRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 40);
  };

  const register = (id: string, node: HTMLElement | null) => {
    if (node) nodeRefs.current.set(id, node);
    else nodeRefs.current.delete(id);
  };

  return (
    <div className="rs-pb">
      {!compact && (
        <aside className="rs-pb-toc" aria-label="Playbook sections">
          <div className="rs-pb-toc-title">Playbook</div>
          <div className="rs-pb-docs-note">
            Engineering documentation compiled from backend source. Static docs — live
            numbers stay in the data tabs.
          </div>
          {groups.map((g) => (
            <div className="rs-pb-group" key={g.key}>
              <div className="rs-pb-group-label">
                {g.label} · {g.entries.length}
              </div>
              {g.entries.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  className={`rs-pb-toc-item ${openId === e.id ? "current" : ""}`}
                  onClick={() => jump(e.id)}
                >
                  {e.title}
                </button>
              ))}
            </div>
          ))}
        </aside>
      )}

      <div>
        <div className="rs-pb-search">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search gates, states, thresholds… e.g. “purge”, “DSR”, “EVIDENCE_BUILDING”"
            aria-label="Search the strategy playbook"
          />
          <span className="rs-pb-count">
            {total} {total === 1 ? "entry" : "entries"}
          </span>
        </div>

        {total === 0 ? (
          <EmptyState
            message={`No playbook entry matches “${query}”.`}
            hint="Try a gate name (BACKTEST), a state (EVIDENCE_BUILDING), or a constant (0.25R, purge, DSR)."
          />
        ) : (
          groups.map((g) => (
            <div key={g.key}>
              <div className="rs-pb-section-label">{g.label}</div>
              <div className="rs-stagger" style={{ display: "grid", gap: 0 }}>
                {g.entries.map((e) => (
                  <EntryCard
                    key={e.id}
                    entry={e}
                    open={openId === e.id}
                    onToggle={() => toggle(e.id)}
                    onSeeAlso={jump}
                    bindRef={(node) => register(e.id, node)}
                  />
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function EntryCard({
  entry,
  open,
  onToggle,
  onSeeAlso,
  bindRef,
}: {
  entry: HandbookEntry;
  open: boolean;
  onToggle: () => void;
  onSeeAlso: (id: string) => void;
  bindRef: (node: HTMLElement | null) => void;
}) {
  const label = `${entry.kind}: ${entry.title}`;
  return (
    <article className={`rs-pb-entry ${open ? "open" : ""}`} ref={bindRef} id={`pb-${entry.id}`}>
      <button type="button" className="rs-pb-head" onClick={onToggle} aria-expanded={open} aria-label={label}>
        {entry.badge && <span className="rs-pb-badge">{entry.badge}</span>}
        <span>
          <span className="rs-pb-title">{entry.title}</span>
          <span className="rs-pb-sub" style={{ display: "block" }}>
            {entry.subtitle}
          </span>
        </span>
        <span className="rs-pb-chevron" aria-hidden="true">
          ▸
        </span>
      </button>

      <div className="rs-pb-body">
        <div>
          <div className="rs-pb-inner">
            {entry.sections.map((s) => (
              <section key={s.heading}>
                <h4>{s.heading}</h4>
                {s.body.map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
                {s.bullets && s.bullets.length > 0 && (
                  <ul>
                    {s.bullets.map((b, i) => (
                      <li key={i}>{b}</li>
                    ))}
                  </ul>
                )}
              </section>
            ))}

            {entry.params && entry.params.length > 0 && (
              <>
                <h4>Constants (verbatim from source)</h4>
                <table className="rs-params">
                  <thead>
                    <tr>
                      <th>name</th>
                      <th>value</th>
                      <th>meaning</th>
                      <th>ref</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entry.params.map((p) => (
                      <tr key={p.name + p.value}>
                        <td>{p.name}</td>
                        <td>{p.value}</td>
                        <td>{p.meaning}</td>
                        <td>{p.ref}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}

            {entry.faq && entry.faq.length > 0 && (
              <>
                <h4>Operator FAQ</h4>
                {entry.faq.map((f) => (
                  <div className="rs-faq" key={f.q}>
                    <div className="rs-faq-q">{f.q}</div>
                    <div className="rs-faq-a">{f.a}</div>
                  </div>
                ))}
              </>
            )}

            <div className="rs-src" title="Backend source this entry was compiled from">
              ◆ {entry.source}
            </div>

            {entry.seeAlso && entry.seeAlso.length > 0 && (
              <div className="rs-seealso">
                <span className="tiny muted" style={{ alignSelf: "center" }}>
                  see also →
                </span>
                {entry.seeAlso.map((id) => (
                  <button type="button" key={id} className="rs-see" onClick={() => onSeeAlso(id)}>
                    {id.replace(/^(gate|state|topic)\//, "")}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}
