import { useEffect, useRef, useState } from "react";
import { appendIngestManifest, completeIngestSession, createIngestSession, getIngestSession,
  listIngestBooks, listIngestSessions, planCollection, resumeIngestFile, sealIngestManifest } from "../../../api/ingestion";
import type { IngestBook, IngestFile, IngestOptions, IngestSession } from "../../../api/types";
import IngestCleanupPanel from "./IngestCleanupPanel";

const labels: Record<string, string> = { manifest: "manifestando", uploading: "pendiente de envío", processing: "verificando originales",
  completed: "completado", partial: "con incidencias", error: "requiere revisión", cancelled: "pausado", cleaning: "limpiando temporales", discarded: "carga descartada" };
const size = (bytes: number) => `${(bytes / 1024 ** 2).toLocaleString("es", { maximumFractionDigits: 1 })} MiB`;

export default function IngestPanel({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [sessions, setSessions] = useState<IngestSession[]>([]);
  const [inactiveOnly, setInactiveOnly] = useState(false);
  const [session, setSession] = useState<IngestSession | null>(null);
  const [books, setBooks] = useState<IngestBook[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [title, setTitle] = useState("Colección de manuscritos");
  const [options, setOptions] = useState<IngestOptions>({ source_origin: "own_photo", may_contain_living: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const controller = useRef<AbortController | null>(null);
  const clientId = useRef<string | null>(null);
  const selectedSession = useRef<string | null>(null);

  async function refreshList() { setSessions(await listIngestSessions(0, inactiveOnly ? 30 : undefined)); }
  async function loadBooks(id: string) {
    const all: IngestBook[] = [];
    let after: string | undefined;
    while (true) {
      const batch = await listIngestBooks(id, after);
      all.push(...batch);
      if (batch.length < 1000) break;
      after = batch[batch.length - 1].id;
    }
    if (selectedSession.current === id) setBooks(all);
    return all;
  }
  useEffect(() => { void refreshList().catch((err: Error) => setError(err.message)); return () => controller.current?.abort(); }, [inactiveOnly]);
  useEffect(() => {
    if (!session || !["processing", "cleaning"].includes(session.status)) return;
    let alive = true;
    const poll = async () => {
      try {
        const current = await getIngestSession(session.id);
        if (!alive) return;
        setSession(current);
        if (!["processing", "cleaning"].includes(current.status)) { await loadBooks(current.id); onChanged(); void refreshList(); }
      } catch (err) { if (alive) setError((err as Error).message); }
    };
    const timer = window.setInterval(() => { void poll(); }, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [session?.id, session?.status, onChanged]);

  async function choose(row: IngestSession) {
    selectedSession.current = row.id;
    setError(""); setBusy(true); setFiles([]); setProgress("");
    try { const current = await getIngestSession(row.id); await loadBooks(row.id); setSession(current); setTitle(current.title); setOptions(current.options); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  function chooseFiles(selected: FileList | null) {
    if (!selected) return;
    setFiles(Array.from(selected).filter((file) => /\.(pdf|jpe?g|png|tiff?|webp)$/i.test(file.name)));
    setError(""); clientId.current = null;
  }

  async function upload() {
    setError(""); setBusy(true);
    const abort = new AbortController(); controller.current = abort;
    let current = session;
    try {
      const planned = planCollection(files, options, books);
      const total = planned.reduce((sum, p) => sum + p.file.size, 0);
      if (current && (current.expected_files !== files.length || current.reserved_bytes !== total)) throw new Error("Vuelve a seleccionar la colección completa, con las mismas rutas, tamaños y archivos del manifiesto original.");
      if (!current) {
        if (!globalThis.crypto?.randomUUID) throw new Error("Abre la aplicación mediante HTTPS o localhost");
        clientId.current ||= crypto.randomUUID();
        current = await createIngestSession({ client_id: clientId.current, title, expected_files: files.length, reserved_bytes: total, options }, abort.signal);
        selectedSession.current = current.id;
        setSession(current);
      }
      const registered = new Map<string, IngestFile>();
      for (let start = 0; start < planned.length; start += 500) {
        setProgress(`Guardando manifiesto: ${Math.min(start + 500, planned.length)} / ${planned.length} archivos`);
        const rows = await appendIngestManifest(current.id, planned.slice(start, start + 500).map((p) => p.entry), abort.signal);
        for (const row of rows) registered.set(row.path, row);
      }
      current = await sealIngestManifest(current.id, abort.signal); setSession(current);
      let uploaded = current.received_bytes;
      for (let i = 0; i < planned.length; i++) {
        const { entry, file } = planned[i];
        await resumeIngestFile(current, registered.get(entry.path)!, file, abort.signal, (sent, offset) => {
          uploaded += sent;
          setProgress(`${i + 1} / ${planned.length} · ${entry.path} · verificado ${size(offset)} · enviado ${size(uploaded)} / ${size(total)}`);
        });
      }
      current = await completeIngestSession(current.id, abort.signal); setSession(current);
      setProgress("Originales recibidos. La verificación y publicación de libros continúa en segundo plano.");
      await loadBooks(current.id); await refreshList(); onChanged();
    } catch (err) {
      setError((err as Error).name === "AbortError" ? "Envío pausado. Los fragmentos confirmados se conservan; puedes reanudar aquí o tras volver a abrir la aplicación." : (err as Error).message);
      if (current) { await getIngestSession(current.id).then(setSession).catch(() => {}); await loadBooks(current.id).catch(() => {}); }
      await refreshList().catch(() => {});
    } finally { setBusy(false); controller.current = null; }
  }

  async function retry() {
    if (!session) return;
    setBusy(true); setError("");
    try { setSession(await completeIngestSession(session.id)); await loadBooks(session.id); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  const field = { background: "var(--bg)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 6, padding: 8 };
  return <section aria-label="Importación masiva reanudable" style={{ padding: 22, border: "1px solid var(--line)", borderRadius: 14, marginBottom: 22 }}>
    <h3>Importar una colección y reanudar envíos</h3>
    <p>Un libro por carpeta de imágenes; cada PDF es un libro. Los originales se conservan privados. Los envíos se guardan en fragmentos de 8 MiB, sin llamadas de IA.</p>
    {error && <p role="alert" style={{ color: "var(--danger)" }}>{error}</p>}
    <label><input type="checkbox" disabled={busy} checked={inactiveOnly} onChange={(e) => setInactiveOnly(e.target.checked)} /> Mostrar cargas sin actividad durante 30 días (no se borran solas)</label>
    <label>Sesión guardada <select disabled={busy} style={field} value={session?.id || ""} onChange={(ev) => {
      const selected = sessions.find((row) => row.id === ev.target.value);
      if (selected) void choose(selected); else { selectedSession.current = null; setSession(null); setBooks([]); setFiles([]); setProgress(""); clientId.current = null; }
    }}><option value="">Nueva colección</option>{sessions.map((row) => <option key={row.id} value={row.id}>{row.title} · {labels[row.status] || row.status} · {row.id.slice(0, 8)}</option>)}</select></label>
    {sessions.length >= 100 && <button disabled={busy} onClick={() => { void listIngestSessions(sessions.length, inactiveOnly ? 30 : undefined).then((rows) => setSessions((old) => [...old, ...rows])).catch((err: Error) => setError(err.message)); }}>Sesiones anteriores</button>}
    {session && <p>Sesión {session.id} · {labels[session.status] || session.status}{session.job_status === "cancelled" ? " (trabajo cancelado; puedes reintentar)" : ""} · {size(session.received_bytes)} / {size(session.reserved_bytes)}</p>}
    {!session && <div style={{ display: "flex", gap: 12, flexWrap: "wrap", margin: "14px 0" }}>
      <label>Título <input style={field} disabled={busy} value={title} onChange={(ev) => setTitle(ev.target.value)} maxLength={512} /></label>
      <label>Origen <select style={field} disabled={busy} value={options.source_origin} onChange={(ev) => setOptions({ ...options, source_origin: ev.target.value })}>
        <option value="own_photo">Foto propia</option><option value="public_archive">Archivo público</option><option value="licensed">Licencia</option><option value="familysearch">FamilySearch (restringido)</option></select></label>
      <label>Municipio <input style={field} disabled={busy} value={options.municipality || ""} onChange={(ev) => setOptions({ ...options, municipality: ev.target.value || null })} /></label>
      <label>Tipo <select style={field} disabled={busy} value={options.record_type || ""} onChange={(ev) => setOptions({ ...options, record_type: ev.target.value || null })}><option value="">Sin especificar</option><option value="baptism">Bautismos</option><option value="marriage">Matrimonios</option><option value="death">Defunciones</option></select></label>
      <label><input type="checkbox" disabled={busy} checked={options.may_contain_living || false} onChange={(ev) => setOptions({ ...options, may_contain_living: ev.target.checked })} />Puede contener personas vivas</label>
    </div>}
    {(!session || ["manifest", "uploading"].includes(session.status)) && <>
      <p>Para reanudar después de cerrar el navegador, selecciona de nuevo la carpeta completa. Se comprobarán los fragmentos locales sin volver a enviar los ya confirmados.</p>
      <label>Carpeta <input type="file" multiple disabled={busy} ref={(node) => node?.setAttribute("webkitdirectory", "")} onChange={(ev) => chooseFiles(ev.target.files)} /></label>
      <label> o archivos <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.webp" disabled={busy} onChange={(ev) => chooseFiles(ev.target.files)} /></label>
      <p>{files.length.toLocaleString("es")} archivos seleccionados · {size(files.reduce((sum, file) => sum + file.size, 0))}</p>
      <button style={field} disabled={busy || !files.length || !title.trim()} onClick={() => { void upload(); }}>{session ? "Reanudar envío" : "Guardar manifiesto y enviar"}</button>
    </>}
    {progress && <p role="status" aria-live="polite">{progress}</p>}
    {session && session.received_bytes === session.reserved_bytes && ["uploading", "processing", "partial", "error"].includes(session.status) && <button style={field} disabled={busy} onClick={() => { void retry(); }}>Verificar / reintentar libros sin volver a subir</button>}
    {session && <IngestCleanupPanel key={session.id} session={session} disabled={busy} onChanged={async () => {
      const value = await getIngestSession(session.id);
      if (selectedSession.current !== session.id) return;
      setSession(value); await loadBooks(session.id); await refreshList(); onChanged();
    }} />}
    {books.length > 0 && <details><summary>{books.length} libros del manifiesto</summary>
      {books.slice(0, 100).map((book) => <p key={book.id}>{book.title} · {labels[book.status] || book.status}{book.error ? ` — ${book.error}` : ""}</p>)}
      {books.length > 100 && <p>Vista previa de los primeros 100 libros. El procesamiento incluye todos.</p>}
    </details>}
    <div style={{ marginTop: 14 }}>{busy && <button style={field} onClick={() => controller.current?.abort()}>Pausar envío</button>} <button style={field} onClick={() => { controller.current?.abort(); onClose(); }}>Cerrar (conservar sesión)</button></div>
  </section>;
}
