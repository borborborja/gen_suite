import { useEffect, useState } from "react";
import { useEstela } from "./store";
import { fonts } from "./theme";
import { ZoomIn } from "./icons";
import { fetchPageObjectUrl } from "../../api/documents";

/** A source image is evidence only when the original page can actually be loaded. */
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
  const hasReal = !!(docId && pageNo && !gone && url);
  return (
    <>
      <div onClick={() => hasReal && setBig(true)} style={{ position: "relative", height: 150, cursor: hasReal ? "zoom-in" : "default", marginBottom: 8, borderRadius: 8, overflow: "hidden", background: "var(--bg)" }}>
        {hasReal
          ? <img src={url} alt="escaneo original de la fuente" style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: "top" }} />
          : <div style={{ height: "100%", padding: 16, display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center", color: "var(--muted)", fontSize: 12, lineHeight: 1.45 }}>
              {!docId || !pageNo ? "No se indicó una página original para esta propuesta."
                : gone ? "No se pudo recuperar la imagen original. Reinténtalo desde el Visor."
                : "Cargando la imagen original…"}
            </div>}
        {hasReal && <span style={{ position: "absolute", top: 8, right: 8, background: "rgba(20,16,10,.72)", color: "#fff", fontFamily: fonts.mono, fontSize: 10, padding: "4px 8px", borderRadius: 5, display: "flex", alignItems: "center", gap: 5 }}>
          <ZoomIn size={11} />ampliar
        </span>}
      </div>
      {quote && <div style={{ fontSize: 11.5, color: "var(--muted)", margin: "-2px 0 8px" }}>Resumen extraído; no sustituye la imagen original.</div>}
      {docId && pageNo && (
        <div onClick={() => e.openDoc(docId, pageNo)} style={{ fontSize: 12, color: "var(--accent)", cursor: "pointer", marginBottom: 12, fontWeight: 600 }}>Ver en el Visor ↗</div>
      )}
      {folio && <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginTop: -8, marginBottom: 10 }}>{folio}</div>}
      {big && url && (
        <div onClick={() => setBig(false)} style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(15,11,6,.85)", display: "flex", alignItems: "center", justifyContent: "center", padding: 40, cursor: "zoom-out" }}>
          <img src={url} alt="" style={{ maxWidth: "92%", maxHeight: "92%", borderRadius: 10, boxShadow: "0 24px 80px rgba(0,0,0,.6)" }} />
        </div>
      )}
    </>
  );
}
