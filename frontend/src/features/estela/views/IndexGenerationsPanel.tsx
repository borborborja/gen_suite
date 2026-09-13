import { useEffect, useRef, useState } from "react";
import { activeIndex, controlIndex, createIndex, getIndex, listIndexes, type ActiveIndex, type IndexGeneration, type IndexSummary } from "../../../api/indexes";
import { field, primaryBtn } from "../ui";
import BillingAuthorizationPanel from "./BillingAuthorizationPanel";

const LABEL: Record<string, string> = { building: "En construcción", ready: "Lista", error: "Con incidencia", cancelled: "Cancelada", queued: "En cola", running: "En marcha", paused: "Pausada", completed: "Terminado", partial: "Parcial" };

export default function IndexGenerationsPanel() {
  const [rows, setRows] = useState<IndexSummary[]>([]);
  const [active, setActive] = useState<ActiveIndex | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [current, setCurrent] = useState<IndexGeneration | null>(null);
  const [title, setTitle] = useState("Nuevo índice del corpus");
  const [includePublic, setIncludePublic] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [confirmedSwitch, setConfirmedSwitch] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const requestKey = useRef<{ signature: string; id: string }>();
  const epoch = useRef(0);
  const changing = useRef(false);
  const activeId = useRef<string | null>(null);
  async function load() {
    const revision = epoch.current;
    const [next, nextActive] = await Promise.all([listIndexes(), activeIndex()]);
    if (revision !== epoch.current) return;
    setRows(next); setActive(nextActive); activeId.current = nextActive.id;
  }
  useEffect(() => { void load().catch((error: Error) => setMessage(error.message)); }, []);
  useEffect(() => {
    if (!selected) return;
    let running = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const revision = epoch.current;
      if (!changing.current) {
        try {
          const [value, live] = await Promise.all([getIndex(selected!), activeIndex()]);
          if (running && revision === epoch.current) {
            setCurrent(value);
            if (activeId.current !== live.id) setConfirmedSwitch(false);
            activeId.current = live.id; setActive(live);
          }
        } catch (error) { if (running) setMessage((error as Error).message); }
      }
      if (running) timer = setTimeout(() => void poll(), 5000);
    }
    void poll(); return () => { running = false; clearTimeout(timer); };
  }, [selected]);
  async function build(event: React.FormEvent) {
    event.preventDefault(); if (!authorized || busy) return;
    changing.current = true; epoch.current += 1; setBusy(true); setMessage("");
    try {
      const signature = JSON.stringify({ title, includePublic });
      if (requestKey.current?.signature !== signature) requestKey.current = { signature, id: crypto.randomUUID() };
      const value = await createIndex({ client_id: requestKey.current.id, title, include_public: includePublic, authorize: true });
      setSelected(value.id); setCurrent(value); setAuthorized(false); setConfirmedSwitch(false);
      await load(); setMessage("Construcción encolada. El índice activo no se ha sustituido.");
    } catch (error) { setMessage((error as Error).message); }
    finally { changing.current = false; epoch.current += 1; setBusy(false); }
  }
  async function control(action: string) {
    if (!current || busy || !reason.trim() || (action === "activate" && !confirmedSwitch)) return;
    changing.current = true; epoch.current += 1; setBusy(true); setMessage("");
    try {
      const value = await controlIndex(current.id, action, { expected_version: current.version, expected_active_id: active?.id ?? null, reason });
      setCurrent(value); setConfirmedSwitch(false); await load();
      setMessage(action === "activate" ? "Índice activado; la generación anterior se conserva para volver atrás." : "Estado actualizado. Los resultados y consumos ya registrados se conservan.");
    } catch (error) { setMessage((error as Error).message); }
    finally { changing.current = false; epoch.current += 1; setBusy(false); }
  }
  return <section style={{ border: "1px solid var(--line)", borderRadius: 12, padding: 16, marginTop: 20 }}>
    <h3>Generaciones del índice de búsqueda</h3>
    <p style={{ fontSize: 13 }}>Cambiar la vinculación de embeddings no cambia el índice consultado. Las búsquedas usan el modelo del índice activo y excluyen fuentes corregidas o retiradas. No se borran vectores para reconstruir.</p>
    <p>{active?.id ? `Índice activo: ${active.model ?? "modelo local"}` : "Sin índice activo: la búsqueda por palabras sigue disponible."}</p>
    <form onSubmit={(event) => void build(event)} style={{ display: "grid", gap: 10 }}>
      <label>Título del nuevo índice <input required maxLength={255} style={field} value={title} onChange={(e) => { setTitle(e.target.value); setAuthorized(false); }} /></label>
      <label><input type="checkbox" checked={includePublic} onChange={(e) => { setIncludePublic(e.target.checked); setAuthorized(false); }} /> Incluir documentos públicos actualmente accesibles, además de mi corpus</label>
      <p style={{ fontSize: 12 }}>Se procesarán las transcripciones y menciones vigentes. Puede consumir IA de pago y almacenamiento adicional; sigue vigente el presupuesto mensual de Ajustes. Sin esta opción, la parte semántica solo cubre tu corpus indexado.</p>
      <label><input type="checkbox" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} /> Autorizo construir con el perfil configurado y el presupuesto mensual vigente</label>
      <button style={primaryBtn} disabled={busy || !authorized}>Construir sin sustituir el índice activo</button>
    </form>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>{rows.map((row) => <button key={row.id} disabled={busy} onClick={() => { epoch.current += 1; setSelected(row.id); setCurrent(null); setConfirmedSwitch(false); setReason(""); }}>{row.title} · {row.is_active ? "Activo" : LABEL[row.status]}</button>)}</div>
    {rows.length >= 20 && <button disabled={busy} onClick={() => void listIndexes(rows.length).then((more) => setRows((old) => [...old, ...more])).catch((error: Error) => setMessage(error.message))}>Más generaciones</button>}
    {current && <div style={{ marginTop: 16 }}>
      <h4>{current.title} · {LABEL[current.status]}</h4>
      <p>{current.profile.engine} / {current.profile.model ?? "local"} · trabajo: {LABEL[current.job_status ?? ""] ?? "Etapa de investigación"}</p>
      <p>Consultables: {current.searchable.transcription ?? 0} transcripciones y {current.searchable.mention ?? 0} menciones. Pendientes: {current.counts.pending ?? 0}. Obsoletas o retiradas: {current.outdated}.</p>
      <p style={{ fontSize: 12 }}>Es la cobertura de esta generación, no una garantía de que todos los documentos futuros estén indexados. {current.include_public ? "Incluye los públicos capturados; se vuelve a comprobar su acceso al consultar." : "Solo corpus propio."}</p>
      {current.error && <p role="alert">{current.error}</p>}
      {current.profile.budget_policy && current.status !== "cancelled" && <BillingAuthorizationPanel key={current.id}
        scope="embedding_generation" targetId={current.id} task="embedding" onChanged={async () => {
          const revision = ++epoch.current; setConfirmedSwitch(false);
          const value = await getIndex(current.id);
          if (revision !== epoch.current) return;
          setCurrent(value); await load();
        }} />}
      <label>Motivo del cambio de índice <textarea maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      {current.status === "ready" && !current.is_active && <>
        <label style={{ display: "block" }}><input type="checkbox" checked={confirmedSwitch} onChange={(e) => setConfirmedSwitch(e.target.checked)} /> He revisado el modelo y la cobertura; quiero activar esta generación</label>
        <button disabled={busy || !reason.trim() || !confirmedSwitch} onClick={() => void control("activate")}>Activar conservando la anterior</button>
      </>}
      {current.purpose === "full" && !current.is_active && <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        {["queued", "running"].includes(current.job_status ?? "") && <button disabled={busy || !reason.trim()} onClick={() => void control("pause")}>Pausar construcción</button>}
        {current.job_status === "paused" && <button disabled={busy || !reason.trim()} onClick={() => void control("resume")}>Reanudar construcción</button>}
        {["error", "partial"].includes(current.job_status ?? "") && <button disabled={busy || !reason.trim()} onClick={() => void control("retry")}>Reintentar conservando resultados</button>}
        {["queued", "running", "paused", "error", "partial"].includes(current.job_status ?? "") && <button disabled={busy || !reason.trim()} onClick={() => void control("cancel")}>Cancelar construcción</button>}
      </div>}
      {current.purpose === "research" && <p style={{ fontSize: 12 }}>Las etapas y sus autorizaciones económicas se gestionan desde Investigación. Esta renovación del índice se aplica a consultas generales, no a las etapas de su investigación. Termina la investigación antes de activar este índice para búsquedas generales.</p>}
    </div>}
    <p role="status">{message}</p>
  </section>;
}
