import { useCallback, useMemo, useRef, useState } from "react";
import { useEstela, avatar } from "../store";
import { fonts } from "../theme";
import SourceScan from "../SourceScan";
import {
  searchPersons, getSubtree, displayName, lifespan,
  type SearchHit, type TreeGraph, type TreePerson, type TreeFamily,
} from "../../../api/tree";
import {
  discover, listCandidates, confirmCandidate, rejectCandidate, acceptProposal,
  type CandidateOut,
} from "../../../api/linkage";
import { computeLayout, NODE_W, NODE_H } from "../../tree/layout";

// roles that become tree nodes around the focus (others are shown in the act but not placed)
const REL: Record<string, { rel: "parent" | "spouse" | "child" | "sibling"; sex: string }> = {
  father: { rel: "parent", sex: "M" }, mother: { rel: "parent", sex: "F" },
  spouse: { rel: "spouse", sex: "U" },
  son: { rel: "child", sex: "M" }, daughter: { rel: "child", sex: "F" }, child: { rel: "child", sex: "U" },
  sibling: { rel: "sibling", sex: "U" },
};
const FAMILY_ROLES = new Set(Object.keys(REL));
const ROLE_LABEL: Record<string, string> = {
  principal: "Principal", father: "Padre", mother: "Madre", spouse: "Cónyuge",
  son: "Hijo", daughter: "Hija", child: "Hijo/a", sibling: "Hermano/a",
  godfather: "Padrino", godmother: "Madrina", witness: "Testigo", other: "Otro",
};

function sleep(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

function buildGraph(real: TreeGraph, focusId: string, cand: CandidateOut | null) {
  const persons: TreePerson[] = [...real.persons];
  const families: TreeFamily[] = [...real.families];
  const proposed = new Set<string>();
  if (cand?.record) {
    const rels = cand.record.mentions.filter((m) => m.id !== cand.person_mention_id && FAMILY_ROLES.has(m.role));
    const parents: { id: string; sex: string }[] = [];
    for (const m of rels) {
      const map = REL[m.role];
      const pid = `prop:${cand.id}:${m.id}`;
      persons.push({ id: pid, given: m.given, surname: m.surname, sex: map.sex, birth_year: null, death_year: null, has_documents: false, deduction_count: 0 });
      proposed.add(pid);
      if (map.rel === "parent") parents.push({ id: pid, sex: map.sex });
      else if (map.rel === "spouse") families.push({ id: `f:${pid}`, husband_id: map.sex === "F" ? focusId : pid, wife_id: map.sex === "F" ? pid : focusId, child_ids: [] });
      else if (map.rel === "child") families.push({ id: `f:${pid}`, husband_id: focusId, wife_id: null, child_ids: [pid] });
      else if (map.rel === "sibling") families.push({ id: `f:${pid}`, husband_id: null, wife_id: null, child_ids: [pid, focusId] });
    }
    if (parents.length) {
      families.push({
        id: `f:parents:${cand.id}`,
        husband_id: parents.find((p) => p.sex === "M")?.id ?? parents[0].id,
        wife_id: parents.find((p) => p.sex === "F")?.id ?? (parents[1]?.id ?? null),
        child_ids: [focusId],
      });
    }
  }
  return { graph: { focus: focusId, persons, families } as TreeGraph, proposed };
}

export default function SuperView() {
  const e = useEstela();
  const [seed, setSeed] = useState<SearchHit | null>(null);
  const [graph, setGraph] = useState<TreeGraph | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [cand, setCand] = useState<CandidateOut | null>(null);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");

  const discovered = useRef<Set<string>>(new Set());
  const queue = useRef<CandidateOut[]>([]);   // pending candidates for the current focus
  const stop = useRef(false);

  const reloadGraph = useCallback(async (rootId: string) => {
    const g = await getSubtree(rootId, 4).catch(() => null);
    if (g) setGraph(g);
    return g;
  }, []);

  // Find the next confirmed person we haven't explored yet (frontier = the growing tree).
  function nextFrontier(g: TreeGraph | null): string | null {
    if (!g) return null;
    const p = g.persons.find((x) => !discovered.current.has(x.id));
    return p ? p.id : null;
  }

  const processNext = useCallback(async (g: TreeGraph | null) => {
    let cur = g;
    for (;;) {
      if (stop.current) { setStatus("Detenido."); return; }
      const fid = nextFrontier(cur);
      if (!fid) { setRunning(false); setStatus("Sin más personas por explorar."); setCand(null); return; }
      setFocusId(fid);
      const who = displayName(cur!.persons.find((p) => p.id === fid)!);
      setStatus(`Buscando registros de ${who}…`);
      discovered.current.add(fid);
      try { await discover(fid); } catch { /* enqueue best-effort */ }
      // poll for pending candidates
      let cands: CandidateOut[] = [];
      for (let i = 0; i < 16 && !stop.current; i++) {
        await sleep(1500);
        cands = await listCandidates(fid, "pending").catch(() => []);
        if (cands.length) break;
      }
      if (cands.length) {
        queue.current = cands;
        setCand(cands[0]);
        setStatus(`${cands.length} posible(s) acta(s) para ${who}.`);
        return; // wait for the user to confirm/decline
      }
      setStatus(`Sin registros para ${who}.`);
      // loop continues: `discovered` grew, so nextFrontier(cur) returns the next person
    }
  }, []);

  async function start() {
    if (!seed) return;
    stop.current = false; setRunning(true);
    discovered.current = new Set();
    const g = await reloadGraph(seed.id);
    setFocusId(seed.id);
    await processNext(g);
  }
  function halt() { stop.current = true; setRunning(false); setStatus("Detenido."); }

  async function confirm() {
    if (!cand || !focusId) return;
    setStatus("Confirmando y añadiendo a tu árbol…");
    try {
      await confirmCandidate(cand.id);
      const rels = (cand.record?.mentions ?? []).filter((m) => m.id !== cand.person_mention_id && FAMILY_ROLES.has(m.role));
      for (const m of rels) { try { await acceptProposal(cand.id, m.id); } catch { /* skip */ } }
    } catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    queue.current = [];
    setCand(null);
    const g = await reloadGraph(seed!.id);
    if (running && !stop.current) await processNext(g);
  }

  async function decline() {
    if (!cand) return;
    try { await rejectCandidate(cand.id); } catch { /* */ }
    queue.current.shift();
    if (queue.current.length) { setCand(queue.current[0]); setStatus("Otra posibilidad."); }
    else { setCand(null); setStatus("No quedan más posibilidades para esta persona."); if (running && !stop.current) await processNext(graph); }
  }

  const built = useMemo(() => graph ? buildGraph(graph, focusId ?? graph.focus, cand) : null, [graph, focusId, cand]);
  const layout = useMemo(() => built ? computeLayout(built.graph) : null, [built]);

  return (
    <section style={{ padding: "32px 44px 64px" }}>
      <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Superdescubrimiento</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 20px", maxWidth: 720 }}>
        Elige una persona de tu árbol y Estela irá descubriendo padres, madres, hermanos e hijos en tus libros, generación a generación. Los hallazgos aparecen <span style={{ color: "var(--accent)", fontWeight: 600 }}>en naranja</span>: púlsalos para ver la fuente y confirmarlos.
      </p>

      {!seed ? (
        <SeedPicker onPick={setSeed} />
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
            <div style={{ fontSize: 14 }}>Raíz: <b>{displayName(seed)}</b> {lifespan(seed) && <span style={{ color: "var(--muted)" }}>({lifespan(seed)})</span>}</div>
            {!running ? (
              <button onClick={start} style={primaryBtn}>Empezar</button>
            ) : (
              <button onClick={halt} style={{ ...primaryBtn, background: "transparent", color: "var(--fg)", border: "1px solid var(--line)" }}>Parar</button>
            )}
            <button onClick={() => { setSeed(null); setGraph(null); setCand(null); setRunning(false); stop.current = true; }} style={{ ...primaryBtn, background: "transparent", color: "var(--muted)", border: "1px solid var(--line)" }}>Cambiar raíz</button>
            {status && <span style={{ fontSize: 12.5, color: "var(--muted)", fontFamily: fonts.mono }}>{status}{running && cand === null ? " ⟳" : ""}</span>}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: cand ? "1fr 360px" : "1fr", gap: 20, alignItems: "start" }}>
            {/* canvas */}
            <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16, height: "70vh", overflow: "auto", position: "relative" }}>
              {layout && built ? (
                <div style={{ position: "relative", width: layout.width + 80, height: layout.height + 80, margin: "0 auto", padding: 40 }}>
                  <svg width={layout.width} height={layout.height} style={{ position: "absolute", left: 40, top: 40, overflow: "visible" }}>
                    {layout.coupleLinks.map((l, i) => <line key={`c${i}`} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke="var(--line)" strokeWidth={2} strokeDasharray="4 3" />)}
                    {layout.parentLinks.map((l, i) => <path key={`p${i}`} d={`M${l.x1} ${l.y1} C${l.x1} ${(l.y1 + l.y2) / 2} ${l.x2} ${(l.y1 + l.y2) / 2} ${l.x2} ${l.y2}`} fill="none" stroke="var(--line)" strokeWidth={2} />)}
                  </svg>
                  {layout.nodes.map((n) => {
                    const isProp = built.proposed.has(n.id);
                    const isFocus = n.id === focusId;
                    const av = avatar(isProp ? "inferred" : "confirmed");
                    return (
                      <div key={n.id} onClick={() => isProp && cand && setCand(cand)} title={isProp ? "Propuesto — revisa la fuente a la derecha" : displayName(n)}
                        style={{ position: "absolute", left: n.x + 40, top: n.y + 40, width: NODE_W, height: NODE_H, display: "flex", alignItems: "center", gap: 10, background: isProp ? "var(--accent-faint, rgba(217,83,30,.10))" : "var(--bg)", border: `1.5px ${isProp ? "dashed var(--accent)" : isFocus ? "solid var(--ok)" : "solid var(--line)"}`, borderRadius: 11, padding: "8px 10px", boxSizing: "border-box", boxShadow: "var(--shadow)", cursor: isProp ? "pointer" : "default" }}>
                        <div style={{ width: 34, height: 34, borderRadius: 8, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 14, background: isProp ? "var(--accent)" : av.bg, color: isProp ? "#fff" : av.fg }}>
                          {((n.given?.[0] ?? "") + (n.surname?.[0] ?? "")).toUpperCase() || "··"}
                        </div>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{displayName(n)}</div>
                          <div style={{ fontFamily: fonts.mono, fontSize: 10, color: isProp ? "var(--accent)" : "var(--muted)" }}>{isProp ? "propuesto" : lifespan(n)}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : <div style={{ padding: 40, color: "var(--muted)" }}>Cargando árbol…</div>}
              <div style={{ position: "absolute", bottom: 12, left: 14, display: "flex", gap: 14, fontSize: 11.5, color: "var(--muted)", background: "var(--surface)", padding: "5px 10px", borderRadius: 8, border: "1px solid var(--line)" }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 11, height: 11, borderRadius: 3, border: "1.5px solid var(--ok)" }} />en tu árbol</span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 11, height: 11, borderRadius: 3, border: "1.5px dashed var(--accent)", background: "rgba(217,83,30,.10)" }} />propuesto</span>
              </div>
            </div>

            {/* act panel */}
            {cand && (
              <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16, padding: 18, position: "sticky", top: 12 }}>
                <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".14em", color: "var(--accent)", marginBottom: 8 }}>POSIBLE ACTA · {Math.round(cand.score * 100)}%</div>
                <div style={{ fontFamily: fonts.serif, fontSize: 16, fontWeight: 600, marginBottom: 10 }}>
                  {cand.record?.record_type ? (cand.record.record_type[0].toUpperCase() + cand.record.record_type.slice(1)) : "Acta"}{cand.record?.date_year ? ` · ${cand.record.date_year}` : ""}{cand.record?.parish_raw ? ` · ${cand.record.parish_raw}` : ""}
                </div>
                <SourceScan docId={cand.record?.document_id ?? undefined} pageNo={cand.record?.page_no ?? undefined} quote={cand.record?.summary ?? ""} folio={cand.record?.page_no ? `pág. ${cand.record.page_no}` : undefined} />
                {cand.record?.summary && <div style={{ fontFamily: fonts.serif, fontStyle: "italic", fontSize: 13.5, lineHeight: 1.5, background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 8, padding: "10px 12px", marginBottom: 10 }}>“{cand.record.summary}”</div>}
                <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 12, fontSize: 12.5 }}>
                  {(cand.record?.mentions ?? []).map((m) => (
                    <div key={m.id} style={{ display: "flex", gap: 9 }}>
                      <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--muted)", textTransform: "uppercase", width: 70, flex: "none", paddingTop: 1 }}>{ROLE_LABEL[m.role] ?? m.role}</span>
                      <span style={{ fontWeight: 500 }}>{m.name_raw || [m.given, m.surname].filter(Boolean).join(" ") || "—"}</span>
                    </div>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={confirm} style={{ ...primaryBtn, flex: 1, background: "var(--ok)" }}>Confirmar</button>
                  <button onClick={decline} style={{ ...primaryBtn, flex: 1, background: "transparent", color: "var(--danger)", border: "1px solid var(--danger)" }}>Declinar</button>
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>Declinar prueba otra acta; si no hay más, esta persona se deja sin ampliar.</div>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

const primaryBtn: React.CSSProperties = { background: "var(--accent)", color: "#fff", border: "none", borderRadius: 9, padding: "10px 18px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" };

function SeedPicker({ onPick }: { onPick: (p: SearchHit) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const t = useRef<ReturnType<typeof setTimeout> | null>(null);
  function onChange(v: string) {
    setQ(v);
    if (t.current) clearTimeout(t.current);
    if (v.trim().length < 2) { setResults([]); return; }
    t.current = setTimeout(() => { searchPersons(v).then(setResults).catch(() => setResults([])); }, 250);
  }
  return (
    <div style={{ maxWidth: 460 }}>
      <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8 }}>¿Desde quién empezamos?</div>
      <input autoFocus value={q} onChange={(e) => onChange(e.target.value)} placeholder="Busca una persona de tu árbol…" style={{ width: "100%", boxSizing: "border-box", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "12px 14px", color: "var(--fg)", fontFamily: "inherit", fontSize: 15 }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
        {results.map((p) => (
          <div key={p.id} onClick={() => onPick(p)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "11px 14px", cursor: "pointer" }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>{displayName(p)}</span>
            <span style={{ fontFamily: fonts.mono, fontSize: 11.5, color: "var(--muted)" }}>{lifespan(p)}</span>
          </div>
        ))}
        {q.trim().length >= 2 && results.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13 }}>Sin resultados.</div>}
      </div>
    </div>
  );
}
