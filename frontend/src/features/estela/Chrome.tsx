import { useEstela } from "./store";
import { fonts } from "./theme";
import { Cross } from "./icons";

export function Toast() {
  const e = useEstela();
  if (!e.toast.show) return null;
  const canUndo = !!e.toast.changeId;
  return (
    <div style={{
      position: "fixed", left: "50%", bottom: 28, transform: "translateX(-50%)", zIndex: 60,
      display: "flex", alignItems: "center", gap: 14, background: "var(--elevated)",
      border: "1px solid var(--line)", borderLeft: `3px solid ${e.toast.color}`,
      borderRadius: 11, padding: "13px 18px", boxShadow: "var(--shadow)",
      animation: "estToast .3s ease",
    }}>
      <span style={{ width: 9, height: 9, borderRadius: "50%", background: e.toast.color, flex: "none" }} />
      <span style={{ fontSize: 13.5, color: "var(--fg)" }}>{e.toast.msg}</span>
      {canUndo && (
        <button onClick={e.undo} style={{ background: "transparent", color: "var(--accent)", border: "none", fontFamily: fonts.sans, fontSize: 13, fontWeight: 600, cursor: "pointer", padding: "2px 4px" }}>
          Deshacer
        </button>
      )}
    </div>
  );
}

export function ZoomModal() {
  const e = useEstela();
  if (!e.zoom) return null;
  return (
    <div onClick={() => e.setZoom(false)} style={{
      position: "fixed", inset: 0, zIndex: 80, background: "rgba(15,11,6,.82)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 40, cursor: "zoom-out",
    }}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ position: "relative", maxWidth: 880, width: "100%", background: "var(--elevated)", borderRadius: 14, overflow: "hidden", boxShadow: "0 24px 80px rgba(0,0,0,.6)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 20px", borderBottom: "1px solid var(--line2)" }}>
          <span style={{ fontFamily: fonts.mono, fontSize: 12, color: "var(--muted)", letterSpacing: ".08em" }}>{e.cur.recRef} · {e.cur.folio}</span>
          <div onClick={() => e.setZoom(false)} style={{ cursor: "pointer", color: "var(--muted)", display: "flex" }}><Cross size={18} /></div>
        </div>
        <div style={{ minHeight: 220, padding: 32, display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center", color: "var(--muted)", fontSize: 14 }}>
          No hay una imagen original disponible para ampliar. Consulta el Visor o la transcripción asociada.
        </div>
      </div>
    </div>
  );
}
