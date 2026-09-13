import { useEffect, useRef, useState } from "react";
import { controlStorageRecount, getStorageQuota, getStorageWrites, recheckStorageWrite, recountStorage } from "../../../api/storageQuota";
import type { StorageQuota, StorageWrite } from "../../../api/types";
import { field, primaryBtn } from "../ui";
import UnusedStorageReservationReview from "./UnusedStorageReservationReview";
import StorageWriteActionHistory from "./StorageWriteActionHistory";

const bytes = (n: number) => `${n.toLocaleString("es-ES")} bytes`;
const PHASE: Record<string, string> = { uninitialized: "Sin línea base", ready: "Cuota activa", draining: "Esperando escrituras previas", counting: "Recuento protegido en marcha" };
const WRITE_STATE: Record<string, string> = { reserved: "Reservado, sin iniciar", sending: "Enviado, pendiente de recibo", unknown: "Resultado incierto" };
export default function StorageQuotaPanel() {
  const [value, setValue] = useState<StorageQuota | null>(null);
  const [writes, setWrites] = useState<StorageWrite[]>([]);
  const [position, setPosition] = useState(0); const offset = useRef(0);
  const [review, setReview] = useState<StorageWrite | null>(null); const [auditVersion, setAuditVersion] = useState(0);
  const [reason, setReason] = useState(""); const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const revision = useRef(0); const changing = useRef(false); const mounted = useRef(true);
  const identity = useRef<{ reason: string; id: string }>();
  async function load() {
    const epoch = revision.current;
    const [next, rows] = await Promise.all([getStorageQuota(), getStorageWrites(offset.current)]);
    if (mounted.current && epoch === revision.current) { setValue(next); setWrites(rows); }
  }
  useEffect(() => {
    mounted.current = true; let stopped = false; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (!stopped && !changing.current) try { await load(); } catch (error) { if (!stopped && mounted.current) setMessage((error as Error).message); }
      if (!stopped && mounted.current) timer = setTimeout(() => void poll(), 5000);
    }
    void poll(); return () => { stopped = true; mounted.current = false; revision.current += 1; clearTimeout(timer); };
  }, []);
  async function page(target: number) {
    if (changing.current) return;
    changing.current = true; revision.current += 1; setBusy(true); setMessage("");
    try {
      const rows = await getStorageWrites(target);
      if (mounted.current) { offset.current = target; setPosition(target); setWrites(rows); }
    } catch (error) { if (mounted.current) setMessage((error as Error).message); }
    finally { changing.current = false; revision.current += 1; if (mounted.current) setBusy(false); }
  }
  async function act(action: "recount" | "retry" | "cancel" | "recheck", id?: string) {
    if (changing.current || (action === "recount" && (!confirmed || !reason.trim()))) return;
    changing.current = true; revision.current += 1; setBusy(true); setMessage("");
    try {
      if (action === "recount") {
        if (identity.current?.reason !== reason.trim()) identity.current = { reason: reason.trim(), id: crypto.randomUUID() };
        const next = await recountStorage({ client_id: identity.current.id, reason: identity.current.reason, authorize_write_pause: true, external_writers_stopped: true });
        if (mounted.current) { setValue(next); setConfirmed(false); } identity.current = undefined;
      } else if (action === "recheck") { if (id) await recheckStorageWrite(id); }
      else { const next = await controlStorageRecount(action); if (mounted.current) setValue(next); }
      await load();
      if (mounted.current) setMessage(action === "cancel" ? "Recuento cancelado; se conserva la cuota anterior, sin liberar espacio estimado."
        : action === "recheck" ? "Recibo verificado sin repetir la escritura." : "Recuento encolado. Se bloquean nuevas escrituras; las respuestas ya reservadas pueden terminar.");
    } catch (error) { if (mounted.current) setMessage((error as Error).message); }
    finally { changing.current = false; revision.current += 1; if (mounted.current) setBusy(false); }
  }
  const recounting = value && ["draining", "counting"].includes(value.phase);
  return <section aria-label="Cuota global de almacenamiento" style={{ border: "1px solid var(--line)", borderRadius: 12, padding: 16, marginTop: 20 }}>
    <h3>Cuota global de almacenamiento</h3>
    <p style={{ fontSize: 13 }}>Todas las escrituras de la suite reservan espacio. Incluye originales, copias, derivados y respuestas de IA; estas últimas se reservan antes de pagar la llamada. Los resultados S3 inciertos conservan su reserva.</p>
    {value && <>
      <h4>{PHASE[value.phase]}</h4>
      <p>Límite por espacio: {bytes(value.limit_bytes)}. Consumo conservador: {bytes(value.used_bytes)}. Reservado: {bytes(value.reserved_bytes)}.</p>
      <p>{value.pending_writes} operaciones pendientes. La cuota mide contenido S3, no espacio libre en disco, PostgreSQL, réplicas, metadatos ni backups externos.</p>
      {(value.phase === "uninitialized" || !value.location_matches) && <p role="alert">No hay una línea base válida para este destino. Los datos antiguos necesitan un recuento protegido antes de admitir escrituras.</p>}
      <p style={{ fontSize: 12 }}>Sobrescribir o borrar no descuenta bytes estimados. Un recuento terminado puede corregir ese exceso. Solo es válido si otros clientes no escriben directamente en S3 y todos los procesos de la suite usan esta versión.</p>
      {recounting ? <>
        <p role="alert">Nuevas cargas y llamadas de IA de pago bloqueadas durante el recuento.</p>
        {value.scan?.error && <p role="alert">{value.scan.error}</p>}
        {["error", "paused"].includes(value.scan?.job_status ?? "") && <button disabled={busy} onClick={() => void act("retry")}>Reanudar recuento protegido</button>}
        <button disabled={busy} onClick={() => void act("cancel")}>Cancelar recuento conservando la cuota</button>
      </> : <form onSubmit={(e) => { e.preventDefault(); void act("recount"); }} style={{ display: "grid", gap: 8 }}>
        <label>Motivo del recuento protegido <input style={field} value={reason} required maxLength={2000} disabled={busy} onChange={(e) => { setReason(e.target.value); setConfirmed(false); }} /></label>
        <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={(e) => setConfirmed(e.target.checked)} /> Acepto bloquear nuevas escrituras durante el recuento y he detenido clientes externos de S3</label>
        <button style={primaryBtn} disabled={busy || !confirmed || !reason.trim()}>Recontar y actualizar la cuota</button>
      </form>}
    </>}
    {(writes.length > 0 || position > 0) && <details><summary>Reservas y resultados pendientes</summary>
      {writes.map((write) => <div key={write.id} style={{ padding: "8px 0" }}>
        <p>Recibo {write.id} · {WRITE_STATE[write.state] ?? "Estado no reconocido"} · {bytes(write.reserved_bytes)}{write.error && ` · ${write.error}`}</p>
        {["sending", "unknown"].includes(write.state) && <button disabled={busy || write.kind === "delete"} onClick={() => void act("recheck", write.id)}>Verificar recibo sin repetir escritura</button>}
        {write.state === "reserved" && <button disabled={busy || review !== null} onClick={() => setReview(write)}>Revisar cancelación de reserva no iniciada</button>}
        {write.kind === "delete" && <p>El borrado sin versión requiere revisión del operador; no se presume espacio liberado.</p>}
      </div>)}
      <p>{writes.length ? `Operaciones ${position + 1}–${position + writes.length}.` : "Esta página ya no contiene operaciones pendientes."} El listado cambia al finalizar escrituras; vuelve al inicio para actualizarlo.</p>
      <nav aria-label="Páginas de recibos pendientes" style={{ display: "flex", gap: 8 }}>
        <button disabled={busy || position === 0} onClick={() => void page(0)}>Primeros recibos</button>
        <button disabled={busy || position === 0} onClick={() => void page(Math.max(0, position - 20))}>Recibos anteriores</button>
        <button disabled={busy || writes.length < 20} onClick={() => void page(position + 20)}>Siguientes recibos</button>
      </nav>
    </details>}
    {review && <UnusedStorageReservationReview key={review.id} write={review} onClose={() => setReview(null)} onResolved={() => {
      setReview(null); setAuditVersion((version) => version + 1); revision.current += 1;
      setMessage("Reserva no iniciada cancelada con auditoría. No se han borrado objetos ni cambiado costes de IA.");
      void load().catch((error: Error) => { if (mounted.current) setMessage(error.message); });
    }} />}
    <StorageWriteActionHistory version={auditVersion} />
    <p role="status">{message}</p>
  </section>;
}
