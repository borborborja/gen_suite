import { useCallback, useEffect, useState } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import { getConsistency, type ConsistencyReport, type ConsistencyIssue } from "../../../api/tree";
import { ghostBtn, miniBtn } from "../ui";

const CODE_LABEL: Record<string, string> = {
  birth_after_death: "Nacimiento posterior a la defunción",
  child_older_than_parent: "Hijo mayor que su padre/madre",
  parent_too_young: "Progenitor demasiado joven",
  mother_too_old: "Madre demasiado mayor",
  child_after_mother_death: "Nacido tras la muerte de la madre",
  child_before_marriage: "Nacido antes del matrimonio",
  spouse_too_young: "Casado/a demasiado joven",
  alive_over_110: "Más de 110 años sin defunción",
};

export default function ConsistenciaView() {
  const e = useEstela();
  const [report, setReport] = useState<ConsistencyReport | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setBusy(true);
    getConsistency().then(setReport).catch(() => setReport({ issues: [], counts: {}, checked_at_year: 0 }))
      .finally(() => setBusy(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const groups: { code: string; items: ConsistencyIssue[] }[] = [];
  if (report) {
    const seen = new Map<string, ConsistencyIssue[]>();
    for (const i of report.issues) {
      if (!seen.has(i.code)) { seen.set(i.code, []); groups.push({ code: i.code, items: seen.get(i.code)! }); }
      seen.get(i.code)!.push(i);
    }
  }
  const errors = report?.issues.filter((i) => i.severity === "error").length ?? 0;
  const warnings = (report?.issues.length ?? 0) - errors;

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 22 }}>
        <div>
          <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Consistencia</h1>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0" }}>
            {report === null ? "Analizando el árbol…" :
              report.issues.length === 0 ? "Sin problemas detectados: las fechas y parentescos cuadran." :
              <>
                <b style={{ color: "var(--danger)" }}>{errors} error{errors !== 1 ? "es" : ""}</b>
                {" y "}
                <b style={{ color: "var(--warn)" }}>{warnings} aviso{warnings !== 1 ? "s" : ""}</b>
                {" que conviene revisar."}
              </>}
          </p>
        </div>
        <button onClick={load} disabled={busy} style={ghostBtn}>{busy ? "Analizando…" : "↻ Reanalizar"}</button>
      </div>

      {report?.issues.length === 0 && report !== null && (
        <div style={{ background: "var(--ok-faint)", border: "1px solid var(--ok)", color: "var(--ok)", borderRadius: 12, padding: 20, fontSize: 14, fontWeight: 600 }}>
          ✓ Todo en orden
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {groups.map((g) => {
          const sev = g.items[0].severity;
          return (
            <div key={g.code} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 13, overflow: "hidden" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderBottom: "1px solid var(--line2)" }}>
                <span style={{ width: 9, height: 9, borderRadius: "50%", background: sev === "error" ? "var(--danger)" : "var(--warn)" }} />
                <span style={{ fontSize: 14, fontWeight: 600, flex: 1 }}>{CODE_LABEL[g.code] ?? g.code}</span>
                <span style={{ fontFamily: fonts.mono, fontSize: 11.5, color: "var(--muted)" }}>{g.items.length}</span>
              </div>
              <div>
                {g.items.map((i, idx) => (
                  <div key={idx} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 16px", borderBottom: idx < g.items.length - 1 ? "1px solid var(--line2)" : "none" }}>
                    <span style={{ flex: 1, fontSize: 13 }}>{i.message}</span>
                    <button onClick={() => e.openPerson(i.person_id)} style={miniBtn}>ficha ↗</button>
                    {i.related_person_id && (
                      <button onClick={() => e.openPerson(i.related_person_id!)} title={i.related_person_name ?? ""} style={miniBtn}>relacionado ↗</button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
