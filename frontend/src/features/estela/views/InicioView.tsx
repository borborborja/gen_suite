import { useEffect, useState } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import { ArrowRight } from "../icons";
import { getStats, type TreeStats } from "../../../api/tree";
import { listJobs, type JobItem } from "../../../api/jobs";

const JOB_LABEL: Record<string, string> = {
  transcription: "Transcripción", extraction: "Extracción", embed_mentions: "Embeddings de menciones",
  reembed_corpus: "Re-embeber corpus", linkage: "Descubrimiento", rasterize: "Rasterizado de PDF",
  embedding: "Embeddings", embed_document: "Embeddings",
};
const STATUS_COLOR: Record<string, string> = {
  completed: "var(--ok)", running: "var(--accent)", queued: "var(--warn)", error: "var(--danger)", cancelled: "var(--muted)",
};
function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  return `hace ${Math.floor(s / 86400)} d`;
}

export default function InicioView() {
  const e = useEstela();
  const [stats, setStats] = useState<TreeStats | null>(null);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  useEffect(() => {
    getStats().then(setStats).catch(() => setStats(null));
    listJobs().then((j) => setJobs(j.slice(0, 6))).catch(() => setJobs([]));
  }, []);

  const cards = [
    { label: "Personas", value: stats ? stats.persons.toLocaleString() : "—", sub: stats ? `${stats.families.toLocaleString()} familias` : "", color: "var(--fg)" },
    { label: "Eventos", value: stats ? stats.events.toLocaleString() : "—", sub: "fechas y lugares", color: "var(--fg)" },
    { label: "Lugares", value: stats ? stats.places.toLocaleString() : "—", sub: "parroquias y pueblos", color: "var(--fg)" },
    { label: "Pendientes", value: String(e.pendingCount), sub: "por revisar", color: e.pendingCount > 0 ? "var(--accent)" : "var(--ok)" },
  ];

  const hasPending = e.pendingCount > 0;

  return (
    <section style={{ padding: "40px 48px 64px", maxWidth: 1180 }}>
      <div style={{ fontFamily: fonts.mono, fontSize: 11, letterSpacing: ".16em", color: "var(--muted)", marginBottom: 8 }}>PANEL</div>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, flexWrap: "wrap", marginBottom: 36 }}>
        <div>
          <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 44, lineHeight: 1.05, letterSpacing: "-.02em", margin: 0, maxWidth: 640 }}>
            {hasPending
              ? <>Tienes <span style={{ color: "var(--accent)" }}>{e.pendingCount} posibles parientes</span> por revisar</>
              : <>Tu archivo, <span style={{ color: "var(--accent)" }}>conectado a tu árbol</span></>}
          </h1>
          <p style={{ color: "var(--muted)", fontSize: 16, margin: "14px 0 0", maxWidth: 560, lineHeight: 1.5 }}>
            {hasPending
              ? "Cada coincidencia llega con su prueba y su porqué — tú decides qué entra en tu árbol."
              : "Sube un libro en la Biblioteca y busca registros de tus antepasados. Cada hallazgo llega con su fuente."}
          </p>
        </div>
        <button onClick={() => e.go(hasPending ? "descubrimientos" : "biblioteca")} style={{ flex: "none", display: "inline-flex", alignItems: "center", gap: 9, background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "14px 22px", fontFamily: fonts.sans, fontSize: 15, fontWeight: 600, cursor: "pointer", boxShadow: "0 6px 20px rgba(217,83,30,.32)" }}>
          {hasPending ? "Revisar descubrimientos" : "Añadir un libro"} <ArrowRight size={18} />
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 18, marginBottom: 34 }}>
        {cards.map((c) => (
          <div key={c.label} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: "20px 22px" }}>
            <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".12em", color: "var(--muted)", textTransform: "uppercase" }}>{c.label}</div>
            <div style={{ fontFamily: fonts.serif, fontSize: 34, fontWeight: 600, marginTop: 10, letterSpacing: "-.02em", color: c.color }}>{c.value}</div>
            <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 4 }}>{c.sub}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 22 }}>
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 16, fontWeight: 600 }}>Actividad reciente</h3>
          {jobs.length === 0 && <p style={{ color: "var(--muted)", fontSize: 13.5 }}>Aún no hay procesos. Sube un libro o lanza un descubrimiento.</p>}
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {jobs.map((j) => {
              const p = j.progress as { done?: number; total?: number } | null;
              return (
                <div key={j.id} style={{ display: "flex", alignItems: "center", gap: 13, padding: "10px 0", borderBottom: "1px solid var(--line2)" }}>
                  <span style={{ width: 9, height: 9, borderRadius: "50%", marginTop: 1, flex: "none", background: STATUS_COLOR[j.status] ?? "var(--muted)", ...(j.status === "running" ? { animation: "estPulse 1.4s infinite" } : {}) }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13.5 }}>{JOB_LABEL[j.type] ?? j.type} <span style={{ color: STATUS_COLOR[j.status] ?? "var(--muted)", fontSize: 12 }}>· {j.status === "running" && p ? `${p.done ?? 0}/${p.total ?? "?"}` : j.status}</span></div>
                    <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginTop: 2 }}>{ago(j.finished_at || j.created_at)}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        <Quick title="Biblioteca" desc="Sube libros (PDF o imágenes), transcríbelos y extrae las actas." onClick={() => e.go("biblioteca")} cta="Ir a la Biblioteca" />
      </div>
    </section>
  );
}

function Quick({ title, desc, onClick, cta }: { title: string; desc: string; onClick: () => void; cta: string }) {
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
      <h3 style={{ margin: "0 0 8px", fontSize: 16, fontWeight: 600 }}>{title}</h3>
      <p style={{ color: "var(--muted)", fontSize: 13.5, margin: "0 0 16px", lineHeight: 1.5 }}>{desc}</p>
      <button onClick={onClick} style={{ background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, padding: "10px 16px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer" }}>{cta}</button>
    </div>
  );
}
