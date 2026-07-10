import { useCallback, useEffect, useRef, useState } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import { getConnectors } from "../../../api/connectors";
import { getMe } from "../../../api/account";
import {
  startFsDownload, listFsCredentials, addFsCredential, deleteFsCredential, type FsCredential,
} from "../../../api/familysearch";
import { listDocuments, compactPdf, type DocumentOut } from "../../../api/documents";
import { startTranscription } from "../../../api/transcription";
import { streamJob, jobOutcome } from "../../../api/jobs";
import { field, ghostBtn, primaryBtn } from "../ui";

export default function FsView() {
  const e = useEstela();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [progress, setProgress] = useState<Record<string, { label: string; done: number; total: number }>>({});
  const streamed = useRef<Set<string>>(new Set());

  const loadDocs = useCallback(() => {
    listDocuments("mine").then((d) => setDocs(d.filter((x) => (x.source_kind || "").startsWith("familysearch")))).catch(() => setDocs([]));
  }, []);
  useEffect(() => {
    getConnectors().then((c) => setEnabled(c.enabled.some((x) => x.name === "familysearch"))).catch(() => setEnabled(false));
    getMe().then((m) => setIsAdmin(m.user.is_server_admin)).catch(() => setIsAdmin(false));
    loadDocs();
  }, [loadDocs]);

  const track = useCallback((key: string, label: string, jobId: string) => {
    if (streamed.current.has(jobId)) return;
    streamed.current.add(jobId);
    setProgress((p) => ({ ...p, [key]: { label, done: 0, total: 0 } }));
    streamJob(jobId,
      (ev) => setProgress((p) => ({ ...p, [key]: { label, done: ev.done ?? (ev as { downloaded?: number }).downloaded ?? 0, total: ev.total ?? 0 } })),
      (last) => {
        setProgress((p) => { const n = { ...p }; delete n[key]; return n; });
        streamed.current.delete(jobId); loadDocs();
        const outcome = jobOutcome(last);
        if (outcome === "ok") e.notify(`${label}: completado`, "var(--ok)");
        else if (outcome === "cancelled") e.notify(`${label}: cancelado`, "var(--muted)");
        else if (outcome === "error") e.notify(`${label}: error${last?.error ? ` — ${last.error}` : ""}`, "var(--danger)");
        else e.notify(`${label}: conexión perdida; sigue en segundo plano`, "var(--muted)");
      });
  }, [loadDocs, e]);

  if (enabled === null) return <section style={{ padding: "32px 44px" }}><p style={{ color: "var(--muted)" }}>Cargando…</p></section>;

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 980 }}>
      <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>FamilySearch</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 22px", maxWidth: 720 }}>
        Descarga libros/películas de FamilySearch (privados o públicos) como documentos privados. Cada descarga conserva su <b>origen</b> (URL + ARK de cada imagen) para trazabilidad de principio a fin; luego puedes compactarla en un PDF y transcribirla.
      </p>

      {!enabled && (
        <div style={{ background: "var(--warn-faint)", border: "1px solid var(--warn)", color: "var(--warn)", borderRadius: 12, padding: 16, fontSize: 13.5 }}>
          El conector de FamilySearch está <b>desactivado</b>. El operador del servidor debe poner <code style={{ fontFamily: fonts.mono }}>FS_CONNECTOR_ENABLED</code> y reiniciar. (FamilySearch no permite descarga programática salvo bajo responsabilidad del operador.)
        </div>
      )}

      {enabled && (
        <>
          {isAdmin && <FsCredentials />}

          {/* download form */}
          <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 20, marginBottom: 18 }}>
            <h3 style={{ margin: "0 0 12px", fontSize: 16, fontWeight: 600 }}>Descargar un libro</h3>
            <DownloadForm onStarted={(jobId) => track(`dl:${jobId}`, "Descargando", jobId)} />
            <p style={{ color: "var(--muted)", fontSize: 12, margin: "10px 0 0" }}>La descarga es privada y no se puede publicar.</p>
          </div>

          {/* my downloads */}
          <h3 style={{ margin: "0 0 12px", fontSize: 16, fontWeight: 600 }}>Mis descargas</h3>
          {docs.length === 0 && <p style={{ color: "var(--muted)", fontSize: 13.5 }}>Aún no has descargado nada.</p>}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(320px,1fr))", gap: 14 }}>
            {docs.map((d) => {
              const pk = progress[`compact:${d.id}`];
              return (
                <div key={d.id} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 16 }}>
                  <div style={{ fontFamily: fonts.serif, fontSize: 15.5, fontWeight: 600, lineHeight: 1.2 }}>{d.title}</div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
                    <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{d.page_count} págs · {d.doc_type === "pdf" ? "PDF" : "imágenes"}</span>
                    {d.derived_from_id && <span style={{ fontFamily: fonts.mono, fontSize: 9.5, padding: "2px 6px", borderRadius: 4, background: "var(--bg)", border: "1px solid var(--line2)", color: "var(--muted)" }}>derivado</span>}
                  </div>
                  {d.source_ref && <a href={d.source_ref} target="_blank" rel="noreferrer" style={{ display: "block", marginTop: 6, fontSize: 11.5, color: "var(--accent)", wordBreak: "break-all" }}>Origen: {d.source_ref} ↗</a>}
                  {pk && (
                    <div style={{ marginTop: 10 }}>
                      <div style={{ height: 6, borderRadius: 999, background: "var(--line2)", overflow: "hidden" }}><div style={{ height: "100%", width: pk.total ? `${Math.round((pk.done / pk.total) * 100)}%` : "10%", background: "linear-gradient(90deg,var(--accent),var(--gold))" }} /></div>
                      <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginTop: 5 }}>{pk.label} · {pk.done}/{pk.total || "…"}</div>
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                    <button onClick={() => e.openDoc(d.id)} style={ghostBtn}>Abrir</button>
                    {d.doc_type === "image_set" && <button onClick={() => compactPdf(d.id).then((j) => track(`compact:${d.id}`, "Compactando PDF", j.id)).catch((err) => e.notify((err as Error).message, "var(--danger)"))} style={ghostBtn}>Compactar en PDF</button>}
                    <button onClick={() => startTranscription(d.id).then((j) => track(`tx:${d.id}`, "Transcribiendo", j.id)).catch((err) => e.notify((err as Error).message, "var(--danger)"))} style={ghostBtn}>Transcribir</button>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

function DownloadForm({ onStarted }: { onStarted: (jobId: string) => void }) {
  const e = useEstela();
  const [url, setUrl] = useState("");
  const [maxImages, setMaxImages] = useState("");
  const [busy, setBusy] = useState(false);
  async function go() {
    if (!url.trim()) return;
    setBusy(true);
    try {
      const j = await startFsDownload({ url: url.trim(), max_images: maxImages ? Number(maxImages) : undefined });
      onStarted(j.id); setUrl(""); e.notify("Descarga iniciada…", "var(--accent)");
    } catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    setBusy(false);
  }
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
      <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)", flex: 1, minWidth: 280 }}>URL de FamilySearch<input style={field} value={url} onChange={(ev) => setUrl(ev.target.value)} placeholder="https://www.familysearch.org/ark:/61903/3:1:..." /></label>
      <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Máx. imágenes<input style={{ ...field, width: 110 }} value={maxImages} onChange={(ev) => setMaxImages(ev.target.value.replace(/\D/g, ""))} placeholder="todas" /></label>
      <button onClick={go} disabled={busy} style={primaryBtn}>{busy ? "Iniciando…" : "Descargar"}</button>
    </div>
  );
}

function FsCredentials() {
  const e = useEstela();
  const [creds, setCreds] = useState<FsCredential[]>([]);
  const [label, setLabel] = useState("");
  const [cookies, setCookies] = useState("");
  const load = () => listFsCredentials().then(setCreds).catch(() => setCreds([]));
  useEffect(() => { load(); }, []);
  async function add() {
    if (!label.trim() || !cookies.trim()) return;
    try { await addFsCredential({ label: label.trim(), cookies_json: cookies.trim() }); setLabel(""); setCookies(""); load(); e.notify("Credencial añadida", "var(--ok)"); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
  }
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 20, marginBottom: 18 }}>
      <h3 style={{ margin: "0 0 6px", fontSize: 16, fontWeight: 600 }}>Credenciales (operador)</h3>
      <p style={{ color: "var(--muted)", fontSize: 12.5, margin: "0 0 12px" }}>Pega las cookies de sesión exportadas de tu navegador (deben incluir <code style={{ fontFamily: fonts.mono }}>fssessionid</code>). Se guardan cifradas. Solo server-admin.</p>
      {creds.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
          {creds.map((c) => (
            <div key={c.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 8, padding: "8px 12px" }}>
              <span style={{ fontSize: 13 }}>{c.label} {c.is_active ? "" : <span style={{ color: "var(--muted)" }}>(inactiva)</span>}</span>
              <button onClick={() => deleteFsCredential(c.id).then(load)} style={{ ...ghostBtn, color: "var(--danger)", border: "1px solid var(--danger)" }}>Borrar</button>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <input style={field} value={label} onChange={(ev) => setLabel(ev.target.value)} placeholder="Etiqueta (p.ej. mi sesión)" />
        <textarea style={{ ...field, minHeight: 70, resize: "vertical", fontFamily: fonts.mono, fontSize: 11.5 }} value={cookies} onChange={(ev) => setCookies(ev.target.value)} placeholder='[{"name":"fssessionid","value":"…"}, …]' />
        <button onClick={add} style={{ ...primaryBtn, alignSelf: "flex-start" }}>Añadir credencial</button>
      </div>
    </div>
  );
}
