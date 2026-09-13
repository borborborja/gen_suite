import { useCallback, useEffect, useState } from "react";
import { cancelJob } from "../../api/jobs";
import { downloadGedcomAudit } from "../../api/tree";
import { createTreeExport, downloadTreeExport, getTreeExportAudit, listTreeExports } from "../../api/tree_exports";
import type { GedcomExportAudit, TreeExport, TreeExportKind } from "../../api/types";
import { field, ghostBtn, primaryBtn } from "./ui";

const labels: Record<TreeExportKind, string> = { gedcom551: "GEDCOM 5.5.1", gedcom7: "GEDCOM 7", gedzip: "GEDZIP con documentos" };
const statuses: Record<string, string> = { queued: "En cola", running: "En curso", completed: "Completada", error: "Error", cancelled: "Cancelada", paused: "Pausada" };

export default function TreeExports() {
  const [items, setItems] = useState<TreeExport[]>([]);
  const [kind, setKind] = useState<TreeExportKind>("gedcom7");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewed, setReviewed] = useState<{ item: TreeExport; report: GedcomExportAudit } | null>(null);
  const refresh = useCallback(async () => {
    try { setItems(await listTreeExports()); }
    catch (err) { setError((err as Error).message); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const active = items.some((item) => item.status === "queued" || item.status === "running");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => { void refresh(); }, 3000);
    return () => clearInterval(timer);
  }, [active, refresh]);

  async function action(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await refresh(); }
    catch (err) { if ((err as Error).name !== "AbortError") setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return <section aria-label="Exportaciones privadas del árbol" style={{ border: "1px solid var(--line)", borderRadius: 12, padding: 20, marginBottom: 24 }}>
    <h2 style={{ marginTop: 0 }}>Exportaciones privadas del árbol</h2>
    <p>Genera una versión en segundo plano. Puedes cerrar este panel y volver después. La descarga incluye personas privadas o vivas; no es una publicación ni una copia de seguridad completa.</p>
    <p>GEDZIP incluye los originales retenidos de tu biblioteca, también los aún no citados. No incluye documentos de otros espacios, revisiones ni inferencias. Para volver a incorporarlo, utiliza la importación revisada GEDCOM 7/GEDZIP; no es una restauración completa.</p>
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <select aria-label="Formato de exportación" style={field} value={kind} onChange={(event) => setKind(event.target.value as TreeExportKind)} disabled={busy}>
        {Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      <button style={primaryBtn} disabled={busy || active} onClick={() => void action(() => createTreeExport(kind))}>Generar exportación privada</button>
      <button style={ghostBtn} disabled={busy} onClick={() => void refresh()}>Actualizar exportaciones</button>
    </div>
    {error && <p role="alert" style={{ color: "var(--danger)" }}>{error}</p>}
    {items.length === 0 && <p>No hay exportaciones. Al generar una nueva, revisa su informe antes de descargarla.</p>}
    <ul>{items.map((item) => <li key={item.id} style={{ margin: "16px 0" }}>
      {labels[item.kind]} · {statuses[item.status] ?? item.status} · {new Date(item.created_at).toLocaleString()}
      {item.progress?.total ? ` · ${item.progress.done ?? 0}/${item.progress.total}` : ""}
      {item.byte_size != null && ` · ${(item.byte_size / 1024**2).toFixed(2)} MB`}
      {item.error && <p role="alert">{item.error}</p>}
      {item.status === "completed" && <button style={{ ...ghostBtn, marginLeft: 12 }} disabled={busy} onClick={() => void action(async () => {
        setReviewed({ item, report: await getTreeExportAudit(item.id) });
      })}>Revisar informe</button>}
      {(item.status === "queued" || item.status === "running") && <button style={{ ...ghostBtn, marginLeft: 12 }} disabled={busy} onClick={() => void action(() => cancelJob(item.id))}>Cancelar exportación</button>}
    </li>)}</ul>
    {reviewed && <div aria-label="Informe de la exportación" style={{ borderTop: "1px solid var(--line)", paddingTop: 16 }}>
      <h3>{reviewed.report.format} · Informe del archivo generado</h3>
      <p>{reviewed.report.counts.persons} personas · {reviewed.report.counts.families} familias · {reviewed.report.counts.sources} fuentes · {reviewed.report.counts.citations_exported}/{reviewed.report.counts.citations} citas.</p>
      <p>Documentos incluidos: {reviewed.report.media_documents ?? 0} · Archivos originales: {reviewed.report.media_files ?? 0}.</p>
      {reviewed.report.snapshot && <p>Instantánea: {reviewed.report.snapshot.captured_at}. Los cambios posteriores del árbol no cambian este archivo.</p>}
      <ul>{reviewed.report.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      {reviewed.report.conversions && <p>Fechas conservadas como texto: {reviewed.report.conversions.date_phrases} · Estructuras antiguas en extensiones: {reviewed.report.conversions.legacy_structures}.</p>}
      <details><summary>Registros excluidos</summary><ul>{Object.entries(reviewed.report.excluded_records).map(([name, count]) => <li key={name}>{name}: {count}</li>)}</ul></details>
      <p style={{ overflowWrap: "anywhere", fontSize: 12 }}>SHA-256 del archivo: {reviewed.report.artifact_sha256}</p>
      <button style={ghostBtn} onClick={() => downloadGedcomAudit(reviewed.report)}>Guardar informe JSON</button>{" "}
      <button style={primaryBtn} disabled={busy} onClick={() => void action(() => downloadTreeExport(reviewed.item, reviewed.report))}>Descargar archivo privado revisado</button>{" "}
      <button style={ghostBtn} onClick={() => setReviewed(null)}>Cerrar informe</button>
    </div>}
  </section>;
}
