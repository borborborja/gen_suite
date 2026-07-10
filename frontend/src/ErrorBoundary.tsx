import { Component, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { error: Error | null }

// Global error boundary: a render-time throw in any view shows a recoverable message
// instead of a white screen.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg, #faf7f2)", color: "var(--fg, #1f1b16)", fontFamily: "system-ui, sans-serif", padding: 24 }}>
        <div style={{ maxWidth: 480, textAlign: "center" }}>
          <h1 style={{ fontSize: 22, marginBottom: 8 }}>Algo ha fallado</h1>
          <p style={{ fontSize: 14, opacity: 0.75, marginBottom: 6 }}>Se ha producido un error inesperado en la interfaz.</p>
          <pre style={{ fontSize: 11, opacity: 0.6, whiteSpace: "pre-wrap", textAlign: "left", maxHeight: 120, overflow: "auto", margin: "0 0 18px" }}>{this.state.error.message}</pre>
          <button
            onClick={() => { this.setState({ error: null }); }}
            style={{ padding: "10px 18px", borderRadius: 8, border: "1px solid #ccc", background: "transparent", cursor: "pointer", fontSize: 14, marginRight: 10 }}
          >
            Reintentar
          </button>
          <button
            onClick={() => window.location.reload()}
            style={{ padding: "10px 18px", borderRadius: 8, border: "none", background: "#d9531e", color: "#fff", cursor: "pointer", fontSize: 14, fontWeight: 600 }}
          >
            Recargar la aplicación
          </button>
        </div>
      </div>
    );
  }
}
