import { useState } from "react";
import type { CSSProperties } from "react";
import { useEstela, confColors, colMap } from "../store";
import { fonts } from "../theme";
import { Check, Cross, SearchPlus } from "../icons";
import { shortcuts } from "../data";
import SourceScan from "../SourceScan";

export default function DescubrimientosView() {
  const e = useEstela();
  const [qf, setQf] = useState("");
  const [minPct, setMinPct] = useState(0);
  const discoveries = e.discoveries;
  const cur = e.cur; // may be undefined when the queue is empty (e.g. after a reset)
  const cc = confColors(cur?.cLevel ?? "BAJA");
  const pos = discoveries.filter((_, i) => i <= e.discIndex).length;

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1280 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 8 }}>
        <div>
          <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Descubrimientos</h1>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0" }}>Compara cada propuesta con su prueba. <span style={{ color: "var(--fg)" }}>Tú confirmas.</span></p>
        </div>
        <button onClick={() => e.reloadDiscoveries()} style={{ background: "var(--surface)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>↻ Actualizar</button>
      </div>

      {e.hasPending && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 296px", gap: 26, alignItems: "start", marginTop: 24 }}>
          <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16, overflow: "hidden", boxShadow: "var(--shadow)" }}>
            {/* confidence bar */}
            <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "16px 24px", borderBottom: "1px solid var(--line2)", background: cc.tint }}>
              <span style={{ fontFamily: fonts.mono, fontSize: 11, letterSpacing: ".14em", color: "var(--muted)" }}>CONFIANZA</span>
              <span style={{ fontWeight: 700, fontSize: 14, color: cc.c, letterSpacing: ".04em" }}>{cur.cLevel}</span>
              <div style={{ flex: 1, height: 8, borderRadius: 999, background: "var(--line2)", overflow: "hidden", maxWidth: 280 }}>
                <div style={{ height: "100%", borderRadius: 999, width: `${cur.pct}%`, background: cc.c }} />
              </div>
              <span style={{ fontFamily: fonts.serif, fontSize: 24, fontWeight: 600, color: cc.c }}>{cur.pct}%</span>
              <span style={{ marginLeft: "auto", fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>{pos} de {discoveries.length}</span>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
              {/* TU ARBOL */}
              <div style={{ padding: 24, borderRight: "1px solid var(--line2)" }}>
                <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".16em", color: "var(--ok)", marginBottom: 14, display: "flex", alignItems: "center", gap: 7 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--ok)" }} />TU ÁRBOL
                </div>
                <div style={{ display: "flex", gap: 14, alignItems: "center", marginBottom: 18 }}>
                  <div style={{ width: 52, height: 52, borderRadius: 12, flex: "none", background: "var(--ok)", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, opacity: 0.92 }}>{cur.treeInitial}</div>
                  <div>
                    <div style={{ fontFamily: fonts.serif, fontSize: 21, fontWeight: 600, lineHeight: 1.1 }}>{cur.treeName}</div>
                    <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 3 }}>{cur.treeMeta}</div>
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 11, fontSize: 13.5 }}>
                  {cur.treeFields.map((f, i) => (
                    <div key={i} style={{ display: "flex", gap: 10 }}>
                      <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)", width: 64, flex: "none", paddingTop: 1 }}>{f.k}</span>
                      <span style={{ color: colMap[f.c] || "var(--fg)" }}>{f.v}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* COINCIDENCIA */}
              <div style={{ padding: 24, background: "var(--bg)" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                  <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".16em", color: "var(--warn)", display: "flex", alignItems: "center", gap: 7 }}>
                    <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--warn)" }} />POSIBLE COINCIDENCIA
                  </div>
                  <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>{cur.recRef}</span>
                </div>
                <div style={{ fontFamily: fonts.serif, fontSize: 17, fontWeight: 600, marginBottom: 12 }}>{cur.recTitle}</div>
                <SourceScan docId={cur.docId} pageNo={cur.pageNo} quote={cur.recQuote} folio={cur.folio} />
                <div style={{ fontFamily: fonts.serif, fontStyle: "italic", fontSize: 14, lineHeight: 1.5, color: "var(--fg)", background: "var(--surface)", border: "1px solid var(--line2)", borderRadius: 8, padding: "11px 14px" }}>“{cur.recQuote}”</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 13, fontSize: 12.5 }}>
                  {cur.mentions.map((m, i) => (
                    <div key={i} style={{ display: "flex", gap: 9 }}>
                      <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--muted)", textTransform: "uppercase", width: 74, flex: "none", paddingTop: 2, letterSpacing: ".06em" }}>{m.role}</span>
                      <span style={{ fontWeight: 500 }}>{m.name}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* evidence */}
            <div style={{ padding: "20px 24px", borderTop: "1px solid var(--line2)" }}>
              <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".14em", color: "var(--muted)", marginBottom: 14 }}>POR QUÉ CREEMOS QUE ES ÉL</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {cur.evidence.map((ev, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 14, background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 10, padding: "11px 14px" }}>
                    <span style={{ fontFamily: fonts.mono, fontSize: 10, letterSpacing: ".08em", color: "var(--accent)", background: "var(--accent-faint)", border: "1px solid var(--line2)", borderRadius: 5, padding: "4px 8px", width: 84, textAlign: "center", flex: "none" }}>{ev.cat}</span>
                    <span style={{ flex: 1, fontSize: 13.5 }}>{ev.text}</span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: colMap[ev.c] }}>
                      <Check size={14} />{ev.s}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* relatives panel (after confirm) */}
            {e.panelOpen && (
              <RelativesPanel />
            )}

            {/* actions */}
            {!e.panelOpen && (
              <div style={{ display: "flex", gap: 12, padding: "20px 24px", borderTop: "1px solid var(--line2)" }}>
                <button onClick={e.confirm} style={{ flex: 1, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 9, background: "var(--ok)", color: "#fff", border: "none", borderRadius: 10, padding: 15, fontFamily: "inherit", fontSize: 15.5, fontWeight: 600, cursor: "pointer", boxShadow: "0 4px 16px rgba(47,125,84,.28)" }}>
                  <Check size={19} />{cur.yes}
                </button>
                <button onClick={e.reject} style={{ flex: "none", display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8, background: "var(--surface)", color: "var(--danger)", border: "1px solid var(--line)", borderRadius: 10, padding: "15px 22px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: "pointer" }}>
                  <Cross size={18} />No es él
                </button>
                <button onClick={e.skip} style={{ flex: "none", background: "transparent", color: "var(--muted)", border: "1px solid var(--line)", borderRadius: 10, padding: "15px 22px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: "pointer" }}>No lo sé</button>
              </div>
            )}
          </div>

          {/* side rail */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 24 }}>
            <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 18 }}>
              <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".12em", color: "var(--muted)", marginBottom: 14 }}>ATAJOS DE TECLADO</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 11, fontSize: 13 }}>
                {shortcuts.map((k) => (
                  <div key={k.label} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <kbd style={{ fontFamily: fonts.mono, fontSize: 11, minWidth: 26, textAlign: "center", padding: "4px 7px", background: "var(--bg)", border: "1px solid var(--line)", borderBottomWidth: 2, borderRadius: 6 }}>{k.key}</kbd>
                    <span style={{ color: "var(--muted)" }}>{k.label}</span>
                  </div>
                ))}
              </div>
            </div>
            <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 18 }}>
              <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".12em", color: "var(--muted)", marginBottom: 10 }}>EN COLA · {e.pendingCount}</div>
              <input value={qf} onChange={(ev) => setQf(ev.target.value)} placeholder="filtrar por apellido…" style={{ width: "100%", boxSizing: "border-box", background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 7, padding: "7px 10px", color: "var(--fg)", fontFamily: "inherit", fontSize: 12.5, marginBottom: 8 }} />
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>conf ≥ {minPct}%</span>
                <input type="range" min={0} max={90} step={10} value={minPct} onChange={(ev) => setMinPct(Number(ev.target.value))} style={{ flex: 1 }} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {discoveries.filter((d) => (!qf || `${d.treeName} ${d.recTitle}`.toLowerCase().includes(qf.toLowerCase())) && d.pct >= minPct).map((d) => {
                  const dec = e.decisions[d.id];
                  const isCur = d.id === cur.id && !dec;
                  const dotc = d.cLevel === "ALTA" ? "var(--ok)" : d.cLevel === "MEDIA" ? "var(--warn)" : "var(--terra)";
                  const dot = dec ? "var(--muted)" : dotc;
                  const strike: CSSProperties = dec ? { color: "var(--muted)", textDecoration: "line-through" } : {};
                  const idx = e.discoveries.indexOf(d);
                  return (
                    <div key={d.id} onClick={() => e.jumpTo(idx)} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 9, cursor: "pointer", background: isCur ? "var(--accent-faint)" : "transparent", border: `1px solid ${isCur ? "var(--accent)" : "transparent"}` }}>
                      <span style={{ width: 7, height: 7, borderRadius: "50%", flex: "none", background: dot }} />
                      <span style={{ flex: 1, fontSize: 13, fontWeight: 500, ...strike }}>{d.treeName}</span>
                      <span style={{ fontFamily: fonts.mono, fontSize: 11, color: dot }}>{dec === "yes" ? "✓" : dec === "no" ? "✕" : `${d.pct}%`}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {e.allDone && (
        <div style={{ marginTop: 60, textAlign: "center", padding: "60px 20px" }}>
          <div style={{ width: 76, height: 76, borderRadius: "50%", background: "var(--ok-faint)", display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 22 }}>
            <Check size={36} stroke="var(--ok)" />
          </div>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 28, fontWeight: 600, margin: 0 }}>Nada que revisar</h2>
          <p style={{ color: "var(--muted)", fontSize: 15, margin: "12px 0 24px" }}>Has revisado toda la cola. Añade más libros y buscaremos parientes por ti.</p>
          <button onClick={() => e.go("biblioteca")} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "13px 22px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: "pointer" }}>Añadir un libro</button>
        </div>
      )}
    </section>
  );
}

function RelativesPanel() {
  const e = useEstela();
  const cur = e.cur;
  return (
    <div style={{ padding: "22px 24px", borderTop: "1px solid var(--line2)", background: "var(--ok-faint)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 6 }}>
        <Check size={18} stroke="var(--ok)" />
        <span style={{ fontWeight: 600, fontSize: 15, color: "var(--ok)" }}>Confirmado. Esta acta también menciona…</span>
      </div>
      <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>Añádelos a tu árbol como <b style={{ color: "var(--warn)" }}>inferidos</b>, cada uno con su fuente.</p>
      <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
        {cur.relatives.map((r) => {
          const done = e.added[r.id];
          const parts = r.name.trim().split(/\s+/);
          const initial = ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || r.name.slice(0, 2);
          return (
            <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 13, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "11px 14px" }}>
              <div style={{ width: 38, height: 38, borderRadius: 9, flex: "none", background: "var(--warn)", opacity: 0.9, color: "#3a2a08", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 16 }}>{initial}</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{r.name}</div>
                <div style={{ fontSize: 12, color: "var(--muted)" }}>{r.rel}</div>
              </div>
              <button onClick={() => !done && e.addRel(r.id)} style={done
                ? { background: "var(--ok-faint)", color: "var(--ok)", border: "1px solid var(--ok)", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13, fontWeight: 600, cursor: "default", flex: "none" }
                : { background: "var(--warn)", color: "#3a2a08", border: "none", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13, fontWeight: 600, cursor: "pointer", flex: "none" }}>
                {done ? "✓ Añadido" : "Añadir"}
              </button>
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 18, paddingTop: 16, borderTop: "1px solid var(--line2)", flexWrap: "wrap" }}>
        <button onClick={e.addAllRel} style={{ background: "var(--ok)", color: "#fff", border: "none", borderRadius: 8, padding: "11px 18px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>Añadir todos</button>
        <button onClick={e.advance} style={{ background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, padding: "11px 18px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>Continuar →</button>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--accent)", fontWeight: 500 }}>
          <SearchPlus size={16} />Al aceptar, Estela buscará más actas de cada pariente
        </div>
      </div>
    </div>
  );
}
