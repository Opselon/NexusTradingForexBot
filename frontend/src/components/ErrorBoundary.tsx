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
 *
 * i18n (wave 2026-09): a class component can't select the store, so the
 * exported wrapper selects `t` and passes it down — the wrapper re-render on
 * a language switch gives the class new props, which re-renders the fallback
 * in the active language without resetting its error state (same inner type,
 * same instance).
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { useI18n } from "@/stores/i18nStore";

type Translate = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

interface Props {
  children: ReactNode;
  /** Shown as the boundary's context word in the fallback (e.g. the route). */
  label?: string;
  /** Change it to force-remount and clear a caught error. */
  resetKey?: unknown;
}

interface InnerProps extends Props {
  t: Translate;
}

interface State {
  error: Error | null;
  detail: string | null;
}

class ErrorBoundaryInner extends Component<InnerProps, State> {
  state: State = { error: null, detail: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Log the real failure — the operator console must not eat stack traces.
    console.error("[ErrorBoundary] render failure:", error, info.componentStack);
    this.setState({ detail: (info.componentStack ?? "").trim().split("\n").slice(0, 3).join(" / ") || null });
  }

  componentDidUpdate(prev: InnerProps): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null, detail: null });
    }
  }

  private readonly reset = () => this.setState({ error: null, detail: null });

  render(): ReactNode {
    const { error, detail } = this.state;
    const t = this.props.t;
    if (!error) return this.props.children;
    const who = this.props.label ?? t("ui.eb.this_view", "This view");
    return (
      <div className="state-block error eb-card" role="alert">
        <div className="glyph">⛔</div>
        <div>
          <strong>{t("ui.eb.crashed", "{l} crashed while rendering.", { l: who })}</strong>{" "}
          {t("ui.eb.intact", "The rest of the console is intact — no values were hidden or invented.")}
        </div>
        <div className="hint inline-mono">
          {error.message || String(error)}
          {detail ? `\n${detail}` : ""}
        </div>
        <div className="eb-actions">
          <button className="btn small" onClick={this.reset}>
            {t("ui.eb.retry_render", "Retry render")}
          </button>
          <button className="btn small primary" onClick={() => window.location.reload()}>
            {t("ui.eb.reload", "Reload")}
          </button>
        </div>
      </div>
    );
  }
}

/** Language-aware shell around the class boundary (see the file header). */
export function ErrorBoundary(props: Props) {
  const t = useI18n((s) => s.t);
  return <ErrorBoundaryInner {...props} t={t} />;
}

export default ErrorBoundary;
