import { useCallback, useEffect, useRef, useState } from "react";
import { listDocuments, type DocumentOut } from "../../../api/documents";
import { listProjects, type ResearchProject } from "../../../api/research";
import { controlResearchRun, createResearchRun, getResearchRun, listResearchRuns, researchRunEvents, researchRunStages, updateResearchRunBudget } from "../../../api/researchRuns";
import type { ResearchExecution, ResearchStage, ResearchRunEvent } from "../../../api/types";
import { field, ghostBtn, primaryBtn } from "../ui";
import BillingAuthorizationPanel from "./BillingAuthorizationPanel";
import type { BillingTask } from "../../../api/types";

const STATUS: Record<string, string> = { planned: "Preparada", running: "En marcha", paused: "Pausada", needs_attention: "Necesita atención", completed: "Procesamiento terminado", cancelled: "Cancelada", pending: "Pendiente", skipped: "Omitida" };
const KIND: Record<string, string> = { rasterize: "Preparar páginas", transcribe: "Transcribir", extract: "Extraer actas", index_register: "Leer índice del libro", embed_text: "Indexar texto", embed_mentions: "Indexar menciones", embed_corpus: "Completar índice del corpus", discover: "Buscar coincidencias", pdf: "PDF de consulta", archive: "Archivo documental" };
const TASK: Record<string, string> = { transcription: "Transcripción", inference: "Interpretación", embedding: "Búsqueda semántica" };
const EVENT: Record<string, string> = { created: "Plan preparado", start: "Investigación iniciada", pause: "Pausa solicitada", resume: "Investigación reanudada", retry: "Reintento autorizado", cancel: "Investigación cancelada", budget_changed: "Presupuesto modificado", completed: "Procesamiento terminado", stage_completed: "Etapa terminada", stage_failed: "Etapa con incidencia", stage_queued: "Etapa encolada", coordinator_error: "Error de coordinación", admission_stopped: "Admisión de IA detenida" };
EVENT.billing_renewed = "Autorización económica renovada";
const dollars = (value: number) => `$${(value / 1_000_000).toFixed(6)}`;

export default function ResearchRunsPanel({ onReview, onDocument }: { onReview?: () => void; onDocument?: (id: string) => void }) {
  const [runs, setRuns] = useState<ResearchExecution[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [current, setCurrent] = useState<ResearchExecution | null>(null);
  const [stages, setStages] = useState<ResearchStage[]>([]);
  const [events, setEvents] = useState<ResearchRunEvent[]>([]);
  const [stageOffset, setStageOffset] = useState(0);
  const [form, setForm] = useState(false);
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [project, setProject] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [amount, setAmount] = useState("");
  const [lang, setLang] = useState("spa");
  const [flags, setFlags] = useState({ transcribe: true, extract: true, index: true, discover_all_tree: true, pdf: true, archive: true });
  const [reason, setReason] = useState("");
  const [newBudget, setNewBudget] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const requestKey = useRef<{ signature: string; id: string }>();
  const controlEpoch = useRef(0);
  const changing = useRef(false);

  const load = useCallback(async () => { setRuns(await listResearchRuns()); }, []);
  useEffect(() => { void load().catch((error: Error) => setMessage(error.message)); }, [load]);
  useEffect(() => {
    if (!form) return;
    let active = true;
    Promise.all([listDocuments("mine", 0, 50), listProjects()]).then(([docs, nextProjects]) => {
      if (active) { setDocuments(docs); setProjects(nextProjects); }
    }).catch((error: Error) => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, [form]);
  useEffect(() => {
    if (!selected) return;
    let active = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (changing.current) { timer = setTimeout(() => void poll(), 5000); return; }
      const epoch = controlEpoch.current;
      try {
        const [run, nextStages, nextEvents] = await Promise.all([getResearchRun(selected!), researchRunStages(selected!, stageOffset), researchRunEvents(selected!)]);
        if (!active) return;
        if (epoch === controlEpoch.current) { setCurrent(run); setStages(nextStages); setEvents(nextEvents); }
      } catch (error) { if (active) setMessage((error as Error).message); }
      if (active) timer = setTimeout(() => void poll(), 5000);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [selected, stageOffset]);
  function selectRun(run: ResearchExecution) {
    controlEpoch.current += 1;
    setSelected(run.id); setCurrent(run); setStages([]); setEvents([]); setStageOffset(0); setAuthorized(false); setReason("");
    setNewBudget((run.budget_microusd / 1_000_000).toString());
  }
  async function prepare(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      const budget = Number(amount);
      if (!amount.trim() || !Number.isFinite(budget) || budget < 0) throw new Error("Indica un presupuesto explícito; cero solo para trabajo local.");
      if (!picked.size) throw new Error("Selecciona al menos un documento.");
      const body = { title, document_ids: [...picked].sort(), project_id: project || undefined, budget_microusd: Math.round(budget * 1_000_000), lang, ...flags };
      const signature = JSON.stringify(body);
      if (requestKey.current?.signature !== signature) requestKey.current = { signature, id: crypto.randomUUID() };
      const run = await createResearchRun({ ...body, client_id: requestKey.current.id });
      selectRun(run); setForm(false); await load(); setMessage("Plan preparado sin llamadas de IA. Revisa sus modelos y presupuesto antes de iniciarlo.");
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  async function action(kind: "start" | "pause" | "resume" | "retry" | "cancel") {
    if (!current || busy) return;
    if (kind === "start" && !authorized) return;
    if (kind === "cancel" && !window.confirm("¿Cancelar esta investigación? Se conservan los originales, resultados y consumos ya registrados.")) return;
    changing.current = true; controlEpoch.current += 1;
    setBusy(true); setMessage("");
    try {
      const run = await controlResearchRun(current.id, kind, current.version, reason);
      setCurrent(run); setAuthorized(false); setStages(await researchRunStages(run.id, stageOffset)); await load();
      setMessage(kind === "pause" ? "Pausada. Las llamadas ya enviadas aún pueden terminar y facturarse." : "Estado actualizado.");
    } catch (error) { setMessage((error as Error).message); }
    finally { changing.current = false; controlEpoch.current += 1; setBusy(false); }
  }
  async function changeBudget() {
    if (!current || busy) return;
    changing.current = true; controlEpoch.current += 1;
    setBusy(true);
    try {
      const value = Number(newBudget);
      if (!newBudget.trim() || !Number.isFinite(value) || value < 0) throw new Error("Introduce un importe válido.");
      setCurrent(await updateResearchRunBudget(current.id, current.version, Math.round(value * 1_000_000), reason));
      setMessage("Presupuesto actualizado y auditado. El límite mensual del espacio también sigue vigente."); await load();
    } catch (error) { setMessage((error as Error).message); }
    finally { changing.current = false; controlEpoch.current += 1; setBusy(false); }
  }
  async function moreDocuments() {
    setBusy(true);
    try { const next = await listDocuments("mine", documents.length, 50); setDocuments((old) => [...old, ...next]); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  const totalStages = current ? Object.values(current.stage_counts).reduce((a, b) => a + b, 0) : 0;
  return <section style={{ border: "1px solid var(--line)", borderRadius: 12, padding: 18, marginBottom: 24 }}>
    <h2 style={{ margin: "0 0 8px", fontSize: 19 }}>Investigación automática</h2>
    <p style={{ fontSize: 13, color: "var(--muted)" }}>Una ejecución reúne libros, lectura manuscrita, extracción, búsqueda y archivo. Los modelos quedan fijados al prepararla; ninguna coincidencia se añade al árbol sin tu revisión.</p>
    <button style={primaryBtn} disabled={busy} onClick={() => setForm(!form)}>{form ? "Cerrar preparación" : "Preparar investigación"}</button>
    {form && <form onSubmit={(event) => void prepare(event)} style={{ display: "grid", gap: 10, marginTop: 14 }}>
      <label>Título <input required maxLength={255} style={field} value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>Presupuesto total (USD) <input required type="number" min="0" max="1000000" step="0.000001" style={field} value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
      <label>Proyecto <select style={field} value={project} onChange={(e) => setProject(e.target.value)}><option value="">Sin proyecto</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
      <label>Idioma para OCR local <select value={lang} onChange={(e) => setLang(e.target.value)}><option value="spa">Español</option><option value="cat">Catalán</option><option value="lat">Latín</option><option value="spa+cat+lat">Combinado (requiere idiomas instalados)</option></select></label>
      <p style={{ fontSize: 12 }}>Si indexas y comparas el árbol, se completará una generación compatible del corpus de tu espacio, también para documentos anteriores. Todo el gasto queda sujeto al presupuesto de esta investigación.</p>
      <fieldset><legend>Etapas</legend>{(Object.keys(flags) as (keyof typeof flags)[]).map((key) => <label key={key} style={{ display: "block", fontSize: 13 }}><input type="checkbox" checked={flags[key]} onChange={(e) => setFlags((old) => ({ ...old, [key]: e.target.checked }))} />{{ transcribe: "Transcribir páginas pendientes", extract: "Extraer actas / leer índices", index: "Indexar texto y menciones", discover_all_tree: "Comparar todo el árbol con el corpus del espacio", pdf: "Generar PDF de consulta (no PDF/A)", archive: "Generar archivo documental por libro" }[key]}</label>)}</fieldset>
      <fieldset style={{ maxHeight: 240, overflow: "auto" }}><legend>Libros seleccionados: {picked.size}</legend>
        <button type="button" disabled={busy} onClick={() => setPicked((old) => new Set([...old, ...documents.filter((doc) => !doc.pending_job_id).map((doc) => doc.id)]))}>Seleccionar los libros cargados disponibles</button>
        {documents.map((doc) => <label key={doc.id} style={{ display: "block", fontSize: 13, padding: 4 }}><input type="checkbox" disabled={!!doc.pending_job_id} checked={picked.has(doc.id)} onChange={(e) => setPicked((old) => { const next = new Set(old); if (e.target.checked) next.add(doc.id); else next.delete(doc.id); return next; })} />{doc.title} · {doc.page_count} páginas{doc.pending_job_id ? " · tarea en curso" : ""}</label>)}
      </fieldset>
      <button type="button" disabled={busy} onClick={() => void moreDocuments()}>Cargar más libros</button>
      <button disabled={busy || !picked.size} style={primaryBtn}>Crear plan sin llamadas de IA</button>
    </form>}
    <div style={{ marginTop: 14, display: "flex", flexWrap: "wrap", gap: 8 }}>
      {runs.map((run) => <button key={run.id} style={ghostBtn} disabled={busy} onClick={() => selectRun(run)}>{run.title} · {STATUS[run.status]}</button>)}
      {runs.length >= 20 && <button onClick={() => void listResearchRuns(runs.length).then((rows) => setRuns((old) => [...old, ...rows])).catch((e: Error) => setMessage(e.message))}>Más investigaciones</button>}
    </div>
    {current && <div style={{ marginTop: 16 }}>
      <h3>{current.title} · {STATUS[current.status]}</h3>
      <p style={{ fontSize: 13 }}>{current.stage_counts.completed ?? 0}/{totalStages} etapas · {current.document_count} libros · {current.person_count} personas. Liquidado estimado: {dollars(current.spent_microusd)} · reservado: {dollars(current.reserved_microusd)} · presupuesto: {dollars(current.budget_microusd)}.</p>
      {current.error && <p role="alert" style={{ color: "var(--danger)" }}>{current.error}</p>}
      <details><summary>Modelos fijados para esta investigación</summary>{Object.entries(current.profiles).map(([task, profile]) => <p key={task} style={{ fontSize: 12 }}>{TASK[task] ?? task}: {profile.engine} / {profile.model ?? "local"}{profile.budget_policy ? ` · caducidad original (consulta las renovaciones): ${profile.budget_policy.valid_until}` : ""}</p>)}</details>
      {!["completed", "cancelled"].includes(current.status) && Object.entries(current.profiles).filter(([task, profile]) => profile.budget_policy && task in TASK).map(([task]) =>
        <BillingAuthorizationPanel key={`${current.id}:${task}`} scope="research_run" targetId={current.id} task={task as BillingTask} onChanged={async () => {
          const epoch = ++controlEpoch.current; setAuthorized(false);
          const [run, nextEvents] = await Promise.all([getResearchRun(current.id), researchRunEvents(current.id)]);
          if (epoch !== controlEpoch.current) return;
          setCurrent(run); setEvents(nextEvents); await load();
        }} />)}
      {current.status === "planned" && <label style={{ display: "block", margin: "12px 0", fontSize: 13 }}><input type="checkbox" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} /> Autorizo iniciar las etapas con estos modelos y este presupuesto estimado.</label>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0" }}>
        {current.status === "planned" && <button style={primaryBtn} disabled={busy || !authorized} onClick={() => void action("start")}>Iniciar con este presupuesto</button>}
        {current.status === "running" && <button disabled={busy} onClick={() => void action("pause")}>Pausar</button>}
        {current.status === "paused" && <button disabled={busy} onClick={() => void action("resume")}>Reanudar misma investigación</button>}
        {current.status === "needs_attention" && <button disabled={busy || !reason.trim()} onClick={() => void action("retry")}>Reintentar etapa conservando resultados</button>}
        {!["completed", "cancelled"].includes(current.status) && <button disabled={busy} onClick={() => void action("cancel")}>Cancelar investigación</button>}
        {onReview && <button onClick={onReview}>Revisar descubrimientos</button>}
      </div>
      {!["completed", "cancelled"].includes(current.status) && <details><summary>Resolver incidencias / presupuesto</summary>
        <p style={{ fontSize: 12 }}>Concilia primero las llamadas inciertas en Ajustes. Un perfil caducado no se sustituye automáticamente. El cambio de presupuesto requiere administración.</p>
        <label>Motivo o corrección realizada <textarea value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} /></label>
        <label>Nuevo presupuesto (USD) <input type="number" min="0" max="1000000" step="0.000001" value={newBudget} onChange={(e) => setNewBudget(e.target.value)} /></label>
        <button disabled={busy || !reason.trim()} onClick={() => void changeBudget()}>Actualizar presupuesto con motivo</button>
      </details>}
      <ul>{stages.map((stage) => <li key={stage.id} style={{ padding: "6px 0", fontSize: 13 }}>
        {stage.position + 1}. {KIND[stage.kind] ?? stage.kind} · {stage.title} · {STATUS[stage.status] ?? stage.status} · intento {stage.attempts}
        {stage.error && <span style={{ color: "var(--danger)" }}> · {stage.error}</span>}
        {stage.document_id && onDocument && <button onClick={() => onDocument(stage.document_id!)}>Abrir libro</button>}
      </li>)}</ul>
      <button disabled={!stageOffset || busy} onClick={() => setStageOffset((old) => Math.max(0, old - 50))}>Etapas anteriores</button> <button disabled={stageOffset + 50 >= totalStages || busy} onClick={() => setStageOffset((old) => old + 50)}>Etapas siguientes</button>
      <details><summary>Historial reciente</summary>{events.map((event) => <p key={event.id} style={{ fontSize: 12 }}>{new Date(event.created_at).toLocaleString()} · {EVENT[event.kind] ?? event.kind}{event.detail.reason ? ` · ${event.detail.reason}` : ""}</p>)}</details>
      {current.status === "completed" && <p>El procesamiento ha terminado. Esto no confirma parentescos: revisa las propuestas y sus fuentes antes de aceptarlas.</p>}
    </div>}
    <p role="status" style={{ fontSize: 13 }}>{message}</p>
  </section>;
}
