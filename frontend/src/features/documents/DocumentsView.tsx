import { useCallback, useEffect, useRef, useState } from "react";
import {
  type DocumentOut,
  type PageOut,
  deleteDoc,
  fetchPageObjectUrl,
  getPages,
  listDocuments,
  publishDoc,
  unpublishDoc,
  uploadDocument,
} from "../../api/documents";
import {
  type TranscriptionOut,
  getJob,
  getTranscriptions,
  isTerminal,
  startTranscription,
} from "../../api/transcription";

const ENGINES = ["tesseract", "claude", "openai", "openrouter", "ollama"];

const RIGHTS = ["owner", "public_domain", "licensed", "permission_granted"];

export default function DocumentsView({ onError }: { onError: (e: string) => void }) {
  const [scope, setScope] = useState<"mine" | "public">("mine");
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [selected, setSelected] = useState<DocumentOut | null>(null);
  const [pages, setPages] = useState<PageOut[]>([]);
  const [preview, setPreview] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [rights, setRights] = useState("owner");
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [engine, setEngine] = useState("");
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [trans, setTrans] = useState<TranscriptionOut[]>([]);

  const load = useCallback(async () => {
    try {
      setDocs(await listDocuments(scope));
    } catch (e) {
      onError((e as Error).message);
    }
  }, [scope, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(doc: DocumentOut) {
    setSelected(doc);
    setPreview(null);
    setJobStatus(null);
    try {
      const ps = await getPages(doc.id);
      setPages(ps);
      setTrans(await getTranscriptions(doc.id));
      if (ps[0]?.content_type?.startsWith("image/")) {
        setPreview(await fetchPageObjectUrl(doc.id, ps[0].page_no));
      }
    } catch (e) {
      onError((e as Error).message);
    }
  }

  async function transcribe() {
    if (!selected) return;
    setTrans([]);
    setJobStatus("encolado…");
    try {
      const job = await startTranscription(selected.id, engine ? { engine } : {});
      let j = job;
      while (!isTerminal(j.status)) {
        await new Promise((r) => setTimeout(r, 1500));
        j = await getJob(job.id);
        setJobStatus(
          j.progress ? `${j.status} (${j.progress.done ?? 0}/${j.progress.total ?? "?"})` : j.status,
        );
      }
      if (j.status === "completed") {
        setJobStatus("completado");
        setTrans(await getTranscriptions(selected.id));
      } else {
        setJobStatus(j.status);
        if (j.error) onError(j.error);
      }
    } catch (e) {
      onError((e as Error).message);
      setJobStatus(null);
    }
  }

  async function doUpload() {
    const files = fileRef.current?.files;
    if (!title || !files?.length) return;
    try {
      await uploadDocument(title, files, { visibility, rights: visibility === "public" ? rights : "" });
      setTitle("");
      if (fileRef.current) fileRef.current.value = "";
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  }

  async function togglePublish(doc: DocumentOut) {
    try {
      if (doc.visibility === "public") await unpublishDoc(doc.id);
      else await publishDoc(doc.id, prompt("Declaración de derechos:", "owner") || "owner");
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  }

  async function remove(doc: DocumentOut) {
    if (!confirm(`¿Eliminar "${doc.title}"?`)) return;
    try {
      await deleteDoc(doc.id);
      if (selected?.id === doc.id) setSelected(null);
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Documentos</strong>
        <div className="tabs">
          <button className={scope === "mine" ? "" : "secondary"} onClick={() => setScope("mine")}>
            Míos
          </button>
          <button className={scope === "public" ? "" : "secondary"} onClick={() => setScope("public")}>
            Biblioteca pública
          </button>
        </div>
      </div>

      {scope === "mine" && (
        <div className="row" style={{ marginTop: ".5rem", flexWrap: "wrap" }}>
          <input placeholder="Título del documento" value={title} onChange={(e) => setTitle(e.target.value)} />
          <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
            <option value="private">Privado</option>
            <option value="public">Público</option>
          </select>
          {visibility === "public" && (
            <select value={rights} onChange={(e) => setRights(e.target.value)}>
              {RIGHTS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          )}
          <input ref={fileRef} type="file" multiple accept="image/*,application/pdf" />
          <button onClick={doUpload} disabled={!title}>
            Subir
          </button>
        </div>
      )}
      {visibility === "public" && scope === "mine" && (
        <p className="muted" style={{ fontSize: ".8rem" }}>
          Al publicar declaras que tienes derechos para compartir este documento.
        </p>
      )}

      <div style={{ display: "flex", gap: "1rem", marginTop: ".75rem" }}>
        <ul className="list" style={{ flex: 1 }}>
          {docs.map((d) => (
            <li key={d.id}>
              <span style={{ cursor: "pointer" }} onClick={() => open(d)}>
                {d.title}{" "}
                <span className="badge">{d.doc_type}</span>{" "}
                <span className="badge" style={d.visibility === "public" ? { background: "#34d39933", color: "#34d399" } : {}}>
                  {d.visibility}
                </span>{" "}
                <span className="muted">{d.page_count} pág.</span>
              </span>
              {scope === "mine" && (
                <span className="row">
                  {d.source_kind !== "familysearch" && (
                    <button className="secondary" onClick={() => togglePublish(d)}>
                      {d.visibility === "public" ? "Despublicar" : "Publicar"}
                    </button>
                  )}
                  <button className="secondary" onClick={() => remove(d)}>
                    🗑
                  </button>
                </span>
              )}
            </li>
          ))}
          {docs.length === 0 && <li className="muted">Sin documentos.</li>}
        </ul>

        {selected && (
          <div style={{ width: 300 }}>
            <div className="muted" style={{ fontSize: ".8rem" }}>
              {selected.title} · {pages.length} elemento(s)
            </div>
            {preview ? (
              <img src={preview} alt="página 1" style={{ width: "100%", borderRadius: 8, marginTop: ".5rem" }} />
            ) : (
              <p className="muted">{pages[0]?.content_type || "—"}</p>
            )}

            <div className="row" style={{ flexWrap: "wrap", marginTop: ".6rem" }}>
              <select value={engine} onChange={(e) => setEngine(e.target.value)}>
                <option value="">motor asignado</option>
                {ENGINES.map((en) => (
                  <option key={en} value={en}>
                    {en}
                  </option>
                ))}
              </select>
              <button onClick={transcribe}>Transcribir</button>
            </div>
            {jobStatus && (
              <p className="muted" style={{ fontSize: ".8rem" }}>
                Estado: {jobStatus}
              </p>
            )}
            {trans.map((t) => (
              <div key={t.id} style={{ marginTop: ".4rem" }}>
                <div className="muted" style={{ fontSize: ".72rem" }}>
                  pág. {t.page_no} · {t.engine} · {t.status}
                </div>
                <div
                  style={{
                    fontSize: ".8rem",
                    whiteSpace: "pre-wrap",
                    maxHeight: 160,
                    overflow: "auto",
                    background: "#0b1220",
                    padding: ".4rem",
                    borderRadius: 6,
                  }}
                >
                  {t.text || "—"}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
