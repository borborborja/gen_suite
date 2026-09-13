import { useCallback, useEffect, useState } from "react";
import { cancelJob } from "../../api/jobs";
import { applyTreeImport, downloadTreeImport, getTreeImportAudit, listTreeImports, uploadTreeImport } from "../../api/tree_imports";
import type { TreeImport, TreeImportKind, TreeImportReport } from "../../api/tree_imports";
import { field, ghostBtn, primaryBtn } from "./ui";

const active = (status: string | null) => status === "queued" || status === "running";
const failed = (status: string | null) => status === "error" || status === "cancelled";
const labels: Record<string, string> = { queued: "En cola", running: "En curso", completed: "Completado", error: "Error", cancelled: "Cancelado", paused: "Pausado" };

export default function TreeImports({ onImported }: { onImported: () => void }) {
  const [items, setItems] = useState<TreeImport[]>([]);
  const [selection, setSelection] = useState<{ file: File; id: string } | null>(null);
  const [kind, setKind] = useState<TreeImportKind>("gedcom7");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState<{ item: TreeImport; report: TreeImportReport } | null>(null);
  const [ack, setAck] = useState(false);
  const refresh = useCallback(async () => {
    try { setItems(await listTreeImports()); }
    catch (err) { setError((err as Error).message); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const pending = items.some((item) => active(item.status) || active(item.apply_status));
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => { void refresh(); }, 3000);
    return () => clearInterval(timer);
  }, [pending, refresh]);
  async function action(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await refresh(); }
    catch (err) { if ((err as Error).name !== "AbortError") setError((err as Error).message); }
    finally { setBusy(false); }
  }
  const current = review && items.find((item) => item.id === review.item.id);
  return <section aria-label="Importaciones revisadas del árbol" style={{ border: "1px solid var(--line)", borderRadius: 12, padding: 20, marginBottom: 24 }}>
    <h2 style={{ marginTop: 0 }}>Importar GEDCOM 7 o GEDZIP</h2>
    <p>Primero se conserva y analiza el original privado. Solo después de revisar el informe podrás añadir personas, familias y fuentes nuevas. No fusiona árboles, no publica documentos y no ejecuta IA.</p>
    <p>GEDZIP admite JPG, PNG, WebP, TIFF de una página y PDF. Los enlaces externos no se descargan. No equivale a restaurar una copia completa de la suite.</p>
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <input type="file" accept=".ged,.gdz,.zip" aria-label="Archivo GEDCOM 7 o GEDZIP" disabled={busy} onChange={(event) => {
        const file = event.target.files?.[0]; setSelection(file ? { file, id: crypto.randomUUID() } : null);
        if (file) setKind(/\.(gdz|zip)$/i.test(file.name) ? "gedzip" : "gedcom7");
      }} />
      <select style={field} aria-label="Formato de importación" value={kind} disabled={busy} onChange={(event) => setKind(event.target.value as TreeImportKind)}>
        <option value="gedcom7">GEDCOM 7</option><option value="gedzip">GEDZIP</option>
      </select>
      <button style={primaryBtn} disabled={busy || !selection} onClick={() => void action(async () => {
        if (selection) { await uploadTreeImport(selection.file, kind, selection.id); setSelection(null); }
      })}>{busy ? "Procesando…" : "Cargar y analizar, sin incorporar"}</button>
      <button style={ghostBtn} disabled={busy} onClick={() => void refresh()}>Actualizar importaciones</button>
    </div>
    <p>La carga debe terminar antes de cerrar este panel. Después, el análisis continúa en segundo plano. Si falla la conexión, reintenta sin seleccionar de nuevo el archivo para reutilizar el mismo envío.</p>
    {error && <p role="alert">{error}</p>}
    <ul>{items.map((item) => <li key={item.id} style={{ margin: "16px 0" }}>
      {item.filename} · Análisis: {labels[item.status] ?? item.status} · {(item.byte_size / 1024**2).toFixed(2)} MB
      {item.apply_status && ` · Incorporación: ${labels[item.apply_status] ?? item.apply_status}`}
      {(item.error || item.apply_error) && <p role="alert">{item.error || item.apply_error}</p>}
      {item.result && <p>{item.result.individuals} personas · {item.result.families} familias · {item.result.sources} fuentes · {item.result.citations} citas · {item.result.documents} documentos incorporados. <button style={ghostBtn} onClick={onImported}>Actualizar árbol</button></p>}
      {item.review_ready && <button style={ghostBtn} disabled={busy} onClick={() => void action(async () => {
        const report = await getTreeImportAudit(item.id);
        if (report.input_sha256 !== item.checksum || report.input_bytes !== item.byte_size) throw new Error("El informe no corresponde al original");
        setReview({ item, report }); setAck(false);
      })}>Revisar importación</button>}{" "}
      <button style={ghostBtn} disabled={busy} onClick={() => void action(() => downloadTreeImport(item))}>Descargar original privado</button>{" "}
      {(active(item.status) || active(item.apply_status)) && <button style={ghostBtn} disabled={busy} onClick={() => void action(() => cancelJob(active(item.apply_status) ? item.apply_job_id! : item.id))}>Cancelar tarea de importación</button>}
    </li>)}</ul>
    {review && <div aria-label="Informe previo de importación" style={{ borderTop: "1px solid var(--line)" }}>
      <h3>{review.item.filename} · Revisión previa</h3>
      <p>{review.report.counts.INDI} personas · {review.report.counts.FAM} familias · {review.report.counts.SOUR} fuentes · {review.report.counts.citations_modeled}/{review.report.counts.citations} citas representables.</p>
      <p>{review.report.media.documents} documentos y {review.report.media.files} archivos. Enlaces remotos sin descargar: {review.report.media.remote_not_fetched}. Locales no adjuntos: {review.report.media.local_not_attached}.</p>
      <ul>{review.report.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      <details><summary>Transformaciones y límites ({Object.values(review.report.change_counts).reduce((sum, count) => sum + count, 0)})</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(review.report.change_counts, null, 2)}</pre>
        <p>Los códigos técnicos y detalles están en el informe JSON completo; el original permite recuperar la información no representada.</p>
      </details>
      <button style={ghostBtn} onClick={() => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(review.report, null, 2)], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = `${review.item.id}-import-audit.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }}>Guardar informe previo JSON</button>
      {current && (!current.apply_status || failed(current.apply_status)) && <>
        <p><label><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} /> He revisado las limitaciones y acepto añadir registros privados nuevos, sin fusionar ni sustituir los existentes.</label></p>
        <button style={primaryBtn} disabled={busy || !ack} onClick={() => void action(async () => {
          await applyTreeImport(review.item.id, review.report, failed(current.apply_status)); setAck(false);
        })}>{failed(current.apply_status) ? "Reintentar incorporación revisada" : "Incorporar árbol revisado"}</button>{" "}
      </>}
      <button style={ghostBtn} onClick={() => setReview(null)}>Cerrar revisión</button>
    </div>}
  </section>;
}
