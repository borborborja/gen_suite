import { useEffect, useRef, useState } from "react";
import { controlStorageInventory, createStorageInventory, getStorageInventory, listStorageInventories } from "../../../api/storageInventory";
import type { StorageInventory, StorageInventorySummary } from "../../../api/types";
import { field, primaryBtn } from "../ui";

const STATES: Record<string, string> = { queued: "En cola", running: "En marcha", completed: "Recorrido terminado", error: "Incompleto", paused: "Pausado", cancelled: "Cancelado" };
const PHASES: Record<string, string> = { versions: "Versiones de objetos", uploads: "Cargas multipart", parts: "Partes cargadas", completed: "Recorrido terminado" };
const CATEGORIES: Record<string, string> = { documents: "Documentos y páginas", exports: "Exportaciones", staging: "Temporales de importación", media: "Imágenes de personas", ai_responses: "Respuestas de IA", other: "Otros objetos" };
const bytes = (value: number) => `${value.toLocaleString("es-ES")} bytes`;
const date = (value: string | null) => value ? new Date(value).toLocaleString("es-ES") : "—";

export default function StorageInventoryPanel() {
  const [rows, setRows] = useState<StorageInventorySummary[]>([]);
  const [current, setCurrent] = useState<StorageInventory | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const epoch = useRef(0);
  const mounted = useRef(true);
  const changing = useRef(false);
  const requestKey = useRef<{ reason: string; id: string }>();
  useEffect(() => {
    mounted.current = true;
    const revision = epoch.current;
    void listStorageInventories().then((value) => {
      if (mounted.current && revision === epoch.current) { setRows(value); setHasMore(value.length === 20); }
    }).catch((error: Error) => { if (mounted.current) setMessage(error.message); });
    return () => { mounted.current = false; epoch.current += 1; };
  }, []);
  useEffect(() => {
    if (!selected) return;
    let live = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const revision = epoch.current;
      if (!changing.current) {
        try {
          const value = await getStorageInventory(selected!);
          if (live && revision === epoch.current) {
            setCurrent(value); setRows((old) => old.map((row) => row.id === value.id ? value : row));
          }
        } catch (error) { if (live && revision === epoch.current) setMessage((error as Error).message); }
      }
      if (live) timer = setTimeout(() => void poll(), 5000);
    }
    void poll(); return () => { live = false; clearTimeout(timer); };
  }, [selected]);
  async function act(action: "create" | "cancel" | "retry") {
    if (changing.current || (action === "create" && !reason.trim())) return;
    changing.current = true; epoch.current += 1; setBusy(true); setMessage("");
    try {
      let value: StorageInventory;
      if (action === "create") {
        if (requestKey.current?.reason !== reason.trim()) requestKey.current = { reason: reason.trim(), id: crypto.randomUUID() };
        value = await createStorageInventory({ client_id: requestKey.current.id, reason: requestKey.current.reason });
        requestKey.current = undefined;
      } else {
        if (!current) return;
        value = await controlStorageInventory(current.id, action);
      }
      if (!mounted.current) return;
      setSelected(value.id); setCurrent(value);
      setRows((old) => [value, ...old.filter((row) => row.id !== value.id)]);
      setMessage(action === "cancel" ? "Inventario cancelado. Se conserva el resultado parcial y no se borra ningún objeto."
        : "Inventario encolado. Solo lee metadatos; no modifica objetos ni cuotas y no consume IA.");
    } catch (error) { if (mounted.current) setMessage((error as Error).message); }
    finally { changing.current = false; epoch.current += 1; if (mounted.current) setBusy(false); }
  }
  async function more() {
    if (busy) return;
    setBusy(true); const revision = epoch.current;
    try {
      const value = await listStorageInventories(rows.length);
      if (mounted.current && revision === epoch.current) {
        setRows((old) => [...old, ...value.filter((row) => !old.some((known) => known.id === row.id))]);
        setHasMore(value.length === 20);
      }
    } catch (error) { if (mounted.current) setMessage((error as Error).message); }
    finally { if (mounted.current) setBusy(false); }
  }
  return <section aria-label="Inventario de almacenamiento" style={{ border: "1px solid var(--line)", borderRadius: 12, padding: 16, marginTop: 20 }}>
    <h3>Inventario de almacenamiento</h3>
    <p style={{ fontSize: 13 }}>Cuenta el contenido observado en los objetos privados y públicos de tu espacio, incluidas versiones antiguas, copias y partes de cargas inacabadas. También incluye objetos sin referencia en la base de datos.</p>
    <p style={{ fontSize: 13 }}>No es una cuota activa, una copia de seguridad ni una comprobación de integridad. El recorrido no bloquea subidas: con cambios concurrentes puede contar objetos que ya no están u omitir otros nuevos. No mide PostgreSQL, réplicas ni el espacio libre del disco.</p>
    <form onSubmit={(event) => { event.preventDefault(); void act("create"); }} style={{ display: "grid", gap: 8 }}>
      <label>Motivo del inventario <input required maxLength={2000} style={field} value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} /></label>
      <span style={{ fontSize: 12 }}>Solo administración puede iniciarlo. No consume IA; el proveedor S3 puede cobrar las peticiones de listado. No se eliminan versiones ni se interrumpen cargas.</span>
      <button style={primaryBtn} disabled={busy || !reason.trim()}>Inventariar sin modificar archivos</button>
    </form>
    <div style={{ display: "grid", gap: 6, marginTop: 12 }}>
      {rows.map((row) => <button key={row.id} disabled={busy} onClick={() => { epoch.current += 1; setSelected(row.id); setCurrent(null); setMessage(""); }}>
        {row.reason} · {STATES[row.job_status ?? ""] ?? "Sin trabajo"}
      </button>)}
      {hasMore && <button disabled={busy} onClick={() => void more()}>Más inventarios</button>}
    </div>
    {current && <div style={{ marginTop: 16 }}>
      <h4>{current.reason} · {STATES[current.job_status ?? ""] ?? "Sin trabajo"}</h4>
      <p>Fase: {PHASES[current.phase]} · {current.bucket_role === "private" ? "Privado" : "Público"}</p>
      <p style={{ fontSize: 12 }}>Inicio: {date(current.started_at)} · Final: {date(current.completed_at)} · Responsable: {current.created_by}</p>
      {!current.storage_configuration_matches && <p role="alert">Inventario histórico de otro destino o invalidado tras una restauración. Inicia uno nuevo; no se puede reanudar este.</p>}
      {current.error && <p role="alert">{current.error}</p>}
      <p>{current.listing_finished ? "Contenido observado durante el recorrido" : "Recuento parcial, recorrido sin terminar"}: {bytes(current.observed_payload_bytes)}.</p>
      <p>Versiones vigentes observadas: {bytes(current.bytes.current_versions)}. Versiones antiguas: {bytes(current.bytes.noncurrent_versions)}. Partes multipart: {bytes(current.bytes.multipart_parts)}.</p>
      <p>Marcadores de borrado: {current.counts.delete_markers}. Se cuentan, pero su coste de metadatos no está incluido en los bytes de contenido.</p>
      {current.counts.uninspected_uploads > 0 && <p role="alert">Hay {current.counts.uninspected_uploads} cargas sin inspección completa; pueden haber terminado o desaparecido durante el recorrido. Su tamaño no se presume cero.</p>}
      {current.groups.length > 0 && <div style={{ overflowX: "auto" }}><table style={{ width: "100%", fontSize: 13 }}>
        <caption>Contenido observado por categoría (clasificación por ruta, no verificación documental)</caption>
        <thead><tr><th>Almacén</th><th>Categoría</th><th>Bytes de contenido</th><th>Versiones</th></tr></thead>
        <tbody>{current.groups.map((group) => <tr key={`${group.bucket_role}:${group.category}`}>
          <td>{group.bucket_role === "private" ? "Privado" : "Público"}</td><td>{CATEGORIES[group.category] ?? group.category}</td>
          <td>{bytes(group.payload_bytes)}</td><td>{group.versions}</td>
        </tr>)}</tbody>
      </table></div>}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        {["error", "paused"].includes(current.job_status ?? "") && current.storage_configuration_matches && <button disabled={busy} onClick={() => void act("retry")}>Reanudar el mismo inventario</button>}
        {["queued", "running", "paused", "error"].includes(current.job_status ?? "") && <button disabled={busy} onClick={() => void act("cancel")}>Cancelar solo el inventario</button>}
      </div>
    </div>}
    <p role="status">{message}</p>
  </section>;
}
