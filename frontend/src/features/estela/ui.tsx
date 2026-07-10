// Shared UI primitives for Estela: common style objects (previously re-declared per view)
// and a themed confirmation dialog that replaces the native window.confirm/prompt/alert mix.
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { fonts } from "./theme";

// Debounced async search: runs `fn(q)` `delay` ms after the last keystroke, ignoring stale
// responses. Replaces the per-view setTimeout scaffolding that was duplicated six times.
export function useDebouncedSearch<T>(
  q: string,
  fn: (q: string) => Promise<T[]>,
  { delay = 300, minLength = 2 }: { delay?: number; minLength?: number } = {},
): T[] {
  const [results, setResults] = useState<T[]>([]);
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    if (q.trim().length < minLength) { setResults([]); return; }
    const t = setTimeout(() => {
      fn(q).then((r) => { if (seq.current === mine) setResults(r); })
        .catch(() => { if (seq.current === mine) setResults([]); });
    }, delay);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fn is intentionally not a dep (callers pass inline lambdas)
  }, [q, delay, minLength]);
  return results;
}

// ── shared style objects ──
export const primaryBtn: CSSProperties = { background: "var(--accent)", color: "#fff", border: "none", borderRadius: 9, padding: "10px 18px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" };
export const ghostBtn: CSSProperties = { background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, padding: "7px 12px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" };
export const dangerBtn: CSSProperties = { background: "var(--danger)", color: "#fff", border: "none", borderRadius: 9, padding: "10px 18px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" };
export const miniBtn: CSSProperties = { background: "transparent", border: "1px solid var(--line)", borderRadius: 6, padding: "4px 9px", cursor: "pointer", color: "var(--fg)", fontFamily: "inherit", fontSize: 11.5, fontWeight: 600 };
export const field: CSSProperties = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 9, padding: "10px 12px", color: "var(--fg)", fontFamily: "inherit", fontSize: 14, boxSizing: "border-box" };
export const overlay: CSSProperties = { position: "fixed", inset: 0, background: "rgba(0,0,0,.4)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 };
export const modalCard: CSSProperties = { background: "var(--elevated, var(--surface))", border: "1px solid var(--line)", borderRadius: 14, padding: 24, width: "100%", maxWidth: 460, boxShadow: "var(--shadow)" };

export interface ConfirmOpts {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  /** Require the user to type this word (e.g. "BORRAR") before confirming — for irreversible actions. */
  typed?: string;
}

// useConfirm(): themed, promise-based replacement for window.confirm / prompt("BORRAR").
//   const { confirmDialog, ask } = useConfirm();
//   if (!(await ask({ title: "¿Borrar el libro?", danger: true }))) return;
// Render {confirmDialog} once in the view's JSX.
export function useConfirm(): { confirmDialog: ReactNode; ask: (opts: ConfirmOpts) => Promise<boolean> } {
  const [opts, setOpts] = useState<ConfirmOpts | null>(null);
  const [typedValue, setTypedValue] = useState("");
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const ask = useCallback((o: ConfirmOpts) => {
    setOpts(o);
    setTypedValue("");
    return new Promise<boolean>((resolve) => { resolver.current = resolve; });
  }, []);

  const close = useCallback((ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setOpts(null);
  }, []);

  const confirmDialog = opts ? (
    <div style={overlay} onClick={() => close(false)}>
      <div style={modalCard} onClick={(e) => e.stopPropagation()}>
        <h2 style={{ fontFamily: fonts.serif, fontSize: 20, fontWeight: 600, margin: "0 0 10px", color: opts.danger ? "var(--danger)" : "var(--fg)" }}>{opts.title}</h2>
        {opts.body && <div style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.5, marginBottom: 16 }}>{opts.body}</div>}
        {opts.typed && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 6 }}>Escribe <b style={{ color: "var(--danger)" }}>{opts.typed}</b> para confirmar:</div>
            <input autoFocus style={{ ...field, width: "100%" }} value={typedValue} onChange={(e) => setTypedValue(e.target.value)} />
          </div>
        )}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button style={ghostBtn} onClick={() => close(false)}>{opts.cancelLabel ?? "Cancelar"}</button>
          <button
            style={{ ...(opts.danger ? dangerBtn : primaryBtn), opacity: opts.typed && typedValue !== opts.typed ? 0.5 : 1 }}
            disabled={!!opts.typed && typedValue !== opts.typed}
            onClick={() => close(true)}
          >
            {opts.confirmLabel ?? "Confirmar"}
          </button>
        </div>
      </div>
    </div>
  ) : null;

  return { confirmDialog, ask };
}
