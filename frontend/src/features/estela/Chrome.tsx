import { useEstela } from "./store";
import { fonts } from "./theme";
import { Cross } from "./icons";

export function Toast() {
  const e = useEstela();
  if (!e.toast.show) return null;
  const canUndo = e.toast.kind !== null;
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
        <ManuscriptPlate quote={e.cur.recQuote} tall />
      </div>
    </div>
  );
}

// A stylised manuscript "page" plate standing in for the <dc-import name="Manuscrito"> import.
export function ManuscriptPlate({ quote, label, tall = false }: { quote?: string; label?: string; tall?: boolean }) {
  return (
    <div style={{
      position: "relative", height: tall ? 460 : "100%", minHeight: tall ? undefined : 150,
      background: "linear-gradient(155deg,#efe4ca,#dcc9a2)", overflow: "hidden",
      boxShadow: "inset 0 0 0 1px rgba(120,85,40,.25)",
    }}>
      <div style={{ position: "absolute", inset: 0, background: "repeating-linear-gradient(transparent 0 26px, rgba(96,64,30,.12) 26px 27px)" }} />
      <div style={{ position: "absolute", inset: 0, padding: tall ? "40px 56px" : "18px 22px", fontFamily: fonts.serif, fontStyle: "italic", color: "#4a3414", fontSize: tall ? 22 : 13, lineHeight: tall ? "27px" : "20px", textShadow: "0 1px 0 rgba(255,255,255,.3)" }}>
        {quote || "Joannes, fill de Francesc Vidal y de Maria Soler…"}
      </div>
      {label && (
        <span style={{ position: "absolute", bottom: 8, left: 10, background: "rgba(20,16,10,.6)", color: "#fff", fontFamily: fonts.mono, fontSize: 10, padding: "3px 7px", borderRadius: 5 }}>{label}</span>
      )}
    </div>
  );
}
