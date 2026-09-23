/**
 * traceStream — the Decision Trace SSE client (§76/§77).
 *
 * A bounded, failure-isolated live-data producer, modeled on
 * `core/realtime.ts` (the engine snapshot stream) but self-contained to this
 * bounded context because it activates/deactivates with the page:
 *
 *  - connect:  EventSource with `?token=` (headers are impossible on ES)
 *              + `?last_seq=` resume point so reconnects never silently
 *              duplicate or fabricate continuity (§77 TRACE GAP instead).
 *  - watchdog: a stalled stream fires NO error event (BUG-212 lesson from
 *              core/realtime) — a timer forces reconnect when no frame
 *              arrives within the stale window.
 *  - honesty:  connection state is a strict state machine
 *              (connecting/connected/reconnecting/disconnected/failed); the
 *              UI shows DISCONNECTED/OBSERVABILITY OFFLINE truthfully.
 *  - safety:   frames are passed through, never repaired. A malformed frame
 *              increments `malformed` and is dropped — never fabricated into
 *              a synthetic event (§02).
 *
 * Nothing in here can affect trading: it only consumes an observer stream.
 */

import { resolveToken } from "@/core/auth";
import { runtimeConfig, STORAGE_KEYS } from "@/core/config";
import type { TraceStreamFrame } from "./types";

export type TraceStreamStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "failed";

export interface TraceStreamOptions {
  /** Resume point: last sequence the client already rendered. */
  lastSeq?: number;
  /** No frame within this window => half-open watchdog reconnect (ms). */
  staleMs?: number;
  maxBackoffMs?: number;
  onFrame: (frame: TraceStreamFrame) => void;
  onStatus: (status: TraceStreamStatus, info?: { error?: string; attempt?: number }) => void;
  /** Gap frames (evicted resume point) — surfaced as TRACE GAP. */
  onGap?: (message: string) => void;
}

const DEFAULT_STALE_MS = 10_000;
const DEFAULT_MAX_BACKOFF = 8_000;

function streamUrl(lastSeq: number): string {
  let token: string | null = null;
  try {
    token = resolveToken();
    if (!token) token = sessionStorage.getItem(STORAGE_KEYS.token);
  } catch {
    token = null;
  }
  const base = `${runtimeConfig.apiBase}/api/trace/stream`;
  const params = new URLSearchParams({ last_seq: String(Math.max(0, Math.floor(lastSeq) || 0)) });
  if (token) params.set(runtimeConfig.tokenQueryParam, token);
  return `${base}?${params.toString()}`;
}

export class TraceStreamClient {
  private es: EventSource | null = null;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private backoff = 500;
  private attempt = 0;
  private lastSeq: number;
  private closed = false;
  private lastFrameAt = 0;
  private opts: TraceStreamOptions;

  constructor(opts: TraceStreamOptions) {
    this.opts = opts;
    this.lastSeq = Math.max(0, opts.lastSeq ?? 0);
  }

  get resumeSeq(): number {
    return this.lastSeq;
  }

  connect(): void {
    if (this.closed) return;
    this.opts.onStatus(this.attempt === 0 ? "connecting" : "reconnecting", { attempt: this.attempt });
    try {
      this.es = new EventSource(streamUrl(this.lastSeq));
    } catch (err) {
      this.scheduleReconnect(String(err));
      return;
    }
    const es = this.es;

    es.addEventListener("hello", (ev) => this.handle(ev));
    es.addEventListener("batch", (ev) => this.handle(ev));
    es.addEventListener("heartbeat", (ev) => this.handle(ev));
    es.addEventListener("gap", (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as { message?: string };
        if (this.opts.onGap) this.opts.onGap(data.message ?? "TRACE GAP");
      } catch {
        if (this.opts.onGap) this.opts.onGap("TRACE GAP");
      }
      this.touch();
    });
    es.addEventListener("error", (ev) => {
      // EventSource error: either a transport failure or a server-side close.
      // Never fabricate data here — report the honest state and reconnect.
      if (es.readyState === EventSource.CLOSED) {
        this.scheduleReconnect((ev as Event).type);
      } else {
        this.opts.onStatus("reconnecting", { attempt: this.attempt });
      }
    });
    es.onopen = () => {
      this.attempt = 0;
      this.backoff = 500;
      this.opts.onStatus("connected");
      this.touch();
    };
  }

  private handle(ev: MessageEvent): void {
    this.touch();
    let frame: TraceStreamFrame;
    try {
      frame = JSON.parse(ev.data) as TraceStreamFrame;
    } catch {
      // Malformed frame: drop it (§02 never fabricate) but stay honest —
      // the store surfaces `malformed`.
      this.opts.onStatus("connected", { error: "MALFORMED_FRAME" });
      return;
    }
    if (frame && frame.event === "batch") {
      const events = Array.isArray(frame.events) ? frame.events : [];
      const lastEv = events.length ? events[events.length - 1] : undefined;
      const last = lastEv ? lastEv.sequence : this.lastSeq;
      this.lastSeq = Math.max(this.lastSeq, frame.last_seq ?? last);
    } else if (frame && frame.event === "hello" && typeof frame.last_seq === "number") {
      this.lastSeq = Math.max(this.lastSeq, frame.last_seq);
    }
    this.opts.onFrame(frame);
  }

  private touch(): void {
    this.lastFrameAt = Date.now();
    if (this.watchdog) clearTimeout(this.watchdog);
    const stale = this.opts.staleMs ?? DEFAULT_STALE_MS;
    this.watchdog = setTimeout(() => {
      if (Date.now() - this.lastFrameAt >= stale) {
        // half-open connection: no frames, no error event — force reconnect
        this.teardownEs();
        this.scheduleReconnect("stale-watchdog");
      } else {
        this.touch();
      }
    }, stale + 1000);
  }

  private scheduleReconnect(cause: string): void {
    if (this.closed) return;
    this.attempt += 1;
    this.opts.onStatus("reconnecting", { error: cause, attempt: this.attempt });
    const max = this.opts.maxBackoffMs ?? DEFAULT_MAX_BACKOFF;
    const delay = Math.min(this.backoff, max);
    this.backoff = Math.min(this.backoff * 2, max);
    setTimeout(() => {
      if (!this.closed) this.connect();
    }, delay);
  }

  private teardownEs(): void {
    if (this.es) {
      try {
        this.es.close();
      } catch {
        /* already closed */
      }
      this.es = null;
    }
  }

  /** Stop consuming; the backend releases the subscriber on disconnect. */
  close(): void {
    this.closed = true;
    if (this.watchdog) clearTimeout(this.watchdog);
    this.watchdog = null;
    this.teardownEs();
    this.opts.onStatus("disconnected");
  }
}
