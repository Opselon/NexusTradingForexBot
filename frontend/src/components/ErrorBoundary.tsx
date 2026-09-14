/**
 * ErrorBoundary — last line of defense for the routed tree (audit lane-09:
 * zero boundaries existed, so a single render-time TypeError unmounted the
 * WHOLE /alt app).
 *
 * Contract: componentDidCatch logs the real error (console + the failed
 * component stack, no swallowing) and renders an honest fallback card that
 * states what happened and offers a Reload. It NEVER renders blank and NEVER
 * disguises an error as a data state. Reset paths: the Retry button re-arms
 * the subtree; navigating between routes remounts it through the key on the
 * boundary in AppShell, so a failed page can't wedge the shell.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Shown as the boundary's context word in the fallback (e.g. the route). */
  label?: string;
  /** Change it to force-remount and clear a caught error. */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
  detail: string | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, detail: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Log the real failure — the operator console must not eat stack traces.
    console.error("[ErrorBoundary] render failure:", error, info.componentStack);
    this.setState({ detail: (info.componentStack ?? "").trim().split("\n").slice(0, 3).join(" / ") || null });
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null, detail: null });
    }
  }

  private readonly reset = () => this.setState({ error: null, detail: null });

  render(): ReactNode {
    const { error, detail } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="state-block error eb-card" role="alert" style={{ display: "grid", gap: 8, justifyItems: "start", padding: 16 }}>
        <div className="glyph">⛔</div>
        <div>
          <strong>
            {this.props.label ?? "This view"} crashed while rendering.
          </strong>{" "}
          The rest of the console is intact — no values were hidden or invented.
        </div>
        <div className="hint inline-mono" style={{ whiteSpace: "pre-wrap", maxWidth: 720 }}>
          {error.message || String(error)}
          {detail ? `\n${detail}` : ""}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn small" onClick={this.reset}>
            Retry render
          </button>
          <button className="btn small primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
