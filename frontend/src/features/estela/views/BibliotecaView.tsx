import { useCallback, useEffect, useRef, useState } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import { Plus } from "../icons";
import {
  listDocuments, uploadDocument, discardImages, getRecordTypes, getSeriesGaps,
  parseIndex, getDocumentIndex, rerasterize,
  type DocumentOut, type UploadFlags, type RecordType, type SeriesGap, type IndexReport,
} from "../../../api/documents";
import { startTranscription, getVersions, reconcile, type VersionPair, type ReconcileBody } from "../../../api/transcription";
import { startExtraction } from "../../../api/extraction";
import { streamJob, listJobs } from "../../../api/jobs";
import { geoSearch, type GeoResult } from "../../../api/geo";
import { getCatalog, listCredentials, type CatalogEntry, type Credential } from "../../../api/providers";

interface Prog { label: string; done: number; total: number; status: string }

const typeLabel = (key: string | null, types: RecordType[]) =>
  key ? (types.find((t) => t.key === key)?.label ?? key) : "";

const ORIGINS = [
  { v: "own_photo", l: "Foto propia" },
  { v: "public_archive", l: "Archivo público / dominio público" },
  { v: "licensed", l: "Colección con licencia" },
  { v: "familysearch", l: "FamilySearch (restringido)" },
];

export default function BibliotecaView() {
  const e = useEstela();
  const [docs, setDocs] = useState<DocumentOut[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [dropped, setDropped] = useState<File[]>([]);
  const [pageDrag, setPageDrag] = useState(false);
  const [progress, setProgress] = useState<Record<string, Prog>>({});
  const [types, setTypes] = useState<RecordType[]>([]);
  const [retxDoc, setRetxDoc] = useState<DocumentOut | null>(null);
  const [reconDoc, setReconDoc] = useState<DocumentOut | null>(null);
  const [extractDoc, setExtractDoc] = useState<DocumentOut | null>(null);
  const [indexDoc, setIndexDoc] = useState<DocumentOut | null>(null);
  const streamed = useRef<Set<string>>(new Set());

  const [gaps, setGaps] = useState<SeriesGap[]>([]);
  const load = useCallback(async () => {
    try { setDocs(await listDocuments("mine")); } catch { setDocs([]); }
    getSeriesGaps().then((g) => setGaps(g.filter((s) => s.missing.length > 0))).catch(() => setGaps([]));
  }, []);
  useEffect(() => { void load(); getRecordTypes().then(setTypes).catch(() => setTypes([])); }, [load]);

  const track = useCallback((docId: string, label: string, jobId: string, init?: { done?: number; total?: number }) => {
    setProgress((p) => ({ ...p, [docId]: { label, done: init?.done ?? 0, total: init?.total ?? 0, status: "running" } }));
    streamJob(jobId,
      (evt) => setProgress((p) => ({ ...p, [docId]: { label, done: evt.done ?? 0, total: evt.total ?? 0, status: "running" } })),
      (last) => {
        setProgress((p) => { const n = { ...p }; delete n[docId]; return n; });
        streamed.current.delete(jobId); void load();
        if (last?.kind === "book_fail") e.notify(`${label}: error`, "var(--danger)");
        else e.notify(`${label}: completado`, "var(--ok)");
      },
    );
  }, [load, e]);

  // auto-stream PDFs still rasterizing
  useEffect(() => {
    for (const d of docs ?? []) {
      if (d.pending_job_id && !streamed.current.has(d.pending_job_id)) {
        streamed.current.add(d.pending_job_id);
        track(d.id, "Rasterizando", d.pending_job_id);
      }
    }
  }, [docs, track]);

  // Reconnect to any job already running/queued for a visible document — started in another tab,
  // before a reload, or by a worker that picked it up later. Without this, progress is only shown
  // when you launch the job and stay on this page; navigate away and it looks stuck.
  useEffect(() => {
    const ids = new Set((docs ?? []).map((d) => d.id));
    if (ids.size === 0) return;
    let alive = true;
    const RUNNING = new Set(["running", "queued"]);
    const LABEL: Record<string, string> = {
      transcription: "Transcribiendo", extraction: "Extrayendo", rasterize: "Rasterizando",
      embedding: "Embeddings", embed_document: "Embeddings",
    };
    const poll = async () => {
      let jobs;
      try { jobs = await listJobs(); } catch { return; }
      if (!alive) return;
      for (const j of jobs) {
        // self-heal: a tracked job that reached a TERMINAL state (incl. an orphaned 'running' job
        // the worker cleaned to 'error' on restart) → clear its card, even if its SSE stream hung.
        if (streamed.current.has(j.id) && !RUNNING.has(j.status)) {
          streamed.current.delete(j.id);
          if (j.document_id) setProgress((p) => { const n = { ...p }; delete n[j.document_id!]; return n; });
          continue;
        }
        if (!RUNNING.has(j.status) || !j.document_id || !ids.has(j.document_id)) continue;
        if (streamed.current.has(j.id)) continue;
        streamed.current.add(j.id);
        track(j.document_id, LABEL[j.type] ?? j.type, j.id, j.progress ?? undefined);
      }
    };
    void poll();
    const t = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [docs, track]);

  async function action(id: string, label: string, fn: () => Promise<unknown>) {
    setBusy(`${id}:${label}`);
    try { await fn(); await load(); e.notify("Hecho", "var(--ok)"); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    setBusy(null);
  }

  async function runJob(docId: string, label: string, start: () => Promise<{ id: string }>) {
    setBusy(`${docId}:${label}`);
    try { const job = await start(); e.notify(`${label}…`); track(docId, label, job.id); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    setBusy(null);
  }

  const hasReal = docs && docs.length > 0;

  return (
    <section
      onDragOver={(ev) => { if (ev.dataTransfer.types.includes("Files")) { ev.preventDefault(); setPageDrag(true); } }}
      onDragLeave={(ev) => { if (ev.currentTarget === ev.target) setPageDrag(false); }}
      onDrop={(ev) => {
        if (!ev.dataTransfer.files.length) return;
        ev.preventDefault(); setPageDrag(false);
        setDropped(Array.from(ev.dataTransfer.files)); setShowUpload(true);
      }}
      style={{ padding: "32px 44px 64px", maxWidth: 1200, position: "relative", minHeight: "70vh" }}
    >
      {pageDrag && (
        <div style={{ position: "fixed", inset: 0, zIndex: 50, background: "var(--accent-faint)", border: "3px dashed var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
          <div style={{ fontFamily: fonts.serif, fontSize: 26, fontWeight: 600, color: "var(--accent)" }}>Suelta tus PDFs para añadirlos</div>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 26 }}>
        <div>
          <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Biblioteca</h1>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0" }}>
            {docs === null ? "Cargando…" : hasReal ? `${docs.length} libros · arrastra PDFs aquí o pulsa Añadir` : "Arrastra tus PDFs aquí, o pulsa Añadir libros"}
          </p>
        </div>
        <button onClick={() => { setDropped([]); setShowUpload((v) => !v); }} style={{ flex: "none", display: "inline-flex", alignItems: "center", gap: 9, background: "var(--accent)", color: "#fff", border: "none", borderRadius: 9, padding: "13px 20px", fontFamily: "inherit", fontSize: 14.5, fontWeight: 600, cursor: "pointer", boxShadow: "0 5px 18px rgba(217,83,30,.3)" }}>
          <Plus size={18} />Añadir libros
        </button>
      </div>

      {gaps.length > 0 && (
        <div style={{ background: "var(--warn-faint)", border: "1px solid var(--warn)", borderRadius: 12, padding: "12px 16px", marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--warn)", marginBottom: 4 }}>⚠ Posibles huecos en la serie de libros</div>
          {gaps.map((s, i) => (
            <div key={i} style={{ fontSize: 12.5, color: "var(--fg)" }}>
              {(s.place_name || "Sin parroquia")} · {typeLabel(s.record_type, types)}: falta(n) el/los libro(s) <b>{s.missing.join(", ")}</b>
              <span style={{ color: "var(--muted)" }}> (tienes: {s.present.join(", ")})</span>
            </div>
          ))}
        </div>
      )}

      {showUpload && <UploadForm initial={dropped} onDone={() => { setShowUpload(false); setDropped([]); void load(); }} />}

      {/* real documents */}
      {hasReal && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 18 }}>
          {docs!.map((d) => (
            <div key={d.id} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, overflow: "hidden" }}>
              <div onClick={() => e.openDoc(d.id)} style={{ height: 96, background: "linear-gradient(155deg,#efe4ca,#d8c39a)", position: "relative", borderBottom: "1px solid var(--line)", cursor: "pointer" }}>
                <div style={{ position: "absolute", inset: 0, background: "repeating-linear-gradient(transparent 0 12px, rgba(96,64,30,.1) 12px 13px)" }} />
                {d.image_policy === "data_only" && (
                  <span style={{ position: "absolute", top: 10, right: 10, fontFamily: fonts.mono, fontSize: 9.5, padding: "3px 7px", borderRadius: 5, background: "rgba(38,33,27,.7)", color: "#fff" }}>SOLO DATOS</span>
                )}
              </div>
              <div style={{ padding: 16 }}>
                <div style={{ fontFamily: fonts.serif, fontSize: 17, fontWeight: 600, lineHeight: 1.15 }}>
                  {d.is_index && <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--accent)", marginRight: 6 }}>ÍNDICE</span>}
                  {d.title}
                </div>
                {(d.default_record_type || d.year_from || d.book_number != null) && (
                  <div style={{ fontSize: 12.5, color: "var(--accent)", marginTop: 4, fontWeight: 500 }}>
                    {[d.book_number != null ? `Libro ${d.book_number}` : null, typeLabel(d.default_record_type, types), d.year_from ? `${d.year_from}–${d.year_to ?? ""}` : null].filter(Boolean).join(" · ")}
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                  <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{d.page_count} págs</span>
                  {d.source_origin && <span style={{ fontFamily: fonts.mono, fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "var(--bg)", border: "1px solid var(--line2)", color: "var(--muted)" }}>{d.source_origin}</span>}
                  {d.may_contain_living && <span style={{ fontFamily: fonts.mono, fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "var(--warn-faint)", color: "var(--warn)", border: "1px solid var(--warn)" }}>posibles vivos</span>}
                </div>
                {progress[d.id] && (
                  <div style={{ marginTop: 12 }}>
                    <div style={{ height: 6, borderRadius: 999, background: "var(--line2)", overflow: "hidden" }}>
                      <div style={{ height: "100%", borderRadius: 999, width: progress[d.id].total ? `${Math.round((progress[d.id].done / progress[d.id].total) * 100)}%` : "10%", background: "linear-gradient(90deg,var(--accent),var(--gold))", transition: "width .3s" }} />
                    </div>
                    <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginTop: 6 }}>{progress[d.id].label} · {progress[d.id].done}/{progress[d.id].total || "…"}</div>
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
                  <Act on={busy === `${d.id}:tx` || !!progress[d.id]} label="Transcribir" onClick={() => runJob(d.id, "Transcribiendo", () => startTranscription(d.id))} />
                  <Act on={!!progress[d.id]} label="Re-reconocer" onClick={() => setRetxDoc(d)} />
                  <Act label="Reconciliar" onClick={() => setReconDoc(d)} />
                  <Act label="Índice" onClick={() => setIndexDoc(d)} />
                  <Act label="Re-procesar" onClick={() => { if (window.confirm("Re-rasteriza este libro a la calidad actual (Ajustes) desde el PDF original y borra sus transcripciones/actas para volver a procesarlo. ¿Continuar?")) runJob(d.id, "Re-procesando", () => rerasterize(d.id)); }} />
                  <Act on={busy === `${d.id}:ex` || !!progress[d.id]} label="Extraer" onClick={() => setExtractDoc(d)} />
                  {d.image_policy !== "data_only" && (
                    <Act danger on={busy === `${d.id}:di`} label="Descartar imágenes"
                      onClick={() => confirm("¿Descartar las imágenes? Se conservan los datos y la cita, pero no podrás volver a ver el escaneo.") && action(d.id, "di", () => discardImages(d.id))} />
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {retxDoc && (
        <RetxPanel doc={retxDoc} onClose={() => setRetxDoc(null)}
          onStart={(opts) => { const d = retxDoc; setRetxDoc(null); runJob(d.id, "Re-reconociendo", () => startTranscription(d.id, { ...opts, replace: true })); }} />
      )}
      {extractDoc && (
        <ExtractPanel doc={extractDoc} onClose={() => setExtractDoc(null)}
          onStart={(opts) => { const d = extractDoc; setExtractDoc(null); runJob(d.id, "Extrayendo", () => startExtraction({ document_id: d.id, ...opts })); }} />
      )}
      {indexDoc && <IndexPanel doc={indexDoc} onClose={() => setIndexDoc(null)} notify={e.notify} />}
      {reconDoc && (
        <ReconcilePanel doc={reconDoc} onClose={() => setReconDoc(null)}
          onDone={() => { setReconDoc(null); void load(); e.notify("Reconciliación aplicada — re-indexando", "var(--ok)"); }} />
      )}

      {/* empty state when there's no real data yet */}
      {docs !== null && !hasReal && !showUpload && (
        <div style={{ marginTop: 8, border: "1px dashed var(--line)", borderRadius: 16, padding: "56px 32px", textAlign: "center", background: "var(--surface)" }}>
          <div style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, marginBottom: 8 }}>Tu biblioteca está vacía</div>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: "0 auto 20px", maxWidth: 460, lineHeight: 1.5 }}>
            Sube tus PDFs o imágenes con «Añadir libros», arrástralos a esta ventana, o descarga libros desde FamilySearch.
          </p>
          <button onClick={() => { setDropped([]); setShowUpload(true); }} style={{ display: "inline-flex", alignItems: "center", gap: 9, background: "var(--accent)", color: "#fff", border: "none", borderRadius: 9, padding: "12px 20px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
            <Plus size={18} />Añadir libros
          </button>
        </div>
      )}
    </section>
  );
}

function Act({ label, onClick, on, danger }: { label: string; onClick: () => void; on?: boolean; danger?: boolean }) {
  return (
    <button onClick={onClick} disabled={on} style={{
      background: "transparent", color: danger ? "var(--danger)" : "var(--fg)",
      border: `1px solid ${danger ? "var(--danger)" : "var(--line)"}`, borderRadius: 7,
      padding: "7px 11px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600,
      cursor: on ? "default" : "pointer", opacity: on ? 0.5 : 1,
    }}>{on ? "…" : label}</button>
  );
}

const ov: React.CSSProperties = { position: "fixed", inset: 0, zIndex: 70, background: "rgba(15,11,6,.55)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "56px 20px", overflowY: "auto" };
const card: React.CSSProperties = { width: "100%", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16, padding: 24, boxShadow: "0 24px 80px rgba(0,0,0,.4)" };
const fldS: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5, width: "100%", boxSizing: "border-box" };
const primaryBtn: React.CSSProperties = { background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "10px 18px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer" };

function RetxPanel({ doc, onClose, onStart }: { doc: DocumentOut; onClose: () => void; onStart: (opts: { credential_id?: string; model?: string }) => void }) {
  const [creds, setCreds] = useState<Credential[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [credId, setCredId] = useState("");
  const [model, setModel] = useState("");
  useEffect(() => {
    Promise.all([listCredentials(), getCatalog()]).then(([c, cat]) => { setCreds(c); setCatalog(cat); }).catch(() => { setCreds([]); setCatalog([]); });
  }, []);
  const capsByKey = Object.fromEntries(catalog.map((c) => [c.key, c.capabilities]));
  const txCreds = creds.filter((c) => (capsByKey[c.provider_key] || []).includes("transcription"));
  const sel = txCreds.find((c) => c.id === credId);
  return (
    <div onClick={onClose} style={ov}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...card, maxWidth: 520 }}>
        <h2 style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, margin: "0 0 4px" }}>Re-reconocer «{doc.title}»</h2>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 18px" }}>Vuelve a transcribir todo el libro con otro modelo. La nueva versión entra como candidata; luego decides en «Reconciliar».</p>
        {txCreds.length === 0
          ? <div style={{ color: "var(--muted)", fontSize: 13.5 }}>No hay modelos con capacidad de transcripción configurados. Añádelos en Ajustes.</div>
          : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Modelo
                <select style={fldS} value={credId} onChange={(e) => { setCredId(e.target.value); setModel(""); }}>
                  <option value="">(elige un proveedor)</option>
                  {txCreds.map((c) => <option key={c.id} value={c.id}>{c.label} · {c.provider_key} {c.model_default ? `(${c.model_default})` : ""}</option>)}
                </select>
              </label>
              {sel && <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Modelo concreto (opcional)
                <input style={fldS} value={model} onChange={(e) => setModel(e.target.value)} placeholder={sel.model_default ?? "por defecto del proveedor"} />
              </label>}
              <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                <button disabled={!credId} onClick={() => onStart({ credential_id: credId, model: model || undefined })} style={{ ...primaryBtn, opacity: credId ? 1 : 0.5 }}>Re-reconocer</button>
                <button onClick={onClose} style={{ ...primaryBtn, background: "transparent", color: "var(--fg)", border: "1px solid var(--line)" }}>Cancelar</button>
              </div>
            </div>
          )}
      </div>
    </div>
  );
}

function ExtractPanel({ doc, onClose, onStart }: { doc: DocumentOut; onClose: () => void; onStart: (opts: { credential_id?: string; model?: string; modality?: "sync" | "batch" }) => void }) {
  const [creds, setCreds] = useState<Credential[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [credId, setCredId] = useState("");
  const [model, setModel] = useState("");
  const [modality, setModality] = useState<"sync" | "batch">("sync");
  useEffect(() => {
    Promise.all([listCredentials(), getCatalog()]).then(([c, cat]) => { setCreds(c); setCatalog(cat); }).catch(() => { setCreds([]); setCatalog([]); });
  }, []);
  const capsByKey = Object.fromEntries(catalog.map((c) => [c.key, c.capabilities]));
  const infCreds = creds.filter((c) => (capsByKey[c.provider_key] || []).includes("text"));
  const sel = infCreds.find((c) => c.id === credId);
  return (
    <div onClick={onClose} style={ov}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...card, maxWidth: 540 }}>
        <h2 style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, margin: "0 0 4px" }}>Extraer actas de «{doc.title}»</h2>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 18px" }}>Elige proveedor y modelo (por defecto, el del libro/tenant), y la modalidad. Solo se re-procesan las páginas que aún no tienen actas.</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Proveedor / modelo
            <select style={fldS} value={credId} onChange={(e) => { setCredId(e.target.value); setModel(""); }}>
              <option value="">(por defecto del tenant)</option>
              {infCreds.map((c) => <option key={c.id} value={c.id}>{c.label} · {c.provider_key} {c.model_default ? `(${c.model_default})` : ""}</option>)}
            </select>
          </label>
          {sel && <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Modelo concreto (opcional)
            <input style={fldS} value={model} onChange={(e) => setModel(e.target.value)} placeholder={sel.model_default ?? "por defecto del proveedor"} />
          </label>}
          <div style={{ fontSize: 12, color: "var(--muted)" }}>Modalidad
            <div style={{ display: "flex", gap: 14, marginTop: 5, fontSize: 13, color: "var(--fg)" }}>
              <label style={{ display: "inline-flex", gap: 6, alignItems: "center", cursor: "pointer" }}><input type="radio" checked={modality === "sync"} onChange={() => setModality("sync")} /> Síncrona (rápida)</label>
              <label style={{ display: "inline-flex", gap: 6, alignItems: "center", cursor: "pointer" }}><input type="radio" checked={modality === "batch"} onChange={() => setModality("batch")} /> Batch (~50% más barata, asíncrona)</label>
            </div>
            {modality === "batch" && <div style={{ fontSize: 11.5, color: "var(--warn)", marginTop: 5 }}>Batch requiere un proveedor compatible (OpenAI / Google); si no, usa síncrona automáticamente. Puede tardar horas.</div>}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button onClick={() => onStart({ credential_id: credId || undefined, model: model || undefined, modality })} style={primaryBtn}>Extraer</button>
            <button onClick={onClose} style={{ ...primaryBtn, background: "transparent", color: "var(--fg)", border: "1px solid var(--line)" }}>Cancelar</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function IndexPanel({ doc, onClose, notify }: { doc: DocumentOut; onClose: () => void; notify: (m: string, c?: string) => void }) {
  const [rep, setRep] = useState<IndexReport | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { getDocumentIndex(doc.id).then(setRep).catch(() => setRep(null)); }, [doc.id]);
  useEffect(() => { load(); }, [load]);
  async function run() {
    setBusy(true);
    try {
      const job = await parseIndex(doc.id);
      notify("Parseando índice…");
      streamJob(job.id, () => {}, () => { setBusy(false); load(); notify("Índice procesado", "var(--ok)"); });
    } catch (err) { setBusy(false); notify((err as Error).message, "var(--danger)"); }
  }
  const missing = (rep?.entries || []).filter((e) => e.matched === false);
  return (
    <div onClick={onClose} style={ov}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...card, maxWidth: 620 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, margin: 0 }}>Índice de «{doc.title}»</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 14px" }}>Parsea las páginas marcadas como «índice» (nombre→folio) y las cruza con las actas extraídas para detectar las que faltan. Marca las páginas de índice en el Visor (botón ▦ Mosaico).</p>
        <button onClick={run} disabled={busy} style={{ ...primaryBtn, marginBottom: 14 }}>{busy ? "Procesando…" : (rep && rep.total > 0 ? "Re-parsear índice" : "Parsear índice")}</button>
        {rep && rep.total > 0 && (
          <>
            <div style={{ display: "flex", gap: 18, marginBottom: 12, fontSize: 13.5, flexWrap: "wrap" }}>
              <span><b>{rep.total}</b> entradas</span>
              <span style={{ color: "var(--ok)" }}><b>{rep.matched}</b> con acta</span>
              <span style={{ color: missing.length ? "var(--danger)" : "var(--muted)" }}><b>{rep.missing}</b> sin acta (posibles faltas)</span>
            </div>
            {missing.length > 0 && (
              <div style={{ maxHeight: 300, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 10 }}>
                {missing.map((en) => (
                  <div key={en.id} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "8px 12px", borderBottom: "1px solid var(--line2)", fontSize: 13 }}>
                    <span>{en.name_raw || "(sin nombre)"}</span>
                    <span style={{ fontFamily: fonts.mono, fontSize: 11.5, color: "var(--muted)", flex: "none" }}>{en.folio_label ? `folio ${en.folio_label}` : ""}{en.record_no ? ` · acta ${en.record_no}` : ""}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
        {rep && rep.total === 0 && <div style={{ color: "var(--muted)", fontSize: 13 }}>Aún no hay índice parseado. Marca las páginas de índice en el mosaico del Visor y pulsa «Parsear índice».</div>}
      </div>
    </div>
  );
}

// Minimal word-diff: highlight words that differ between the two strings (greedy LCS).
function wordDiff(a: string, b: string): { a: { w: string; same: boolean }[]; b: { w: string; same: boolean }[] } {
  const aw = (a || "").split(/\s+/), bw = (b || "").split(/\s+/);
  const n = aw.length, m = bw.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = aw[i] === bw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const ra: { w: string; same: boolean }[] = [], rb: { w: string; same: boolean }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (aw[i] === bw[j]) { ra.push({ w: aw[i], same: true }); rb.push({ w: bw[j], same: true }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ra.push({ w: aw[i], same: false }); i++; }
    else { rb.push({ w: bw[j], same: false }); j++; }
  }
  while (i < n) ra.push({ w: aw[i++], same: false });
  while (j < m) rb.push({ w: bw[j++], same: false });
  return { a: ra, b: rb };
}

function DiffText({ parts }: { parts: { w: string; same: boolean }[] }) {
  return <div style={{ fontFamily: fonts.serif, fontSize: 13, lineHeight: 1.55, whiteSpace: "pre-wrap" }}>
    {parts.map((p, i) => <span key={i} style={p.same ? undefined : { background: "rgba(217,83,30,.18)", borderRadius: 3 }}>{p.w}{" "}</span>)}
  </div>;
}

function ReconcilePanel({ doc, onClose, onDone }: { doc: DocumentOut; onClose: () => void; onDone: () => void }) {
  const e = useEstela();
  const [versions, setVersions] = useState<VersionPair[] | null>(null);
  const [mode, setMode] = useState<"substitute" | "mix" | "manual">("substitute");
  const [criterion, setCriterion] = useState<"frequency" | "llm">("frequency");
  const [keepHistory, setKeepHistory] = useState(true);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [idx, setIdx] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => { getVersions(doc.id).then(setVersions).catch(() => setVersions([])); }, [doc.id]);
  const pending = (versions ?? []).filter((v) => v.candidate);

  async function apply() {
    setBusy(true);
    const body: ReconcileBody = { mode, keep_history: keepHistory };
    if (mode === "mix") body.criterion = criterion;
    if (mode === "manual") body.choices = choices;
    try { await reconcile(doc.id, body); onDone(); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); setBusy(false); }
  }

  const cur = pending[idx];
  return (
    <div onClick={onClose} style={ov}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ ...card, maxWidth: mode === "manual" ? 980 : 560 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, margin: 0 }}>Reconciliar «{doc.title}»</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        {versions === null && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Cargando versiones…</div>}
        {versions && pending.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>No hay una versión nueva pendiente. Usa «Re-reconocer» primero.</div>}
        {pending.length > 0 && <>
          <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 14px" }}>{pending.length} páginas con una nueva transcripción candidata.</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
            {([["substitute", "Sustituir por la nueva"], ["mix", "Mezcla"], ["manual", "Revisión manual"]] as const).map(([m, l]) => (
              <button key={m} onClick={() => setMode(m)} style={{ background: mode === m ? "var(--accent)" : "transparent", color: mode === m ? "#fff" : "var(--fg)", border: "1px solid var(--line)", borderRadius: 7, padding: "7px 12px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>{l}</button>
            ))}
          </div>
          {mode === "mix" && (
            <div style={{ display: "flex", gap: 14, marginBottom: 12, fontSize: 13 }}>
              <label style={{ display: "inline-flex", gap: 6, alignItems: "center", cursor: "pointer" }}><input type="radio" checked={criterion === "frequency"} onChange={() => setCriterion("frequency")} /> Frecuencia en el libro</label>
              <label style={{ display: "inline-flex", gap: 6, alignItems: "center", cursor: "pointer" }}><input type="radio" checked={criterion === "llm"} onChange={() => setCriterion("llm")} /> Reconciliar con LLM</label>
            </div>
          )}
          {mode === "manual" && cur && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, fontFamily: fonts.mono, fontSize: 12 }}>
                <button onClick={() => setIdx((n) => Math.max(0, n - 1))} style={navMini}>‹</button>
                <span>página {cur.page_no} · {idx + 1}/{pending.length}</span>
                <button onClick={() => setIdx((n) => Math.min(pending.length - 1, n + 1))} style={navMini}>›</button>
                <span style={{ marginLeft: "auto", color: choices[cur.page_no] ? "var(--ok)" : "var(--muted)" }}>{choices[cur.page_no] === "old" ? "→ vieja" : choices[cur.page_no] === "new" ? "→ nueva" : choices[cur.page_no] ? "→ editada" : "sin decidir"}</span>
              </div>
              {(() => { const d = wordDiff(cur.active?.text ?? "", cur.candidate?.text ?? ""); return (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div style={{ background: "var(--bg)", border: `1px solid ${choices[cur.page_no] === "old" ? "var(--accent)" : "var(--line2)"}`, borderRadius: 10, padding: 12 }}>
                    <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginBottom: 6 }}>VIEJA · {cur.active?.model ?? cur.active?.engine ?? ""}</div>
                    <DiffText parts={d.a} />
                    <button onClick={() => setChoices((c) => ({ ...c, [cur.page_no]: "old" }))} style={{ ...navMini, marginTop: 8, width: "auto", padding: "5px 12px" }}>Quedarme con la vieja</button>
                  </div>
                  <div style={{ background: "var(--bg)", border: `1px solid ${choices[cur.page_no] === "new" ? "var(--accent)" : "var(--line2)"}`, borderRadius: 10, padding: 12 }}>
                    <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginBottom: 6 }}>NUEVA · {cur.candidate?.model ?? cur.candidate?.engine ?? ""}</div>
                    <DiffText parts={d.b} />
                    <button onClick={() => setChoices((c) => ({ ...c, [cur.page_no]: "new" }))} style={{ ...navMini, marginTop: 8, width: "auto", padding: "5px 12px" }}>Quedarme con la nueva</button>
                  </div>
                </div>
              ); })()}
            </div>
          )}
          <label style={{ display: "inline-flex", gap: 7, alignItems: "center", fontSize: 13, marginBottom: 14, cursor: "pointer" }}>
            <input type="checkbox" checked={keepHistory} onChange={(ev) => setKeepHistory(ev.target.checked)} /> Guardar la versión anterior como histórico (no borrarla)
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <button disabled={busy} onClick={apply} style={primaryBtn}>{busy ? "Aplicando…" : "Aplicar"}</button>
            <button onClick={onClose} style={{ ...primaryBtn, background: "transparent", color: "var(--fg)", border: "1px solid var(--line)" }}>Cancelar</button>
          </div>
        </>}
      </div>
    </div>
  );
}

const navMini: React.CSSProperties = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, minWidth: 26, height: 26, cursor: "pointer", color: "var(--fg)", fontSize: 13 };

type ItemStatus = "pending" | "uploading" | "done" | "error";

function fmtSize(bytes: number): string {
  if (bytes > 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes > 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  return (bytes / 1e3).toFixed(0) + " KB";
}

interface FileItem { file: File; type: string; yf: string; yt: string; bn: string }

function parseBookNumber(name: string): string {
  // "LIBRO 35", "Llibre 12", "Libro de bautismos 22" → the number of the book in its series
  const m = name.match(/(?:libro|llibre|book|tomo|vol(?:umen)?)\.?\s*(?:de\s+\w+\s+)?(\d{1,3})\b/i);
  return m ? m[1] : "";
}

function parseType(name: string): string {
  const n = name.toLowerCase();
  if (/bautism|baptism|bateig|baptisme/.test(n)) return "baptism";
  if (/matrimoni|casamient|marriage|despos|bodas/.test(n)) return "marriage";
  if (/defunci|[oó]bito|difunt|death|enterram|sepultur/.test(n)) return "death";
  if (/confirmaci/.test(n)) return "confirmation";
  if (/censo electoral/.test(n)) return "electoral_census";
  if (/censo|padr[oó]n|census/.test(n)) return "census";
  if (/testament|will/.test(n)) return "will";
  if (/militar|quinta|reemplaz|levas/.test(n)) return "military";
  return "";
}
function parseYears(name: string): [string, string] {
  const m = name.match(/(1[5-9]\d{2}|20\d{2})\s*[-–—/]\s*(1[5-9]\d{2}|20\d{2})/);
  if (m) return [m[1], m[2]];
  const s = name.match(/(1[5-9]\d{2}|20\d{2})/);
  return s ? [s[1], ""] : ["", ""];
}
function toItem(f: File): FileItem {
  const [yf, yt] = parseYears(f.name);
  return { file: f, type: parseType(f.name), yf, yt, bn: parseBookNumber(f.name) };
}

function UploadForm({ initial = [], onDone }: { initial?: File[]; onDone: () => void }) {
  const [items, setItems] = useState<FileItem[]>(() => initial.map(toItem));
  const [origin, setOrigin] = useState("own_photo");
  const [living, setLiving] = useState(false);
  const [isIndex, setIsIndex] = useState(false);
  const [policy, setPolicy] = useState<"retain" | "data_only">("retain");
  const [municipality, setMunicipality] = useState("");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [types, setTypes] = useState<RecordType[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Record<string, ItemStatus>>({});
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { getRecordTypes().then(setTypes).catch(() => setTypes([])); }, []);

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list).filter((f) => f.type === "application/pdf" || f.type.startsWith("image/") || /\.(pdf|jpe?g|png|tiff?|webp)$/i.test(f.name));
    setItems((prev) => {
      const seen = new Set(prev.map((it) => it.file.name + it.file.size));
      return [...prev, ...incoming.filter((f) => !seen.has(f.name + f.size)).map(toItem)];
    });
  }
  const remove = (i: number) => setItems((prev) => prev.filter((_, j) => j !== i));
  const setItem = (i: number, patch: Partial<FileItem>) => setItems((prev) => prev.map((it, j) => j === i ? { ...it, ...patch } : it));
  const isPdf = (f: File) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);

  async function submit() {
    if (items.length === 0) return;
    setBusy(true);
    const shared = {
      source_origin: origin, may_contain_living: living, image_policy: policy, is_index: isIndex,
      municipality: municipality || undefined, municipality_lat: coords?.lat, municipality_lng: coords?.lng,
    };
    const pdfs = items.filter((it) => isPdf(it.file));
    const imgs = items.filter((it) => !isPdf(it.file));
    const tasks: { key: string; title: string; payload: File[]; flags: UploadFlags }[] = [
      ...pdfs.map((it) => ({
        key: it.file.name + it.file.size, title: it.file.name.replace(/\.pdf$/i, ""), payload: [it.file],
        flags: { ...shared, record_type: it.type || undefined, year_from: it.yf ? Number(it.yf) : undefined, year_to: it.yt ? Number(it.yt) : undefined, book_number: it.bn ? Number(it.bn) : undefined } as UploadFlags,
      })),
      ...(imgs.length ? [{
        key: "__imgs__", title: imgs[0].file.name.replace(/\.[^.]+$/, "") || "Libro de imágenes", payload: imgs.map((it) => it.file),
        flags: { ...shared, record_type: imgs[0].type || undefined, year_from: imgs[0].yf ? Number(imgs[0].yf) : undefined, year_to: imgs[0].yt ? Number(imgs[0].yt) : undefined, book_number: imgs[0].bn ? Number(imgs[0].bn) : undefined } as UploadFlags,
      }] : []),
    ];
    for (const t of tasks) {
      setStatus((s) => ({ ...s, [t.key]: "uploading" }));
      try { await uploadDocument(t.title, t.payload, t.flags); setStatus((s) => ({ ...s, [t.key]: "done" })); }
      catch { setStatus((s) => ({ ...s, [t.key]: "error" })); }
    }
    setBusy(false);
    onDone();
  }

  const field: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 12px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5 };
  const mini: React.CSSProperties = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, padding: "5px 7px", color: "var(--fg)", fontFamily: "inherit", fontSize: 11.5 };
  const pdfCount = items.filter((it) => isPdf(it.file)).length;

  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 22, marginBottom: 22, boxShadow: "var(--shadow)" }}>
      <h3 style={{ margin: "0 0 16px", fontSize: 16, fontWeight: 600 }}>Añadir libros</h3>

      {/* dropzone */}
      <div
        onClick={() => fileRef.current?.click()}
        onDragOver={(ev) => { ev.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(ev) => { ev.preventDefault(); setDragOver(false); addFiles(ev.dataTransfer.files); }}
        style={{ border: `2px dashed ${dragOver ? "var(--accent)" : "var(--line)"}`, background: dragOver ? "var(--accent-faint)" : "var(--bg)", borderRadius: 12, padding: "26px 18px", textAlign: "center", cursor: "pointer", transition: "all .15s" }}>
        <div style={{ fontFamily: fonts.serif, fontSize: 17, fontWeight: 600, marginBottom: 6 }}>Arrastra tus PDFs aquí</div>
        <div style={{ color: "var(--muted)", fontSize: 13 }}>o haz clic para elegir · cada PDF se añade como un libro · también imágenes sueltas</div>
        <input ref={fileRef} type="file" accept=".pdf,image/*" multiple onChange={(ev) => ev.target.files && addFiles(ev.target.files)} style={{ display: "none" }} />
      </div>

      {/* selected files — each with its own type + year range (auto-detected from the filename) */}
      {items.length > 0 && (
        <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 6, maxHeight: 320, overflowY: "auto" }}>
          {items.map((it, i) => {
            const key = it.file.name + it.file.size;
            return (
              <div key={key} style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 8, padding: "7px 10px", flexWrap: "wrap" }}>
                <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--accent)", flex: "none" }}>{isPdf(it.file) ? "PDF" : "IMG"}</span>
                <span style={{ flex: "1 1 200px", minWidth: 120, fontSize: 12.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={it.file.name}>{it.file.name}</span>
                <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", flex: "none" }}>{fmtSize(it.file.size)}</span>
                <select style={{ ...mini, flex: "none", maxWidth: 130 }} value={it.type} onChange={(ev) => setItem(i, { type: ev.target.value })} title="Tipo de documento">
                  <option value="">(tipo)</option>
                  {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                </select>
                <input style={{ ...mini, width: 54, flex: "none" }} value={it.yf} onChange={(ev) => setItem(i, { yf: ev.target.value })} placeholder="desde" />
                <input style={{ ...mini, width: 54, flex: "none" }} value={it.yt} onChange={(ev) => setItem(i, { yt: ev.target.value })} placeholder="hasta" />
                <input style={{ ...mini, width: 48, flex: "none" }} value={it.bn} onChange={(ev) => setItem(i, { bn: ev.target.value.replace(/\D/g, "") })} placeholder="nº" title="Nº de libro en la serie (parroquia+tipo)" />
                {status[key] === "uploading" && <span style={{ fontSize: 11, color: "var(--accent)" }}>subiendo…</span>}
                {status[key] === "done" && <span style={{ fontSize: 11, color: "var(--ok)" }}>✓</span>}
                {status[key] === "error" && <span style={{ fontSize: 11, color: "var(--danger)" }}>error</span>}
                {!busy && <span onClick={() => remove(i)} style={{ cursor: "pointer", color: "var(--muted)", fontSize: 14, flex: "none" }}>✕</span>}
              </div>
            );
          })}
          <div style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
            {pdfCount} PDF{pdfCount !== 1 ? "s" : ""}{items.length - pdfCount > 0 ? ` · ${items.length - pdfCount} imágenes` : ""} · tipo, años y nº de libro por libro (auto-detectados del nombre)
          </div>
        </div>
      )}

      {/* shared metadata — same for all the files being added */}
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 14, marginTop: 16 }}>
        <MunicipalityInput field={field} value={municipality} onPick={(name, c) => { setMunicipality(name); setCoords(c); }} />
        <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>
          Origen de la fuente
          <select style={field} value={origin} onChange={(ev) => setOrigin(ev.target.value)}>
            {ORIGINS.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>
          Política de imagen
          <select style={field} value={policy} onChange={(ev) => setPolicy(ev.target.value as "retain" | "data_only")}>
            <option value="retain">Conservar imagen (mostrar la página fuente)</option>
            <option value="data_only">Solo datos (no guardar la imagen)</option>
          </select>
        </label>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 14, fontSize: 13, color: "var(--fg)", cursor: "pointer" }}>
        <input type="checkbox" checked={living} onChange={(ev) => setLiving(ev.target.checked)} />
        Pueden contener personas vivas <span style={{ color: "var(--muted)", fontSize: 12 }}>(marca para aplicar reglas GDPR)</span>
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 8, fontSize: 13, color: "var(--fg)", cursor: "pointer" }}>
        <input type="checkbox" checked={isIndex} onChange={(ev) => setIsIndex(ev.target.checked)} />
        Es un índice <span style={{ color: "var(--muted)", fontSize: 12 }}>(lista nombre→folio; no se extrae como actas, se cruza con el libro)</span>
      </label>
      <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
        <button onClick={submit} disabled={busy || items.length === 0} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "10px 18px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: busy ? "default" : "pointer", opacity: busy || items.length === 0 ? 0.6 : 1 }}>
          {busy ? "Subiendo…" : `Subir ${items.length || ""} libro${items.length !== 1 ? "s" : ""}`}
        </button>
        <button onClick={onDone} style={{ background: "transparent", color: "var(--muted)", border: "1px solid var(--line)", borderRadius: 8, padding: "10px 18px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>Cancelar</button>
      </div>
    </div>
  );
}

function MunicipalityInput({ field, value, onPick }: { field: React.CSSProperties; value: string; onPick: (name: string, coords: { lat: number; lng: number } | null) => void }) {
  const [q, setQ] = useState(value);
  const [results, setResults] = useState<GeoResult[]>([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    const t = setTimeout(() => geoSearch(q).then(setResults).catch(() => setResults([])), 350);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)", position: "relative" }}>
      Municipio de origen
      <input style={field} value={q}
        onChange={(ev) => { setQ(ev.target.value); onPick(ev.target.value, null); setOpen(true); }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder="Escribe y elige…" />
      {open && results.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30, background: "var(--elevated)", border: "1px solid var(--line)", borderRadius: 8, marginTop: 4, boxShadow: "var(--shadow)", maxHeight: 220, overflowY: "auto" }}>
          {results.map((r, i) => (
            <div key={i} onMouseDown={() => { setQ(r.name); onPick(r.name, { lat: r.lat, lng: r.lng }); setOpen(false); }}
              style={{ padding: "8px 12px", cursor: "pointer", borderBottom: i < results.length - 1 ? "1px solid var(--line2)" : "none" }}>
              <div style={{ fontWeight: 600, fontSize: 13, color: "var(--fg)" }}>{r.name}</div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>{r.display_name}</div>
            </div>
          ))}
        </div>
      )}
    </label>
  );
}
