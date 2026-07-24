import { useEffect, useState, type CSSProperties } from "react";
import { getPersonReport, displayName, type PersonReport } from "../../api/tree";
import { ghostBtn, primaryBtn } from "./ui";

const EVENT_LABEL: Record<string, string> = {
  birth: "Nacimiento", baptism: "Bautismo", christening: "Cristianización", marriage: "Matrimonio",
  death: "Defunción", burial: "Entierro", confirmation: "Confirmación", residence: "Residencia",
  census: "Censo", occupation: "Oficio", divorce: "Divorcio", engagement: "Compromiso",
};

/** Ficha imprimible: overlay blanco con la persona completa; al imprimir solo se ve el informe. */
export default function PrintReport({ personId, onClose }: { personId: string; onClose: () => void }) {
  const [r, setR] = useState<PersonReport | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { getPersonReport(personId).then(setR).catch(() => setErr(true)); }, [personId]);

  const name = r ? displayName({
    given: r.person.names[0]?.given ?? null, surname: r.person.names[0]?.surname ?? null,
  }) : "";

  const h2: CSSProperties = { fontSize: 15, fontWeight: 700, borderBottom: "1.5px solid #222", paddingBottom: 4, margin: "22px 0 10px" };
  const td: CSSProperties = { padding: "4px 10px 4px 0", verticalAlign: "top", fontSize: 12.5 };

  return (
    <div className="print-overlay" style={{ position: "fixed", inset: 0, zIndex: 200, background: "rgba(0,0,0,.5)", overflowY: "auto", padding: "30px 16px" }} onClick={onClose}>
      <style>{`
        @media print {
          body * { visibility: hidden !important; }
          .print-report, .print-report * { visibility: visible !important; }
          /* el overlay fixed+scroll recortaría el informe a una página: en papel ambos
             pasan a flujo normal para que el contenido pagine completo */
          .print-overlay { position: static !important; overflow: visible !important; padding: 0 !important; background: none !important; }
          .print-report { position: static !important; margin: 0 !important; max-width: none !important; border-radius: 0 !important; box-shadow: none !important; }
          .print-hide { display: none !important; }
        }
      `}</style>
      <div className="print-report" onClick={(ev) => ev.stopPropagation()}
        style={{ maxWidth: 760, margin: "0 auto", background: "#fff", color: "#1a1a1a", borderRadius: 10, padding: "36px 42px", fontFamily: "Georgia, 'Times New Roman', serif", boxShadow: "0 24px 80px rgba(0,0,0,.4)" }}>
        <div className="print-hide" style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginBottom: 14 }}>
          <button onClick={onClose} style={{ ...ghostBtn, color: "#444", borderColor: "#ccc" }}>Cerrar</button>
          <button onClick={() => window.print()} style={primaryBtn}>🖨 Imprimir</button>
        </div>

        {err && <p>No se pudo cargar la ficha.</p>}
        {!r && !err && <p>Cargando…</p>}
        {r && (
          <>
            <div style={{ borderBottom: "3px double #222", paddingBottom: 14, marginBottom: 4 }}>
              <div style={{ fontSize: 11, letterSpacing: ".18em", textTransform: "uppercase", color: "#666" }}>Ficha genealógica</div>
              <h1 style={{ fontSize: 30, margin: "6px 0 2px", fontWeight: 700 }}>{name || "(sin nombre)"}</h1>
              <div style={{ fontSize: 12.5, color: "#444" }}>
                {r.person.sex === "M" ? "Hombre" : r.person.sex === "F" ? "Mujer" : ""}
                {r.person.names.filter((n) => !n.is_primary && (n.given || n.surname)).map((n) => ` · también «${[n.given, n.surname].filter(Boolean).join(" ")}»`).join("")}
              </div>
            </div>

            <h2 style={h2}>Hechos</h2>
            <table style={{ borderCollapse: "collapse", width: "100%" }}><tbody>
              {r.person.events.map((ev) => (
                <tr key={ev.id}>
                  <td style={{ ...td, fontWeight: 700, whiteSpace: "nowrap", width: 130 }}>{EVENT_LABEL[ev.type] ?? ev.type}{ev.family_id ? " ⚭" : ""}</td>
                  <td style={{ ...td, whiteSpace: "nowrap", width: 110 }}>{ev.date_raw ?? ev.date_year ?? "—"}</td>
                  <td style={td}>{[ev.place, ev.value, ev.family_id && ev.spouse_name ? `con ${ev.spouse_name}` : null].filter(Boolean).join(" · ") || "—"}</td>
                </tr>
              ))}
              {r.person.events.length === 0 && <tr><td style={td}>Sin hechos registrados.</td></tr>}
            </tbody></table>

            <h2 style={h2}>Familia</h2>
            <table style={{ borderCollapse: "collapse", width: "100%" }}><tbody>
              {r.person.parents.map((p) => (
                <tr key={p.id}><td style={{ ...td, fontWeight: 700, width: 130 }}>{p.sex === "F" ? "Madre" : "Padre"}</td><td style={td}>{displayName(p)}{p.birth_year ? ` (${p.birth_year}${p.death_year ? `–${p.death_year}` : ""})` : ""}</td></tr>
              ))}
              {r.families.map((f) => (
                <tr key={f.id}><td style={{ ...td, fontWeight: 700 }}>Cónyuge</td><td style={td}>{f.spouse ? displayName(f.spouse) : "(desconocido)"}{f.children_count > 0 ? ` — ${f.children_count} hijo${f.children_count !== 1 ? "s" : ""}` : ""}</td></tr>
              ))}
              {r.person.children.map((c) => (
                <tr key={c.id}><td style={{ ...td, fontWeight: 700 }}>{c.sex === "F" ? "Hija" : "Hijo"}</td><td style={td}>{displayName(c)}{c.birth_year ? ` (${c.birth_year}${c.death_year ? `–${c.death_year}` : ""})` : ""}</td></tr>
              ))}
              {r.person.parents.length + r.families.length + r.person.children.length === 0 && (
                <tr><td style={td}>Sin familiares registrados.</td></tr>
              )}
            </tbody></table>

            {r.person.notes && (<><h2 style={h2}>Notas</h2><p style={{ fontSize: 12.5, whiteSpace: "pre-wrap", margin: 0 }}>{r.person.notes}</p></>)}

            <h2 style={h2}>Fuentes</h2>
            {r.citations.length === 0 && <p style={{ fontSize: 12.5, margin: 0 }}>Sin fuentes registradas.</p>}
            <ol style={{ margin: 0, paddingLeft: 20 }}>
              {r.citations.map((c) => (
                <li key={c.id} style={{ fontSize: 12.5, marginBottom: 4 }}>
                  {[c.document_title, c.page_no ? `pág. ${c.page_no}` : null, c.summary || c.note].filter(Boolean).join(" — ") || "Registro"}
                </li>
              ))}
            </ol>

            <div style={{ marginTop: 30, paddingTop: 8, borderTop: "1px solid #ccc", fontSize: 10.5, color: "#777", display: "flex", justifyContent: "space-between" }}>
              <span>gen_suite · Estela</span>
              <span>{new Date().toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" })}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
