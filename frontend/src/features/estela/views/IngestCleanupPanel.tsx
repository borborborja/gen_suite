import { useEffect, useRef, useState } from "react";
import { approveIngestCleanup, getIngestCleanup, previewIngestCleanup, retryIngestCleanup } from "../../../api/ingestion";
import { cancelJob } from "../../../api/jobs";
import type { IngestCleanup, IngestCleanupPreview, IngestSession } from "../../../api/types";

const size = (bytes: number) => `${(bytes / 1024 ** 2).toLocaleString("es", { maximumFractionDigits: 2 })} MiB`;
const PHASE: Record<string, string> = { verify_originals: "Comprobando originales conservados", inventory: "Inventariando versiones temporales", delete: "Eliminando temporales inventariados", verify_empty: "Verificando staging vacío", completed: "Limpieza completada" };
type Props = { session: IngestSession; disabled: boolean; onChanged: () => Promise<void> };

export default function IngestCleanupPanel(props: Props) {
  const [open, setOpen] = useState(false);
  return <details style={{ marginTop: 16 }} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Liberar temporales de esta sesión (administración)</summary>
    {open && <Editor key={props.session.id} {...props} />}
  </details>;
}

function Editor({ session, disabled, onChanged }: Props) {
  const [current, setCurrent] = useState<IngestCleanup | null>(null);
  const [preview, setPreview] = useState<IngestCleanupPreview | null>(null);
  const [reason, setReason] = useState("");
  const [localConfirmed, setLocalConfirmed] = useState(false);
  const [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState(false);
  const epoch = useRef(0);
  const working = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    let active = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const revision = epoch.current;
      try {
        if (!working.current) {
          const value = await getIngestCleanup(session.id);
          if (active && revision === epoch.current) { setCurrent(value); setLoaded(true); }
        }
      } catch (error) { if (active) setMessage((error as Error).message); }
      if (active) timer = setTimeout(() => void poll(), 5000);
    }
    void poll(); return () => { active = false; mounted.current = false; clearTimeout(timer); };
  }, [session.id]);
  function reset() { setPreview(null); setLocalConfirmed(false); setDeleteConfirmed(false); }
  function begin() { working.current = true; epoch.current += 1; setBusy(true); setMessage(""); }
  function finish() { working.current = false; epoch.current += 1; if (mounted.current) setBusy(false); }
  async function compare() {
    if (busy || disabled) return;
    begin(); reset();
    try { const value = await previewIngestCleanup(session.id); if (mounted.current) setPreview(value); }
    catch (error) { setMessage((error as Error).message); }
    finally { finish(); }
  }
  async function approve() {
    if (!preview || !localConfirmed || !deleteConfirmed || !reason.trim() || busy || disabled) return;
    begin();
    try {
      const value = await approveIngestCleanup(session.id, { preview_hash: preview.preview_hash, reason,
        authorize_permanent_staging_deletion: true, local_originals_confirmed: true });
      if (!mounted.current) return;
      setCurrent(value);
      reset(); setMessage("Sesión cerrada para limpieza. Ya no admite envíos. Los originales publicados no se eliminan.");
      await onChanged();
    } catch (error) { setMessage((error as Error).message); reset(); }
    finally { finish(); }
  }
  async function retry() {
    if (busy || disabled) return;
    begin();
    try { const value = await retryIngestCleanup(session.id); if (!mounted.current) return; setCurrent(value); await onChanged(); }
    catch (error) { setMessage((error as Error).message); }
    finally { finish(); }
  }
  async function stopVerification() {
    if (!session.job_id || busy || disabled) return;
    begin(); reset();
    try { await cancelJob(session.job_id); if (!mounted.current) return; await onChanged(); setMessage("Cancelación solicitada. Espera a que termine el trabajador antes de comparar la limpieza."); }
    catch (error) { setMessage((error as Error).message); }
    finally { finish(); }
  }
  return <div style={{ fontSize: 13 }}>
    <p>La antigüedad no borra nada automáticamente. Esta acción elimina permanentemente los fragmentos temporales y todas sus versiones, no los libros, originales publicados ni citas. Con una carga incompleta perderás la posibilidad de reanudarla; conserva primero tus archivos locales.</p>
    {session.job_id && ["queued", "running"].includes(session.job_status ?? "") && <button disabled={busy || disabled} onClick={() => void stopVerification()}>Cancelar verificación sin borrar originales</button>}
    {loaded && !current && !["cleaning", "discarded"].includes(session.status) && <fieldset disabled={busy || disabled} style={{ border: 0, padding: 0 }}>
      <label>Motivo de la limpieza <textarea maxLength={2000} value={reason} onChange={(e) => { setReason(e.target.value); reset(); }} /></label>
      <button disabled={!reason.trim()} onClick={() => void compare()}>Comparar limpieza antes de autorizar</button>
      {preview && <>
        <p>{preview.mode === "compact" ? "Retirar temporales de colección completada" : "Descartar carga incompleta"}: {preview.title} · {preview.files} archivos · {preview.published_books} libros publicados.</p>
        <p>Recibido en el manifiesto: {size(preview.received_bytes)}. Las versiones antiguas pueden ocupar más. Reserva de admisión liberable al terminar: {size(preview.released_reservation_bytes)}; conservada: {size(preview.retained_reservation_bytes)}. No es una medida de toda la ocupación física.</p>
        <label style={{ display: "block" }}><input type="checkbox" checked={localConfirmed} onChange={(e) => setLocalConfirmed(e.target.checked)} /> Conservo y he comprobado mis originales locales y sus copias.</label>
        <label style={{ display: "block" }}><input type="checkbox" checked={deleteConfirmed} onChange={(e) => setDeleteConfirmed(e.target.checked)} /> Autorizo eliminar permanentemente solo los temporales de esta sesión y todas sus versiones; no podré reanudar esta carga.</label>
        <button disabled={!localConfirmed || !deleteConfirmed || !reason.trim()} onClick={() => void approve()}>Cerrar sesión y eliminar temporales</button>
      </>}
    </fieldset>}
    {current && <div>
      <p>{PHASE[current.phase] ?? current.phase} · {current.deleted_objects} / {current.planned_objects} versiones · {size(current.deleted_bytes)} confirmados ausentes. Inventario {current.inventory_epoch}.</p>
      <p>Autorizado por {current.created_by}: {current.reason}. La cuota no se libera hasta verificar el final; una reserva de originales ya procesados se conserva.</p>
      {current.error && <p role="alert">{current.error}</p>}
      {current.phase !== "completed" && !["queued", "running"].includes(current.job_status) && <button disabled={busy || disabled} onClick={() => void retry()}>Reintentar la misma limpieza autorizada</button>}
    </div>}
    <p role="status">{message}</p>
  </div>;
}
