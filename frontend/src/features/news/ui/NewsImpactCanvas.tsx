/**
 * NewsImpactCanvas — 1:1 port of legacy `drawNewsImpactChart` (Web/app.js
 * ~L10906-11160) onto a DPR-aware Canvas 2D surface.
 *
 * Data contract: renders the EXACT bucket rows GET /api/news/timeline returns
 * (bucket_start / bullish / bearish / neutral / article_count / top_title —
 * signed impact sums the backend already aggregated). No re-bucketing, no
 * smoothing, no extrapolation: a missing magnitude draws at the backend's own
 * value or 0 exactly as legacy `(b[key] || 0)` did; an empty bucket list keeps
 * the dashed zero line + honest empty overlay (legacy news-timeline-empty).
 *
 * Ported semantics (each one verified in the legacy body):
 *  - zero-centered y-scale, floor maxAbs = 0.05, scale = (plotH/2 - 6)/maxAbs
 *  - padL 34, padR 8, padT 12, padB 18; step = plotW / max(n-1, 1)
 *  - neutral area, then bearish, then bullish fills @ 0.18 alpha; lines 1.6px
 *  - r2 point markers on bullish/bearish at every bucket
 *  - x labels when n <= 24 or every ceil(n/12) bucket; day format when bucket_sec >= 86400
 *  - hover: nearest bucket within 22px, else hide (rAF-throttled like legacy)
 *  - footer "Top: … · impact <mag>" from the backend's own bucket numbers
 *  - DPR transform (setTransform(dpr,0,0,dpr,0,0)) — crisp on scaled displays
 *
 * Colors come from theme.css tokens resolved via getComputedStyle (canvas has
 * no var() support), falling back to the legacy hues only if a token is absent.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import type { Lang } from "@/lib/i18n";
import type { NewsTimelineBucket } from "../types";

interface Props {
  buckets: NewsTimelineBucket[];
  bucketSec: number;
  hoursBack: number;
  height?: number;
  emptyHint?: string;
}

interface Hover {
  index: number;
  x: number;
  y: number;
}

/** Locale for canvas-rendered dates — switches with the active app language. */
const DATE_LOCALE: Record<Lang, string> = { en: "en-US", fa: "fa-IR", de: "de-DE", es: "es-ES", ar: "ar" };

const PAD_L = 34;
const PAD_R = 8;
const PAD_T = 12;
const PAD_B = 18;

function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function NewsImpactCanvas({ buckets, bucketSec, hoursBack, height = 220, emptyHint }: Props) {
  const t = useI18n((s) => s.t);
  const lang = useI18n((s) => s.lang);
  const locale = DATE_LOCALE[lang] ?? "en";
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);

  const colors = useMemo(
    () => ({
      bgTop: token("--bg-panel", "#0b1320"),
      bgBottom: token("--bg", "#060a12"),
      grid: token("--border", "#1e293b"),
      axisText: token("--text-faint", "#475569"),
      neutral: token("--text-dim", "#94a3b8"),
      bearish: token("--red", "#ef4444"),
      bullish: token("--green", "#22c55e"),
    }),
    // density/theme never change at runtime in this app; compute once per mount
    [],
  );

  /* Draw on every data or size change; ResizeObserver keeps it honest when the
   * panel widens/narrows (legacy re-drew on each poll tick). */
  useEffect(() => {
    let raf = 0;
    const draw = (): void => {
      const canvas = canvasRef.current;
      const wrap = wrapRef.current;
      if (!canvas || !wrap) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      // Time axis stays LTR regardless of document direction (canvas text
      // direction inherits from computed style — force legacy behavior).
      ctx.direction = "ltr";
      const dpr = window.devicePixelRatio || 1;
      let rect = canvas.getBoundingClientRect();
      if ((rect.width | 0) < 10) rect = wrap.getBoundingClientRect();
      if ((rect.width | 0) < 10) return;
      const h = (rect.height | 0) < 10 ? height : rect.height;
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = rect.width;

      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, colors.bgTop);
      g.addColorStop(1, colors.bgBottom);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      if (buckets.length < 1) {
        // keep the grid so the empty state doesn't look broken (legacy)
        ctx.strokeStyle = colors.grid;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(PAD_L, h / 2);
        ctx.lineTo(w - PAD_R, h / 2);
        ctx.stroke();
        ctx.setLineDash([]);
        return;
      }

      const plotW = w - PAD_L - PAD_R;
      const plotH = h - PAD_T - PAD_B;
      let maxAbs = 0.05;
      for (const b of buckets) {
        maxAbs = Math.max(maxAbs, Math.abs(b.bullish || 0), Math.abs(b.bearish || 0), Math.abs(b.neutral || 0));
      }
      const midY = PAD_T + plotH / 2;
      const scale = (plotH / 2 - 6) / maxAbs;
      const yAt = (v: number) => midY - v * scale;

      ctx.strokeStyle = colors.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD_L, midY);
      ctx.lineTo(w - PAD_R, midY);
      ctx.stroke();

      ctx.fillStyle = colors.axisText;
      ctx.font = "9px monospace";
      ctx.textAlign = "right";
      ctx.fillText("0", PAD_L - 4, midY + 3);
      ctx.fillText(`+${maxAbs.toFixed(2)}`, PAD_L - 4, PAD_T + 6);
      ctx.fillText(`-${maxAbs.toFixed(2)}`, PAD_L - 4, h - PAD_B + 6);

      const n = buckets.length;
      const step = plotW / Math.max(n - 1, 1);
      const xAt = (i: number) => PAD_L + i * step;

      const line = (key: "bullish" | "bearish" | "neutral", color: string): void => {
        ctx.beginPath();
        buckets.forEach((b, i) => {
          const y = yAt((b[key] || 0) as number);
          if (i === 0) ctx.moveTo(xAt(i), y);
          else ctx.lineTo(xAt(i), y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.6;
        ctx.stroke();
      };
      const fill = (key: "bullish" | "bearish" | "neutral", color: string): void => {
        ctx.beginPath();
        buckets.forEach((b, i) => {
          const y = yAt((b[key] || 0) as number);
          if (i === 0) ctx.moveTo(xAt(i), y);
          else ctx.lineTo(xAt(i), y);
        });
        ctx.lineTo(xAt(n - 1), midY);
        ctx.lineTo(xAt(0), midY);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.18;
        ctx.fill();
        ctx.globalAlpha = 1.0;
      };

      fill("neutral", colors.neutral);
      fill("bearish", colors.bearish);
      fill("bullish", colors.bullish);
      line("neutral", colors.neutral);
      line("bearish", colors.bearish);
      line("bullish", colors.bullish);

      ctx.textAlign = "center";
      ctx.font = "9px monospace";
      buckets.forEach((b, i) => {
        const x = xAt(i);
        ctx.beginPath();
        ctx.arc(x, yAt((b.bullish || 0) as number), 2, 0, Math.PI * 2);
        ctx.fillStyle = colors.bullish;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, yAt((b.bearish || 0) as number), 2, 0, Math.PI * 2);
        ctx.fillStyle = colors.bearish;
        ctx.fill();
        if (n <= 24 || i % Math.ceil(n / 12) === 0) {
          ctx.fillStyle = colors.axisText;
          const d = new Date(b.bucket_start);
          const lbl =
            bucketSec >= 86_400
              ? d.toLocaleDateString(locale, { month: "short", day: "numeric" })
              : d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
          ctx.fillText(lbl, x, h - 4);
        }
      });
    };

    const schedule = (): void => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    };
    schedule();
    const ro = new ResizeObserver(schedule);
    if (wrapRef.current) ro.observe(wrapRef.current);
    window.addEventListener("resize", schedule);
    document.addEventListener("nexus:lang-changed", schedule);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("resize", schedule);
      document.removeEventListener("nexus:lang-changed", schedule);
    };
  }, [buckets, bucketSec, colors, height, lang, locale]);

  /* Hover: nearest bucket within 22px of the cursor x (legacy hit()). */
  const onMove = (e: React.MouseEvent<HTMLCanvasElement>): void => {
    if (buckets.length < 1) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const plotW = rect.width - PAD_L - PAD_R;
    const step = plotW / Math.max(buckets.length - 1, 1);
    let best: number | null = null;
    let bestDx = 1e9;
    buckets.forEach((_, i) => {
      const dx = Math.abs(mx - (PAD_L + i * step));
      if (dx < bestDx && dx < 22) {
        bestDx = dx;
        best = i;
      }
    });
    setHover(best === null ? null : { index: best, x: mx, y: my });
  };

  const top = useMemo<NewsTimelineBucket | null>(() => {
    let best: NewsTimelineBucket | null = null;
    let bestMag = -1;
    for (const b of buckets) {
      const mag = Math.abs(b.bullish) + Math.abs(b.bearish);
      if (mag > bestMag) {
        bestMag = mag;
        best = b;
      }
    }
    return best;
  }, [buckets]);
  const daily = bucketSec >= 86_400;
  const hb = hover !== null ? buckets[hover.index] : null;

  return (
    <div className="news-impact-chart" ref={wrapRef} style={{ height }}>
      <canvas
        ref={canvasRef}
        className="news-impact-canvas"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={t("news.impact.aria", "news impact chart, {n} buckets", { n: buckets.length })}
      />
      {buckets.length < 1 && (
        <div className="news-impact-empty">
          {emptyHint ?? t("news.impact.empty_default", "No news impact data in this window — try a wider timeframe.")}
        </div>
      )}
      {hb && hover && (
        <div
          className="news-impact-tip"
          style={{
            insetInlineStart: Math.max(8, Math.min((wrapRef.current?.clientWidth ?? 400) - 270, hover.x + 10)),
            insetBlockStart: Math.max(8, hover.y - 10),
          }}
        >
          <b>
            {daily
              ? new Date(hb.bucket_start).toLocaleDateString(locale)
              : new Date(hb.bucket_start).toLocaleString(locale)}
          </b>
          <div>
            <span className="up">▲ {(hb.bullish || 0).toFixed(3)}</span> · <span className="down">▼ {(hb.bearish || 0).toFixed(3)}</span> ·{" "}
            <span className="neu">● {(hb.neutral || 0).toFixed(3)}</span>
          </div>
          <div className="muted">{t("news.impact.articles", "{n} articles", { n: hb.article_count ?? 0 })}</div>
          {hb.top_title && <div className="tt">{hb.top_title}</div>}
        </div>
      )}
      <div className="news-impact-foot">
        <span className="inline-mono">
          {buckets.length > 0
            ? daily
              ? t("news.impact.window_day", "{h}h window", { h: hoursBack })
              : t("news.impact.window", "{h}h window / {m}m buckets", { h: hoursBack, m: bucketSec / 60 })
            : "—"}
        </span>
        <span className="top" title={top?.top_title ?? ""}>
          {top
            ? t(
                "news.impact.footer",
                "{part} · impact {mag}",
                {
                  part: top.top_title
                    ? t("news.impact.top", "Top: {title}", { title: top.top_title.slice(0, 88) })
                    : t("news.impact.events", "{n} events", { n: top.article_count ?? 0 }),
                  mag: (Math.abs(top.bullish) + Math.abs(top.bearish) + Math.abs(top.neutral || 0)).toFixed(3),
                },
              )
            : ""}
        </span>
      </div>
    </div>
  );
}
