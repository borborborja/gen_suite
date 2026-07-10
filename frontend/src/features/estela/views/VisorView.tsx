import { useCallback, useEffect, useRef, useState } from "react";
import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity, type ZoomBehavior } from "d3-zoom";
import { useEstela } from "../store";
import { fonts } from "../theme";
import { roleLabel } from "../labels";
import { ArrowLeft } from "../icons";
import { fetchPageObjectUrl, getPages, getDocument, setPageKind, splitDocument, type PageOut, type DocumentOut } from "../../../api/documents";
import { getTranscriptions, correctTranscription, type TranscriptionOut } from "../../../api/transcription";
import { documentRecords, reextract, mergeNext, splitRecord, type ExtractedRecordOut, type MentionLite } from "../../../api/extraction";
import { streamJob, jobOutcome } from "../../../api/jobs";

export default function VisorView() {
  const e = useEstela();
  if (!e.selDoc) return <SampleVisor />;
  return <LiveVisor docId={e.selDoc} />;
}

function LiveVisor({ docId }: { docId: string }) {
  const e = useEstela();
  const [pages, setPages] = useState<PageOut[]>([]);
  const [doc, setDoc] = useState<DocumentOut | null>(null);
  const [pageNo, setPageNo] = useState(e.selPage ?? 1);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [imgGone, setImgGone] = useState(false);
  const [txs, setTxs] = useState<TranscriptionOut[]>([]);
  const [records, setRecords] = useState<ExtractedRecordOut[]>([]);
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [mosaic, setMosaic] = useState(false);
  const [treeOpen, setTreeOpen] = useState<Set<string>>(new Set());
  const [retxMsg, setRetxMsg] = useState<string | null>(null);
  const reloadPages = useCallback(() => { void getPages(docId).then(setPages).catch(() => {}); }, [docId]);

  const reloadData = useCallback(async () => {
    setLoadErr(null);
    try {
      setTxs(await getTranscriptions(docId));
      setRecords(await documentRecords(docId));
    } catch (err) {
      // Surface the error instead of showing an empty panel that looks like "not processed yet".
      setLoadErr((err as Error).message || "error al cargar");
    }
  }, [docId]);

  useEffect(() => { void getPages(docId).then(setPages).catch(() => setPages([])); void getDocument(docId).then(setDoc).catch(() => setDoc(null)); void reloadData(); }, [docId, reloadData]);

  // load page image
  useEffect(() => {
    let url: string | null = null;
    setImgGone(false); setImgUrl(null);
    fetchPageObjectUrl(docId, pageNo).then((u) => { url = u; setImgUrl(u); }).catch(() => setImgGone(true));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [docId, pageNo]);

  const tx = txs.find((t) => t.page_no === pageNo);
  useEffect(() => { setDraft(tx?.text || ""); setSaved(false); }, [tx?.id, tx?.text]);

  async function save() {
    if (!tx) return;
    await correctTranscription(tx.id, draft);
    setSaved(true);
    await reloadData();
  }

  const curPage = pages.find((p) => p.page_no === pageNo);
  const pageNoOf = (id: string | null) => (id ? pages.find((p) => p.id === id)?.page_no ?? null : null);
  // A record belongs to this view if it STARTS here or, for a split entry, ENDS here.
  const pageRecords = records.filter(
    (r) => !r.page_id || r.page_id === curPage?.id || r.page_end_id === curPage?.id,
  );

  async function doMerge(id: string) { try { await mergeNext(id); await reloadData(); } catch (err) { e.notify((err as Error).message, "var(--danger)"); } }
  async function doSplit(id: string) { try { await splitRecord(id); await reloadData(); } catch (err) { e.notify((err as Error).message, "var(--danger)"); } }

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1280 }}>
      <div onClick={() => e.go("biblioteca")} style={{ display: "inline-flex", alignItems: "center", gap: 7, color: "var(--muted)", fontSize: 13, cursor: "pointer", marginBottom: 16 }}>
        <ArrowLeft size={16} />Volver a la Biblioteca
      </div>

      {loadErr && (
        <div style={{ background: "var(--danger-faint, rgba(192,57,43,.08))", border: "1px solid var(--danger)", color: "var(--danger)", borderRadius: 10, padding: "11px 15px", marginBottom: 16, fontSize: 13 }}>
          No se pudieron cargar la transcripción/actas de este libro: {loadErr}. <span style={{ textDecoration: "underline", cursor: "pointer" }} onClick={() => reloadData()}>Reintentar</span>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 18, flexWrap: "wrap" }}>
        <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 26, margin: 0 }}>Visor</h1>
        {pages.length > 1 && (
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontFamily: fonts.mono, fontSize: 12.5 }}>
            <button onClick={() => setPageNo((n) => Math.max(1, n - 1))} style={navBtn}>‹</button>
            <span style={{ color: "var(--muted)" }}>página {pageNo} / {pages.length}{curPage?.folio_label ? ` · folio ${curPage.folio_label}` : ""}</span>
            <button onClick={() => setPageNo((n) => Math.min(pages.length, n + 1))} style={navBtn}>›</button>
            <span style={{ color: "var(--muted)", marginLeft: 6 }}>ir a</span>
            <input
              type="number" min={1} max={pages.length} defaultValue={pageNo} key={pageNo}
              onKeyDown={(ev) => { if (ev.key === "Enter") { const n = Math.min(pages.length, Math.max(1, Number((ev.target as HTMLInputElement).value) || 1)); setPageNo(n); } }}
              onBlur={(ev) => { const n = Math.min(pages.length, Math.max(1, Number(ev.target.value) || 1)); setPageNo(n); }}
              style={{ width: 56, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, padding: "4px 6px", color: "var(--fg)", fontFamily: fonts.mono, fontSize: 12.5 }}
            />
          </div>
        )}
        {pages.length > 0 && (
          <button onClick={() => setMosaic((v) => !v)} style={{ ...navBtn, width: "auto", padding: "0 12px", marginLeft: "auto", fontFamily: fonts.mono, fontSize: 12 }}>
            {mosaic ? "✕ Cerrar mosaico" : "▦ Mosaico"}
          </button>
        )}
      </div>

      {mosaic && (
        <PageMosaic docId={docId} pages={pages}
          onOpen={(n) => { setPageNo(n); setMosaic(false); }}
          onChanged={reloadPages} />
      )}

      {(doc?.source_ref || pages.find((p) => p.page_no === pageNo)?.source_ref) && (
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 14, marginTop: -6 }}>
          {doc?.title && <span style={{ fontWeight: 600, color: "var(--fg)" }}>{doc.title}</span>}
          {doc?.source_ref && <> · Origen: <a href={doc.source_ref} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>{doc.source_ref.length > 60 ? doc.source_ref.slice(0, 60) + "…" : doc.source_ref} ↗</a></>}
          {doc?.derived_from_id && <> · <span title="documento derivado (compactado)">derivado</span></>}
          {pages.find((p) => p.page_no === pageNo)?.source_ref && <> · <span style={{ fontFamily: fonts.mono }}>img {pages.find((p) => p.page_no === pageNo)!.source_ref}</span></>}
        </div>
      )}

      {!mosaic && (
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22, alignItems: "start" }}>
        {/* image */}
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, overflow: "hidden", boxShadow: "var(--shadow)" }}>
          {imgGone ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--muted)" }}>
              <div style={{ fontFamily: fonts.serif, fontSize: 17, marginBottom: 8 }}>Imagen descartada</div>
              <div style={{ fontSize: 13 }}>Este libro es «solo datos»: conservamos las actas y su cita, pero no la imagen del documento.</div>
            </div>
          ) : imgUrl ? (
            <ZoomableImage src={imgUrl} alt={`página ${pageNo}`} />
          ) : (
            <div style={{ height: 300, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}>Cargando imagen…</div>
          )}
        </div>

        {/* transcription editor + records */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 20 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".14em", color: "var(--muted)" }}>
                TRANSCRIPCIÓN {tx ? `· ${tx.engine}${tx.status === "corrected" ? " · CORREGIDA" : ""}` : ""}
              </div>
              {tx && <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: tx.status === "corrected" ? "var(--ok)" : "var(--muted)" }}>{tx.status}</span>}
            </div>
            {tx ? (
              <>
                <textarea value={draft} onChange={(ev) => { setDraft(ev.target.value); setSaved(false); }}
                  style={{ width: "100%", minHeight: 220, resize: "vertical", background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: 12, color: "var(--fg)", fontFamily: fonts.serif, fontSize: 14, lineHeight: 1.55, boxSizing: "border-box" }} />
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                  <button onClick={save} disabled={draft === (tx.text || "")} style={{ background: draft === (tx.text || "") ? "var(--line)" : "var(--ok)", color: draft === (tx.text || "") ? "var(--muted)" : "#fff", border: "none", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: draft === (tx.text || "") ? "default" : "pointer" }}>
                    Guardar corrección
                  </button>
                  <button disabled={!!retxMsg && retxMsg.startsWith("Re-extra")} onClick={() => {
                    setSaved(false); setRetxMsg("Re-extrayendo…");
                    reextract(tx.id).then((job) => {
                      streamJob(job.id,
                        (ev) => { if (ev.kind === "page_ok" || ev.kind === "book_start") setRetxMsg("Re-extrayendo… analizando la página"); },
                        (ev) => {
                          const o = jobOutcome(ev);
                          setRetxMsg(o === "ok" ? "✓ Re-extraído" : o === "unknown" ? "✗ Conexión perdida (puede seguir en curso)" : "✗ Falló la re-extracción");
                          reloadData(); setTimeout(() => setRetxMsg(null), 4000);
                        });
                    }).catch(() => setRetxMsg("✗ Error al lanzar"));
                  }} style={{ background: "transparent", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer", opacity: retxMsg && retxMsg.startsWith("Re-extra") ? 0.6 : 1 }}>
                    Re-extraer
                  </button>
                  {retxMsg && <span style={{ fontSize: 12.5, color: retxMsg.startsWith("✓") ? "var(--ok)" : retxMsg.startsWith("✗") ? "var(--danger)" : "var(--accent)" }}>{retxMsg.startsWith("Re-extra") ? "⏳ " : ""}{retxMsg}</span>}
                  {saved && !retxMsg && <span style={{ fontSize: 12.5, color: "var(--ok)" }}>✓ Guardado — texto re-indexado; re-embebiendo esta página. Pulsa «Re-extraer» si cambiaron nombres/fechas de las actas.</span>}
                </div>
              </>
            ) : (
              <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Sin transcripción todavía. Vuelve a la Biblioteca y pulsa «Transcribir».</div>
            )}
          </div>

          <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 20 }}>
            <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".14em", color: "var(--muted)", marginBottom: 12 }}>ACTAS EXTRAÍDAS · {pageRecords.length}</div>
            {pageRecords.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13 }}>Sin actas. Pulsa «Extraer» en la Biblioteca.</div>}
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {pageRecords.map((r) => {
                const startsHere = !r.page_id || r.page_id === curPage?.id;
                const endNo = pageNoOf(r.page_end_id);
                const startNo = pageNoOf(r.page_id);
                return (
                <div key={r.id} style={{ background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 10, padding: "11px 14px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                    <span style={{ fontFamily: fonts.serif, fontSize: 15, fontWeight: 600 }}>
                      {r.record_no ? <span style={{ color: "var(--accent)" }}>Acta {r.record_no} · </span> : ""}
                      {r.record_type} {r.date_year ? `· ${r.date_year}` : ""}
                    </span>
                    <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: r.status === "needs_review" ? "var(--warn)" : "var(--muted)" }}>{r.status}</span>
                  </div>
                  {r.is_continued && (
                    <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--accent)", marginTop: 4 }}>
                      {startsHere ? `↪ continúa en pág. ${endNo ?? "?"}` : `↩ viene de pág. ${startNo ?? "?"}`}
                    </div>
                  )}
                  {(actaDate(r) || r.place) && (
                    <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 4, fontFamily: fonts.mono }}>
                      {actaDate(r)}{actaDate(r) && r.place ? " · " : ""}{r.place ? `📍 ${r.place}` : ""}
                    </div>
                  )}
                  {/* people in the act, grouped by role */}
                  {r.mentions && r.mentions.length > 0 ? (
                    <div style={{ marginTop: 7, display: "flex", flexDirection: "column", gap: 3 }}>
                      {r.mentions.slice(0, treeOpen.has(r.id) ? r.mentions.length : 5).map((m, i) => (
                        <div key={i} style={{ display: "flex", gap: 8, fontSize: 12.5 }}>
                          <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--muted)", textTransform: "uppercase", width: 78, flex: "none", paddingTop: 1 }}>{roleLabel(m.role, r.record_type)}</span>
                          <span style={{ fontWeight: 500 }}>{mName(m)}</span>
                        </div>
                      ))}
                      {!treeOpen.has(r.id) && r.mentions.length > 5 && (
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>+{r.mentions.length - 5} más…</span>
                      )}
                    </div>
                  ) : (
                    <div style={{ fontSize: 12, color: "var(--warn)", marginTop: 6 }}>Sin personas extraídas — pulsa «Re-extraer» en la transcripción.</div>
                  )}
                  {r.summary && <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 6, lineHeight: 1.45, fontStyle: "italic" }}>{r.summary}</div>}
                  {r.sequence_warning && (
                    <div style={{ fontSize: 11.5, color: "var(--warn)", marginTop: 6 }}>⚠ {r.sequence_warning}</div>
                  )}
                  {treeOpen.has(r.id) && r.mentions && r.mentions.length > 0 && <ActaTree mentions={r.mentions} recordType={r.record_type} />}
                  <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                    {r.mentions && r.mentions.length > 0 && (
                      <button onClick={() => setTreeOpen((s) => { const x = new Set(s); x.has(r.id) ? x.delete(r.id) : x.add(r.id); return x; })} style={miniBtn} title="Ver el árbol de esta acta">
                        {treeOpen.has(r.id) ? "▾ Ocultar árbol" : `🌳 Árbol (${r.mentions.length})`}
                      </button>
                    )}
                    {!r.is_continued && startsHere && pageNo < pages.length && (
                      <button onClick={() => doMerge(r.id)} style={miniBtn} title="Unir con el primer acta de la página siguiente">Unir con siguiente ↪</button>
                    )}
                    {r.is_continued && (
                      <button onClick={() => doSplit(r.id)} style={miniBtn} title="Desligar de la segunda hoja">Separar</button>
                    )}
                  </div>
                </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
      )}
    </section>
  );
}

const navBtn: React.CSSProperties = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, width: 26, height: 26, cursor: "pointer", color: "var(--fg)", fontSize: 15 };
const miniBtn: React.CSSProperties = { background: "transparent", border: "1px solid var(--line)", borderRadius: 6, padding: "4px 9px", cursor: "pointer", color: "var(--fg)", fontFamily: "inherit", fontSize: 11.5, fontWeight: 600 };

function mName(m: MentionLite): string {
  return m.name_raw || [m.given, m.surname].filter(Boolean).join(" ") || "—";
}
function actaDate(r: ExtractedRecordOut): string {
  if (r.date_day && r.date_month && r.date_year) return `${r.date_day}/${r.date_month}/${r.date_year}`;
  if (r.date_year) return String(r.date_year);
  return r.date_raw || "";
}

// Compact per-act family diagram: parents on top, the focal person (+ spouse) below, then godparents/others.
function ActaTree({ mentions, recordType }: { mentions: MentionLite[]; recordType?: string | null }) {
  const by = (role: string) => mentions.filter((m) => m.role === role);
  const principal = mentions.find((m) => ["principal", "head", "testator", "defendant", "soldier"].includes(m.role));
  const father = by("father")[0], mother = by("mother")[0], spouse = by("spouse")[0];
  const god = [...by("godfather"), ...by("godmother")];
  const core = new Set(["principal", "head", "testator", "defendant", "soldier", "father", "mother", "spouse", "godfather", "godmother"]);
  const others = mentions.filter((m) => !core.has(m.role));
  const box = (label: string, m: MentionLite | undefined, accent = false) => (
    <div style={{ border: `1px solid ${accent ? "var(--accent)" : "var(--line)"}`, borderRadius: 7, padding: "4px 9px", background: accent ? "var(--accent-faint, rgba(187,125,26,.1))" : "var(--surface)", maxWidth: 150 }}>
      <div style={{ fontSize: 9, color: "var(--muted)", fontFamily: fonts.mono, textTransform: "uppercase", letterSpacing: ".06em" }}>{label}</div>
      <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{m ? mName(m) : "—"}</div>
    </div>
  );
  return (
    <div style={{ marginTop: 9, padding: 11, background: "var(--surface)", border: "1px dashed var(--line)", borderRadius: 9 }}>
      {(father || mother) && (
        <>
          <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
            {father && box("Padre", father)}
            {mother && box("Madre", mother)}
          </div>
          <div style={{ width: 1, height: 12, borderLeft: "1px solid var(--line)", margin: "0 auto" }} />
        </>
      )}
      <div style={{ display: "flex", gap: 8, justifyContent: "center", alignItems: "center" }}>
        {box(roleLabel(principal?.role ?? "principal", recordType), principal, true)}
        {spouse && <span style={{ color: "var(--muted)" }}>⚭</span>}
        {spouse && box("Cónyuge", spouse)}
      </div>
      {god.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 9, textAlign: "center" }}>
          <b>Padrinos:</b> {god.map(mName).join(", ")}
        </div>
      )}
      {others.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 5, textAlign: "center" }}>
          {others.map((m) => `${roleLabel(m.role, recordType)}: ${mName(m)}`).join(" · ")}
        </div>
      )}
    </div>
  );
}

// Pan/zoom the manuscript image with d3-zoom (rueda, arrastre, doble-clic; controles +/−/reset).
function ZoomableImage({ src, alt }: { src: string; alt: string }) {
  const vpRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<ZoomBehavior<HTMLDivElement, unknown> | null>(null);

  useEffect(() => {
    const vp = vpRef.current, layer = layerRef.current;
    if (!vp || !layer) return;
    const zb = d3zoom<HTMLDivElement, unknown>()
      .scaleExtent([1, 6])
      .on("zoom", (ev) => { layer.style.transform = `translate(${ev.transform.x}px,${ev.transform.y}px) scale(${ev.transform.k})`; });
    zoomRef.current = zb;
    select(vp).call(zb);
    return () => { select(vp).on(".zoom", null); };
  }, [src]);

  const zoomBy = (f: number) => { if (vpRef.current && zoomRef.current) select(vpRef.current).call(zoomRef.current.scaleBy, f); };
  const reset = () => { if (vpRef.current && zoomRef.current) select(vpRef.current).call(zoomRef.current.transform, zoomIdentity); };

  return (
    <div style={{ position: "relative" }}>
      <div ref={vpRef} style={{ overflow: "hidden", cursor: "grab", background: "var(--bg)" }}>
        <div ref={layerRef} style={{ transformOrigin: "0 0" }}>
          <img src={src} alt={alt} style={{ width: "100%", display: "block", pointerEvents: "none" }} />
        </div>
      </div>
      <div style={{ position: "absolute", top: 12, right: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        <button onClick={() => zoomBy(1.4)} style={zCtrl} title="Acercar">+</button>
        <button onClick={() => zoomBy(1 / 1.4)} style={zCtrl} title="Alejar">−</button>
        <button onClick={reset} style={zCtrl} title="Restablecer">⤢</button>
      </div>
    </div>
  );
}

const zCtrl: React.CSSProperties = { width: 32, height: 32, borderRadius: 8, background: "var(--surface)", border: "1px solid var(--line)", cursor: "pointer", color: "var(--fg)", fontSize: 16, boxShadow: "var(--shadow)" };

// Lazy-loaded page thumbnail (only fetches when scrolled near, so a 300-page mosaic is cheap).
function Thumb({ docId, pageNo }: { docId: string; pageNo: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    let revoked: string | null = null;
    const io = new IntersectionObserver((es) => {
      if (es[0].isIntersecting) {
        io.disconnect();
        fetchPageObjectUrl(docId, pageNo, true).then((u) => { revoked = u; setUrl(u); }).catch(() => {});
      }
    }, { rootMargin: "300px" });
    io.observe(el);
    return () => { io.disconnect(); if (revoked) URL.revokeObjectURL(revoked); };
  }, [docId, pageNo]);
  return (
    <div ref={ref} style={{ width: "100%", aspectRatio: "3 / 4", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      {url ? <img src={url} alt={`pág ${pageNo}`} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <span style={{ color: "var(--muted)", fontSize: 11 }}>…</span>}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = { record: "acta", index: "índice", cover: "portada", blank: "blanco" };

function PageMosaic({ docId, pages, onOpen, onChanged }: { docId: string; pages: PageOut[]; onOpen: (n: number) => void; onChanged: () => void }) {
  const e = useEstela();
  const [breaks, setBreaks] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const toggleBreak = (n: number) => setBreaks((s) => { const x = new Set(s); x.has(n) ? x.delete(n) : x.add(n); return x; });
  async function changeKind(n: number, kind: string) {
    try { await setPageKind(docId, n, kind); onChanged(); } catch (err) { e.notify((err as Error).message, "var(--danger)"); }
  }
  async function doSplit() {
    if (!breaks.size) return;
    setBusy(true);
    try {
      const docs = await splitDocument(docId, Array.from(breaks).sort((a, b) => a - b));
      e.notify(`Partido en ${docs.length} libros`, "var(--ok)"); setBreaks(new Set()); onChanged();
    } catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    setBusy(false);
  }
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "var(--muted)" }}>{pages.length} páginas · clic en una para abrirla · marca su tipo, o un corte de libro (▎) y pulsa «Partir».</span>
        {breaks.size > 0 && <button onClick={doSplit} disabled={busy} style={{ marginLeft: "auto", background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "8px 14px", fontFamily: "inherit", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>{busy ? "Partiendo…" : `Partir en ${breaks.size + 1} libros`}</button>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(108px,1fr))", gap: 12 }}>
        {pages.map((p) => (
          <div key={p.id} style={{ borderLeft: breaks.has(p.page_no) ? "3px solid var(--accent)" : "3px solid transparent", paddingLeft: 4 }}>
            <div onClick={() => onOpen(p.page_no)} style={{ cursor: "pointer", border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
              <Thumb docId={docId} pageNo={p.page_no} />
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 4, gap: 4 }}>
              <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{p.page_no}{p.folio_label ? `·f${p.folio_label}` : ""}</span>
              <button onClick={() => toggleBreak(p.page_no)} title="Marcar el inicio de un libro nuevo aquí" style={{ background: "transparent", border: "none", cursor: "pointer", color: breaks.has(p.page_no) ? "var(--accent)" : "var(--muted)", fontSize: 14, lineHeight: 1, padding: 0 }}>▎</button>
            </div>
            <select value={p.kind} onChange={(ev) => changeKind(p.page_no, ev.target.value)} title="Tipo de página" style={{ width: "100%", marginTop: 2, background: p.kind !== "record" ? "var(--warn-faint)" : "var(--surface)", border: "1px solid var(--line2)", borderRadius: 5, padding: "2px 4px", color: "var(--fg)", fontFamily: fonts.mono, fontSize: 10 }}>
              {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── empty state (shown when no real document is selected) ──
function SampleVisor() {
  const e = useEstela();
  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1280 }}>
      <div style={{ marginTop: 60, textAlign: "center", padding: "60px 20px" }}>
        <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 30, margin: "0 0 10px", letterSpacing: "-.02em" }}>Ningún documento abierto</h1>
        <p style={{ color: "var(--muted)", fontSize: 15, margin: "0 0 24px" }}>Abre un libro desde la Biblioteca para ver aquí sus páginas, la transcripción y las actas extraídas.</p>
        <button onClick={() => e.go("biblioteca")} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "13px 22px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: "pointer" }}>Ir a la Biblioteca</button>
      </div>
    </section>
  );
}
