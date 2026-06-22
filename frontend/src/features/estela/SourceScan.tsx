import { useEffect, useState } from "react";
import { useEstela } from "./store";
import { fonts } from "./theme";
import { ZoomIn } from "./icons";
import { ManuscriptPlate } from "./Chrome";
import { fetchPageObjectUrl } from "../../api/documents";

/** The REAL scanned page of a source act (falls back to the stylised plate when there's no scan). */
export default function SourceScan({ docId, pageNo, quote, folio }: {
  docId?: string; pageNo?: number; quote: string; folio?: string;
}) {
  const e = useEstela();
  const [url, setUrl] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const [big, setBig] = useState(false);
  useEffect(() => {
    setUrl(null); setGone(false);
    if (!docId || !pageNo) return;
    let u: string | null = null;
    fetchPageObjectUrl(docId, pageNo).then((x) => { u = x; setUrl(x); }).catch(() => setGone(true));
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [docId, pageNo]);
  const hasReal = !!(docId && pageNo && !gone);
  return (
    <>
      <div onClick={() => (hasReal ? url && setBig(true) : e.setZoom(true))} style={{ position: "relative", height: 150, cursor: "zoom-in", marginBottom: 8, borderRadius: 8, overflow: "hidden", background: "var(--bg)" }}>
        {hasReal
          ? (url ? <img src={url} alt="escaneo de la fuente" style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: "top" }} />
                 : <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: 12 }}>Cargando escaneo…</div>)
          : <ManuscriptPlate quote={quote} label={folio} />}
        <span style={{ position: "absolute", top: 8, right: 8, background: "rgba(20,16,10,.72)", color: "#fff", fontFamily: fonts.mono, fontSize: 10, padding: "4px 8px", borderRadius: 5, display: "flex", alignItems: "center", gap: 5 }}>
          <ZoomIn size={11} />ampliar
        </span>
      </div>
      {docId && pageNo && (
        <div onClick={() => e.openDoc(docId, pageNo)} style={{ fontSize: 12, color: "var(--accent)", cursor: "pointer", marginBottom: 12, fontWeight: 600 }}>Ver en el Visor ↗</div>
      )}
      {big && url && (
        <div onClick={() => setBig(false)} style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(15,11,6,.85)", display: "flex", alignItems: "center", justifyContent: "center", padding: 40, cursor: "zoom-out" }}>
          <img src={url} alt="" style={{ maxWidth: "92%", maxHeight: "92%", borderRadius: 10, boxShadow: "0 24px 80px rgba(0,0,0,.6)" }} />
        </div>
      )}
    </>
  );
}
