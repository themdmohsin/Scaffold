import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Per-panel error boundary: one page section failing must not replace the
 * whole app with an error screen (the old dashboard's failure mode). Each
 * route renders inside this boundary with a retry.
 */

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the console signal; the engine's X-Request-Id (when present) is
    // quoted in the message so bug reports are actionable.
    console.error("Panel crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="panel error-panel" role="alert">
          <h2>This section failed to render</h2>
          <p>{this.state.error.message}</p>
          <button type="button" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
