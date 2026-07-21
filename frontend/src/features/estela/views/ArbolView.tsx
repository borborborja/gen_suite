import { useCallback, useEffect, useRef, useState } from "react";
import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity, type ZoomBehavior } from "d3-zoom";
import { useEstela, avatar, type TreeView } from "../store";
import { fonts } from "../theme";
import {
  getStats, getRoots, getSubtree, searchPersons, importGedcom, downloadGedcom, getPerson,
  listDuplicates, mergePersons, getHome, setHome, createPerson, listPersons,
  downloadPersonsCsv, listPlaces,
  displayName, lifespan, type TreeStats, type SearchHit, type TreeGraph, type DuplicatePair,
  type PersonDetail, type PersonPage, type PersonFilters, type PlaceRow,
} from "../../../api/tree";
import { AddRelativeDialog, RelationshipDialog } from "../TreeDialogs";
import { computeLayout, NODE_W, NODE_H, type PositionedPerson } from "../../tree/layout";
import { computePedigree } from "../../tree/pedigree";
import { computeFan, arcPath } from "../../tree/fan";
import { useConfirm, useDebouncedSearch } from "../ui";

const tabs: { key: TreeView; label: string }[] = [
  { key: "genograma", label: "Genograma" },
  { key: "pedigree", label: "Pedigrí" },
  { key: "abanico", label: "Abanico" },
  { key: "lista", label: "Lista" },
];

function initials(given: string | null, surname: string | null): string {
  return ((given?.[0] ?? "") + (surname?.[0] ?? "")).toUpperCase() || "··";
}

export default function ArbolView() {
  const e = useEstela();
  const [stats, setStats] = useState<TreeStats | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [home, setHomeId] = useState<string | null>(null);
  const [graph, setGraph] = useState<TreeGraph | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showDupes, setShowDupes] = useState(false);
  const [showKinship, setShowKinship] = useState(false);
  const [addRel, setAddRel] = useState<{ id: string; name: string } | null>(null);
  const [refresh, setRefresh] = useState(0);

  const load = useCallback(async () => {
    try {
      const [s, r, h] = await Promise.all([getStats(), getRoots(), getHome().catch(() => ({ person_id: null }))]);
      setStats(s); setHomeId(h.person_id);
      const def = h.person_id || (r.length > 0 ? r[0].id : null);
      if (def) setFocus((f) => f ?? def);
    } catch { /* no backend */ }
    setLoaded(true);
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function makeHome(id: string) {
    try { await setHome(id); setHomeId(id); e.notify("Persona principal actualizada", "var(--ok)"); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
  }

  const [depth, setDepth] = useState(4);
  useEffect(() => {
    if (!focus) return;
    void getSubtree(focus, depth).then(setGraph).catch(() => setGraph(null));
  }, [focus, depth, refresh]);

  const focusHit: SearchHit | null = (() => {
    const p = graph?.persons.find((x) => x.id === graph.focus);
    return p ? { id: p.id, given: p.given, surname: p.surname, birth_year: p.birth_year, death_year: p.death_year } : null;
  })();
  const openAddRelative = (p: { id: string; given: string | null; surname: string | null }) =>
    setAddRel({ id: p.id, name: displayName(p) });

  const empty = loaded && (stats?.persons ?? 0) === 0;

  return (
    <section style={{ padding: "32px 44px 64px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 22 }}>
        <div>
          <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Mi árbol</h1>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0" }}>
            {stats ? `${stats.persons.toLocaleString()} personas · ${stats.families.toLocaleString()} familias · ${stats.events.toLocaleString()} eventos` : "Cargando…"}
          </p>
        </div>
        {!empty && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <TreeSearch onPick={(id) => { setFocus(id); if (e.treeView === "lista") e.setTree("genograma"); }} />
            <div style={{ display: "inline-flex", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 9, padding: 3, gap: 2 }}>
              {tabs.map((t) => (
                <span key={t.key} onClick={() => e.setTree(t.key)} style={{ padding: "8px 14px", borderRadius: 7, cursor: "pointer", fontSize: 13, fontWeight: e.treeView === t.key ? 600 : 500, color: e.treeView === t.key ? "#fff" : "var(--muted)", background: e.treeView === t.key ? "var(--accent)" : "transparent" }}>{t.label}</span>
              ))}
            </div>
            {home && home !== focus && <button onClick={() => setFocus(home)} title="Volver a la persona principal" style={ghostBtn}>⌂ Inicio</button>}
            <button onClick={() => setShowKinship(true)} title="¿Qué parentesco tienen dos personas?" style={ghostBtn}>Parentesco</button>
            <button onClick={() => setShowDupes(true)} style={ghostBtn}>Fusionar duplicados</button>
            <button
              onClick={() => downloadGedcom().catch((err) => e.notify(`No se pudo exportar: ${(err as Error).message}`, "var(--danger)"))}
              title="Descargar el árbol completo como GEDCOM (con fuentes)"
              style={ghostBtn}
            >⬇ Exportar GEDCOM</button>
          </div>
        )}
      </div>

      {showDupes && <DuplicatesPanel onClose={() => setShowDupes(false)} onMerged={load} />}
      {showKinship && <RelationshipDialog initialA={focusHit} onClose={() => setShowKinship(false)} />}
      {addRel && (
        <AddRelativeDialog
          personId={addRel.id} personName={addRel.name}
          onClose={() => setAddRel(null)} onDone={() => setRefresh((n) => n + 1)}
        />
      )}

      {empty ? <EmptyTree onImported={load} onCreated={(id) => { setFocus(id); void load(); }} onSuper={() => e.go("super")} /> : (
        <>
          <div style={{ display: "flex", gap: 18, alignItems: "center", marginBottom: 14, fontSize: 12.5, color: "var(--muted)" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><span style={{ width: 12, height: 12, borderRadius: 3, background: "var(--ok)" }} />Confirmado</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><span style={{ width: 12, height: 12, borderRadius: 3, border: "1.5px dashed var(--warn)", background: "var(--warn-faint)" }} />Con datos inferidos</span>
          </div>

          {e.treeView === "lista" ? <PeopleList /> : (
            <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 18, alignItems: "start" }}>
              {focus && <FocusCard personId={focus} isHome={home === focus} onHome={() => makeHome(focus)} onAddRelative={(name) => setAddRel({ id: focus, name })} />}
              {graph ? (
                e.treeView === "pedigree" ? <PedigreeCanvas graph={graph} depth={depth} setDepth={setDepth} onRecenter={setFocus} />
                : e.treeView === "abanico" ? <FanCanvas graph={graph} depth={depth} setDepth={setDepth} onRecenter={setFocus} />
                : <GraphCanvas graph={graph} depth={depth} setDepth={setDepth} onRecenter={setFocus} onAddRelative={openAddRelative} />
              ) : <div style={{ color: "var(--muted)", padding: 40 }}>Cargando árbol…</div>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function DuplicatesPanel({ onClose, onMerged }: { onClose: () => void; onMerged: () => void }) {
  const e = useEstela();
  const [pairs, setPairs] = useState<DuplicatePair[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { confirmDialog, ask } = useConfirm();

  const reload = useCallback(() => {
    setPairs(null);
    listDuplicates().then(setPairs).catch(() => setPairs([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function merge(keep: SearchHit, dup: SearchHit) {
    const ok = await ask({
      title: `¿Fusionar «${displayName(dup)}» dentro de «${displayName(keep)}»?`,
      body: "Los nombres, hechos, parentescos y fuentes del duplicado pasan a la persona conservada. No se puede deshacer.",
      danger: true, confirmLabel: "Fusionar",
    });
    if (!ok) return;
    setBusy(keep.id + dup.id);
    try { await mergePersons(keep.id, dup.id); onMerged(); reload(); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    setBusy(null);
  }

  const label = (p: SearchHit) => `${displayName(p)}${lifespan(p) ? ` (${lifespan(p)})` : ""}`;

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 70, background: "rgba(15,11,6,.55)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "60px 20px", overflowY: "auto" }}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ width: "100%", maxWidth: 720, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16, padding: 26, boxShadow: "0 24px 80px rgba(0,0,0,.4)" }}>
        {confirmDialog}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, margin: 0 }}>Posibles duplicados</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 18px" }}>Personas que parecen la misma (nombre fonético + año compatible). Conserva la izquierda; la derecha se fusiona dentro.</p>
        {pairs === null && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Buscando duplicados…</div>}
        {pairs && pairs.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>No se han encontrado duplicados evidentes.</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {pairs?.map((pr) => (
            <div key={pr.a.id + pr.b.id} style={{ background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 11, padding: "13px 15px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontSize: 13.5, fontWeight: 600 }}>{label(pr.a)}</span>
                <span style={{ color: "var(--muted)" }}>↔</span>
                <span style={{ fontSize: 13.5, fontWeight: 600 }}>{label(pr.b)}</span>
                <span style={{ marginLeft: "auto", fontFamily: fonts.mono, fontSize: 10.5, color: "var(--accent)" }}>{Math.round(pr.score * 100)}%</span>
              </div>
              <div style={{ fontSize: 11.5, color: "var(--muted)", margin: "6px 0 10px" }}>{pr.reason}</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button disabled={!!busy} onClick={() => merge(pr.a, pr.b)} style={dupBtn}>Conservar «{displayName(pr.a)}»</button>
                <button disabled={!!busy} onClick={() => merge(pr.b, pr.a)} style={dupBtn}>Conservar «{displayName(pr.b)}»</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const dupBtn: React.CSSProperties = { background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "8px 13px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" };

function GraphCanvas({ graph, depth, setDepth, onRecenter, onAddRelative }: { graph: TreeGraph; depth: number; setDepth: (d: number) => void; onRecenter: (id: string) => void; onAddRelative: (p: PositionedPerson) => void }) {
  const e = useEstela();
  const layout = computeLayout(graph);
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<ZoomBehavior<HTMLDivElement, unknown> | null>(null);

  // set up d3 pan/zoom on the viewport, applying the transform to the content layer
  useEffect(() => {
    const vp = viewportRef.current, content = contentRef.current;
    if (!vp || !content) return;
    const zb = d3zoom<HTMLDivElement, unknown>()
      .scaleExtent([0.1, 2.5])
      .on("zoom", (ev) => { content.style.transform = `translate(${ev.transform.x}px,${ev.transform.y}px) scale(${ev.transform.k})`; });
    zoomRef.current = zb;
    select(vp).call(zb).on("dblclick.zoom", null);
    return () => { select(vp).on(".zoom", null); };
  }, []);

  const fit = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || !zoomRef.current) return;
    const w = vp.clientWidth, h = vp.clientHeight;
    const k = Math.min(w / (layout.width + 80), h / (layout.height + 80), 1.2);
    const tx = (w - layout.width * k) / 2, ty = 30;
    select(vp).call(zoomRef.current.transform, zoomIdentity.translate(tx, ty).scale(k));
  }, [layout.width, layout.height]);

  // auto-fit when the graph changes
  useEffect(() => { const t = setTimeout(fit, 50); return () => clearTimeout(t); }, [fit, graph.focus]);

  const zoomBy = (factor: number) => { if (viewportRef.current && zoomRef.current) select(viewportRef.current).call(zoomRef.current.scaleBy, factor); };

  return (
    <div style={{ position: "relative", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16, overflow: "hidden", height: "72vh" }}>
      <div ref={viewportRef} style={{ position: "absolute", inset: 0, cursor: "grab", overflow: "hidden" }}>
        <div ref={contentRef} style={{ position: "absolute", top: 0, left: 0, transformOrigin: "0 0", width: layout.width, height: layout.height }}>
          <svg width={layout.width} height={layout.height} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
            {layout.coupleLinks.map((l, i) => <line key={`c${i}`} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke="var(--line)" strokeWidth={2} strokeDasharray="4 3" />)}
            {layout.parentLinks.map((l, i) => <path key={`p${i}`} d={`M${l.x1} ${l.y1} C${l.x1} ${(l.y1 + l.y2) / 2} ${l.x2} ${(l.y1 + l.y2) / 2} ${l.x2} ${l.y2}`} fill="none" stroke="var(--line)" strokeWidth={2} />)}
          </svg>
          {layout.nodes.map((n: PositionedPerson) => {
            const inferred = (n.deduction_count ?? 0) > 0;
            const av = avatar(inferred ? "inferred" : "confirmed");
            const sel = n.id === graph.focus;
            return (
              <div key={n.id} onClick={() => (sel ? e.openPerson(n.id) : onRecenter(n.id))} title={sel ? "Abrir ficha" : "Centrar aquí · clic de nuevo abre la ficha"} style={{ position: "absolute", left: n.x, top: n.y, width: NODE_W, height: NODE_H, display: "flex", alignItems: "center", gap: 10, background: "var(--bg)", border: `1.5px ${inferred ? "dashed" : "solid"} ${sel ? "var(--accent)" : inferred ? "var(--warn)" : "var(--line)"}`, borderRadius: 11, padding: "8px 10px", cursor: "pointer", boxShadow: "var(--shadow)", boxSizing: "border-box" }}>
                <div style={{ width: 34, height: 34, borderRadius: 8, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 14, background: av.bg, color: av.fg }}>{initials(n.given, n.surname)}</div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{displayName(n)}</div>
                  <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{lifespan(n)}</div>
                </div>
                <span onClick={(ev) => { ev.stopPropagation(); onAddRelative(n); }} title="Añadir familiar" style={{ flex: "none", color: "var(--muted)", fontSize: 14, lineHeight: 1, padding: "0 2px" }}>＋</span>
                {sel && <span onClick={(ev) => { ev.stopPropagation(); e.openPerson(n.id); }} title="Abrir ficha" style={{ flex: "none", color: "var(--accent)", fontSize: 13, padding: "0 2px" }}>↗</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* controls */}
      <div style={{ position: "absolute", top: 14, right: 14, display: "flex", flexDirection: "column", gap: 6 }}>
        <Ctrl onClick={() => zoomBy(1.3)}>+</Ctrl>
        <Ctrl onClick={() => zoomBy(1 / 1.3)}>−</Ctrl>
        <Ctrl onClick={fit} title="Ajustar">⤢</Ctrl>
      </div>
      <div style={{ position: "absolute", bottom: 14, left: 14, display: "flex", alignItems: "center", gap: 8, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 9, padding: "6px 10px" }}>
        <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>generaciones</span>
        <button onClick={() => setDepth(Math.max(2, depth - 1))} style={miniBtn}>−</button>
        <span style={{ fontFamily: fonts.mono, fontSize: 12, minWidth: 14, textAlign: "center" }}>{depth}</span>
        <button onClick={() => setDepth(Math.min(6, depth + 1))} style={miniBtn}>+</button>
      </div>
      <div style={{ position: "absolute", bottom: 14, right: 14, fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", pointerEvents: "none" }}>arrastra para mover · rueda para zoom</div>
    </div>
  );
}

const miniBtn: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 5, width: 22, height: 22, cursor: "pointer", color: "var(--fg)", fontSize: 13 };

function Ctrl({ children, onClick, title }: { children: React.ReactNode; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title} style={{ width: 34, height: 34, borderRadius: 8, background: "var(--surface)", border: "1px solid var(--line)", cursor: "pointer", color: "var(--fg)", fontSize: 17, boxShadow: "var(--shadow)" }}>{children}</button>
  );
}

const ghostBtn: React.CSSProperties = { background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 9, padding: "9px 14px", fontFamily: "inherit", fontSize: 13, fontWeight: 600, cursor: "pointer" };
const canvasBox: React.CSSProperties = { position: "relative", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16, overflow: "hidden", height: "72vh" };

// ── header person search (typeahead → recenter) ──
function TreeSearch({ onPick }: { onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const res = useDebouncedSearch(q, (v) => searchPersons(v));
  return (
    <div style={{ position: "relative" }}>
      <input value={q} onChange={(ev) => { setQ(ev.target.value); setOpen(true); }} onBlur={() => setTimeout(() => setOpen(false), 150)} placeholder="Ir a una persona…" style={{ width: 200, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 9, padding: "9px 12px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13 }} />
      {open && res.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, width: 280, zIndex: 40, background: "var(--elevated)", border: "1px solid var(--line)", borderRadius: 9, marginTop: 4, boxShadow: "var(--shadow)", maxHeight: 280, overflowY: "auto" }}>
          {res.map((p) => (
            <div key={p.id} onMouseDown={() => { onPick(p.id); setQ(""); setOpen(false); }} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "9px 12px", cursor: "pointer", borderBottom: "1px solid var(--line2)" }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{displayName(p)}</span>
              <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>{lifespan(p)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── focus person card (left panel) ──
function FocusCard({ personId, isHome, onHome, onAddRelative }: { personId: string; isHome: boolean; onHome: () => void; onAddRelative: (name: string) => void }) {
  const e = useEstela();
  const [p, setP] = useState<PersonDetail | null>(null);
  useEffect(() => { setP(null); getPerson(personId).then(setP).catch(() => setP(null)); }, [personId]);
  const primary = p?.names.find((n) => n.is_primary) ?? p?.names[0];
  const name = displayName({ given: primary?.given ?? null, surname: primary?.surname ?? null });
  const birth = p?.events.find((ev) => ev.type === "birth");
  const death = p?.events.find((ev) => ev.type === "death");
  const place = p?.events.find((ev) => ev.place)?.place;
  const av = avatar((p?.names.some((n) => n.is_inferred) || p?.events.some((ev) => ev.is_inferred)) ? "inferred" : "confirmed");
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 18, position: "sticky", top: 12 }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, textAlign: "center" }}>
        <div style={{ width: 64, height: 64, borderRadius: 16, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 26, background: av.bg, color: av.fg }}>{initials(primary?.given ?? null, primary?.surname ?? null)}</div>
        <div style={{ fontFamily: fonts.serif, fontSize: 18, fontWeight: 600, lineHeight: 1.15 }}>{name}</div>
        <div style={{ fontSize: 12.5, color: "var(--muted)" }}>{lifespan({ birth_year: birth?.date_year ?? null, death_year: death?.date_year ?? null }) || "—"}{place ? ` · ${place}` : ""}</div>
        {isHome && <span style={{ fontFamily: fonts.mono, fontSize: 10, background: "var(--accent)", color: "#fff", borderRadius: 5, padding: "2px 7px" }}>PERSONA PRINCIPAL</span>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 16 }}>
        <button onClick={() => e.openPerson(personId)} style={{ ...ghostBtn, width: "100%", textAlign: "center", background: "var(--accent)", color: "#fff", border: "none" }}>Abrir ficha</button>
        <button onClick={() => onAddRelative(name)} style={{ ...ghostBtn, width: "100%" }}>＋ Añadir familiar</button>
        {!isHome && <button onClick={onHome} style={{ ...ghostBtn, width: "100%" }}>★ Fijar como principal</button>}
        <button onClick={() => { import("../../../api/linkage").then(({ discover }) => discover(personId).catch(() => undefined)); e.notify("Buscando registros…"); e.go("descubrimientos"); }} style={{ ...ghostBtn, width: "100%" }}>Buscar registros</button>
        <button onClick={() => { import("../../../api/linkage").then(({ discoverFamily }) => discoverFamily(personId).catch(() => undefined)); e.notify("Buscando hermanos y padres…"); e.go("descubrimientos"); }} style={{ ...ghostBtn, width: "100%" }} title="Busca otras partidas con los mismos padres → hermanos y confirma a los padres">Descubrir familia (hermanos)</button>
      </div>
    </div>
  );
}

// Shared pan/zoom viewport with fit + zoom + generation controls (mirrors GraphCanvas's scaffold).
function PanZoom({ width, height, depth, setDepth, children }: { width: number; height: number; depth: number; setDepth: (d: number) => void; children: React.ReactNode }) {
  const vpRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<ZoomBehavior<HTMLDivElement, unknown> | null>(null);
  useEffect(() => {
    const vp = vpRef.current, layer = layerRef.current;
    if (!vp || !layer) return;
    const zb = d3zoom<HTMLDivElement, unknown>().scaleExtent([0.1, 2.5])
      .on("zoom", (ev) => { layer.style.transform = `translate(${ev.transform.x}px,${ev.transform.y}px) scale(${ev.transform.k})`; });
    zoomRef.current = zb;
    select(vp).call(zb).on("dblclick.zoom", null);
    return () => { select(vp).on(".zoom", null); };
  }, []);
  const fit = useCallback(() => {
    const vp = vpRef.current;
    if (!vp || !zoomRef.current) return;
    const k = Math.min(vp.clientWidth / (width + 80), vp.clientHeight / (height + 80), 1.2);
    select(vp).call(zoomRef.current.transform, zoomIdentity.translate((vp.clientWidth - width * k) / 2, 30).scale(k));
  }, [width, height]);
  useEffect(() => { const t = setTimeout(fit, 50); return () => clearTimeout(t); }, [fit]);
  const zoomBy = (f: number) => { if (vpRef.current && zoomRef.current) select(vpRef.current).call(zoomRef.current.scaleBy, f); };
  return (
    <div style={canvasBox}>
      <div ref={vpRef} style={{ position: "absolute", inset: 0, cursor: "grab", overflow: "hidden" }}>
        <div ref={layerRef} style={{ position: "absolute", top: 0, left: 0, transformOrigin: "0 0", width, height }}>{children}</div>
      </div>
      <div style={{ position: "absolute", top: 14, right: 14, display: "flex", flexDirection: "column", gap: 6 }}>
        <Ctrl onClick={() => zoomBy(1.3)}>+</Ctrl><Ctrl onClick={() => zoomBy(1 / 1.3)}>−</Ctrl><Ctrl onClick={fit} title="Ajustar">⤢</Ctrl>
      </div>
      <div style={{ position: "absolute", bottom: 14, left: 14, display: "flex", alignItems: "center", gap: 8, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 9, padding: "6px 10px" }}>
        <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>generaciones</span>
        <button onClick={() => setDepth(Math.max(2, depth - 1))} style={miniBtn}>−</button>
        <span style={{ fontFamily: fonts.mono, fontSize: 12, minWidth: 14, textAlign: "center" }}>{depth}</span>
        <button onClick={() => setDepth(Math.min(6, depth + 1))} style={miniBtn}>+</button>
      </div>
    </div>
  );
}

function PedigreeCanvas({ graph, depth, setDepth, onRecenter }: { graph: TreeGraph; depth: number; setDepth: (d: number) => void; onRecenter: (id: string) => void }) {
  const ped = computePedigree(graph, graph.focus, depth);
  return (
    <PanZoom width={ped.width} height={ped.height} depth={depth} setDepth={setDepth}>
      <svg width={ped.width} height={ped.height} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
        {ped.links.map((l, i) => <path key={i} d={`M${l.x1} ${l.y1} C${(l.x1 + l.x2) / 2} ${l.y1} ${(l.x1 + l.x2) / 2} ${l.y2} ${l.x2} ${l.y2}`} fill="none" stroke="var(--line)" strokeWidth={2} />)}
      </svg>
      {ped.nodes.map((n) => {
        const inferred = (n.deduction_count ?? 0) > 0;
        const av = avatar(inferred ? "inferred" : "confirmed");
        const sel = n.id === graph.focus;
        return (
          <div key={n.id} onClick={() => onRecenter(n.id)} style={{ position: "absolute", left: n.x, top: n.y, width: NODE_W, height: NODE_H, display: "flex", alignItems: "center", gap: 10, background: "var(--bg)", border: `1.5px ${inferred ? "dashed var(--warn)" : sel ? "solid var(--accent)" : "solid var(--line)"}`, borderRadius: 11, padding: "8px 10px", boxSizing: "border-box", boxShadow: "var(--shadow)", cursor: "pointer" }}>
            <div style={{ width: 34, height: 34, borderRadius: 8, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 14, background: av.bg, color: av.fg }}>{initials(n.given, n.surname)}</div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{displayName(n)}</div>
              <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{lifespan(n)}</div>
            </div>
          </div>
        );
      })}
    </PanZoom>
  );
}

function FanCanvas({ graph, depth, setDepth, onRecenter }: { graph: TreeGraph; depth: number; setDepth: (d: number) => void; onRecenter: (id: string) => void }) {
  const fan = computeFan(graph, graph.focus, depth);
  const size = fan.R * 2;
  const fill = (sex: string, gen: number) => gen === 0 ? "var(--accent)" : sex === "F" ? "var(--warn-faint, #f3e6d6)" : sex === "M" ? "var(--ok-faint, #e7efe6)" : "var(--bg)";
  return (
    <PanZoom width={size} height={size} depth={depth} setDepth={setDepth}>
      <svg width={size} height={size} style={{ position: "absolute", inset: 0 }}>
        {fan.segs.map((s) => {
          const d = arcPath(fan.center, fan.center, s.r0, s.r1, s.a0, s.a1);
          const mid = (s.a0 + s.a1) / 2, rr = (s.r0 + s.r1) / 2;
          const tx = fan.center + rr * Math.cos(mid), ty = fan.center + rr * Math.sin(mid);
          const deg = (mid * 180) / Math.PI;
          const rot = deg > 90 && deg < 270 ? deg + 180 : deg;
          return (
            <g key={s.id} onClick={() => onRecenter(s.id)} style={{ cursor: "pointer" }}>
              <path d={d} fill={fill(s.sex, s.gen)} stroke="var(--surface)" strokeWidth={2} />
              {s.gen <= 3 && (
                <text x={tx} y={ty} fontSize={s.gen === 0 ? 12 : 10.5} fontFamily={fonts.sans} fontWeight={600} fill={s.gen === 0 ? "#fff" : "var(--fg)"} textAnchor="middle" dominantBaseline="middle" transform={s.gen === 0 ? undefined : `rotate(${rot} ${tx} ${ty})`} style={{ pointerEvents: "none" }}>
                  {s.gen === 0 ? displayName(s) : initials(s.given, s.surname)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </PanZoom>
  );
}

const PAGE_SIZE = 50;
type SortKey = "name" | "birth" | "death";

function PeopleList() {
  const e = useEstela();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("name");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PersonPage | null>(null);
  const [showFilters, setShowFilters] = useState(false);
  const [sex, setSex] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [place, setPlace] = useState<PlaceRow | null>(null);
  const [missing, setMissing] = useState<string[]>([]);

  const filters: PersonFilters = {
    q: q.trim() || undefined,
    sex: sex || undefined,
    year_from: yearFrom ? Number(yearFrom) : undefined,
    year_to: yearTo ? Number(yearTo) : undefined,
    place_id: place?.id,
    missing: missing.length ? missing : undefined,
  };
  const filterKey = JSON.stringify(filters);

  useEffect(() => {
    const t = setTimeout(() => {
      listPersons({ ...(JSON.parse(filterKey) as PersonFilters), sort, order, page, page_size: PAGE_SIZE })
        .then(setData).catch(() => setData({ total: 0, items: [] }));
    }, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [filterKey, sort, order, page, q]);

  const toggleMissing = (k: string) => {
    setPage(1);
    setMissing((m) => (m.includes(k) ? m.filter((x) => x !== k) : [...m, k]));
  };
  const activeFilters = (sex ? 1 : 0) + (yearFrom || yearTo ? 1 : 0) + (place ? 1 : 0) + missing.length;

  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const toggleSort = (k: SortKey) => {
    setPage(1);
    if (sort === k) setOrder((o) => (o === "asc" ? "desc" : "asc"));
    else { setSort(k); setOrder("asc"); }
  };
  const arrow = (k: SortKey) => (sort === k ? (order === "asc" ? " ↑" : " ↓") : "");
  const th: React.CSSProperties = { textAlign: "left", padding: "11px 14px", fontSize: 11.5, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".05em", cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" };
  const td: React.CSSProperties = { padding: "11px 14px", fontSize: 13.5, borderTop: "1px solid var(--line2)" };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "11px 16px", flex: "1 1 280px", maxWidth: 420 }}>
          <svg width={17} height={17} viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth={1.8}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input value={q} onChange={(ev) => { setQ(ev.target.value); setPage(1); }} placeholder="Buscar por nombre o apellido…" style={{ flex: 1, background: "transparent", border: "none", outline: "none", color: "var(--fg)", fontFamily: "inherit", fontSize: 14 }} />
        </div>
        <button onClick={() => setShowFilters((v) => !v)} style={{ ...ghostBtn, background: showFilters ? "var(--accent-faint)" : "transparent" }}>
          Filtros{activeFilters > 0 ? ` · ${activeFilters}` : ""}
        </button>
        <button
          onClick={() => downloadPersonsCsv(filters).catch(() => e.notify("No se pudo exportar el CSV", "var(--danger)"))}
          title="Exporta las personas filtradas como CSV"
          style={ghostBtn}
        >⬇ CSV</button>
        {data && <span style={{ fontFamily: fonts.mono, fontSize: 12, color: "var(--muted)" }}>{data.total.toLocaleString()} personas</span>}
      </div>

      {showFilters && (
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 16, marginBottom: 16 }}>
          <label style={filterLbl}>Sexo
            <select value={sex} onChange={(ev) => { setSex(ev.target.value); setPage(1); }} style={filterFld}>
              <option value="">Todos</option><option value="M">Hombres</option><option value="F">Mujeres</option><option value="U">Sin definir</option>
            </select>
          </label>
          <label style={filterLbl}>Nacimiento desde
            <input value={yearFrom} onChange={(ev) => { setYearFrom(ev.target.value.replace(/\D/g, "")); setPage(1); }} placeholder="1800" style={{ ...filterFld, width: 90 }} />
          </label>
          <label style={filterLbl}>hasta
            <input value={yearTo} onChange={(ev) => { setYearTo(ev.target.value.replace(/\D/g, "")); setPage(1); }} placeholder="1900" style={{ ...filterFld, width: 90 }} />
          </label>
          <PlaceFilter value={place} onPick={(p) => { setPlace(p); setPage(1); }} />
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {[["birth", "Sin nacimiento"], ["parents", "Sin padres"], ["sources", "Sin fuentes"]].map(([k, l]) => (
              <span key={k} onClick={() => toggleMissing(k)} style={{ padding: "7px 12px", borderRadius: 999, cursor: "pointer", fontSize: 12.5, fontWeight: 600, border: `1px solid ${missing.includes(k) ? "var(--accent)" : "var(--line)"}`, color: missing.includes(k) ? "#fff" : "var(--muted)", background: missing.includes(k) ? "var(--accent)" : "transparent" }}>{l}</span>
            ))}
          </div>
          {activeFilters > 0 && (
            <button onClick={() => { setSex(""); setYearFrom(""); setYearTo(""); setPlace(null); setMissing([]); setPage(1); }} style={{ ...miniBtn, alignSelf: "center" }}>Limpiar</button>
          )}
        </div>
      )}

      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 13, overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th} onClick={() => toggleSort("name")}>Nombre{arrow("name")}</th>
                <th style={th} onClick={() => toggleSort("birth")}>Nacimiento{arrow("birth")}</th>
                <th style={th} onClick={() => toggleSort("death")}>Defunción{arrow("death")}</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((p) => {
                const av = avatar("confirmed");
                return (
                  <tr key={p.id} onClick={() => e.openPerson(p.id)} style={{ cursor: "pointer" }}>
                    <td style={td}>
                      <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                        <div style={{ width: 32, height: 32, borderRadius: 8, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 13, background: av.bg, color: av.fg }}>{initials(p.given, p.surname)}</div>
                        <span style={{ fontWeight: 600 }}>{displayName(p)}</span>
                        {p.sex !== "U" && <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{p.sex === "M" ? "♂" : "♀"}</span>}
                      </div>
                    </td>
                    <td style={{ ...td, fontFamily: fonts.mono, fontSize: 12.5, color: "var(--muted)" }}>{p.birth_year ?? "—"}</td>
                    <td style={{ ...td, fontFamily: fonts.mono, fontSize: 12.5, color: "var(--muted)" }}>{p.death_year ?? "—"}</td>
                  </tr>
                );
              })}
              {data && data.items.length === 0 && (
                <tr><td style={{ ...td, color: "var(--muted)" }} colSpan={3}>Sin resultados.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {pages > 1 && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10, padding: "10px 14px", borderTop: "1px solid var(--line2)" }}>
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} style={{ ...miniBtn, opacity: page <= 1 ? 0.4 : 1 }}>‹</button>
            <span style={{ fontFamily: fonts.mono, fontSize: 12, color: "var(--muted)" }}>{page} / {pages}</span>
            <button onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page >= pages} style={{ ...miniBtn, opacity: page >= pages ? 0.4 : 1 }}>›</button>
          </div>
        )}
      </div>
    </div>
  );
}

const filterLbl: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 4, fontSize: 11.5, color: "var(--muted)" };
const filterFld: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13 };

function PlaceFilter({ value, onPick }: { value: PlaceRow | null; onPick: (p: PlaceRow | null) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const results = useDebouncedSearch(q, async (v) => (await listPlaces({ q: v, page_size: 12 })).items, { minLength: 1 });
  return (
    <label style={{ ...filterLbl, position: "relative" }}>Lugar
      {value ? (
        <span style={{ ...filterFld, display: "inline-flex", alignItems: "center", gap: 8 }}>
          {value.name}
          <span onClick={() => onPick(null)} style={{ cursor: "pointer", color: "var(--muted)" }}>✕</span>
        </span>
      ) : (
        <>
          <input value={q} onChange={(ev) => { setQ(ev.target.value); setOpen(true); }} onBlur={() => setTimeout(() => setOpen(false), 150)} placeholder="Cualquiera" style={{ ...filterFld, width: 150 }} />
          {open && results.length > 0 && (
            <div style={{ position: "absolute", top: "100%", left: 0, minWidth: 220, zIndex: 40, background: "var(--elevated)", border: "1px solid var(--line)", borderRadius: 9, marginTop: 4, boxShadow: "var(--shadow)", maxHeight: 220, overflowY: "auto" }}>
              {results.map((r) => (
                <div key={r.id} onMouseDown={() => { onPick(r); setQ(""); setOpen(false); }} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "8px 11px", cursor: "pointer", borderBottom: "1px solid var(--line2)" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>{r.name}</span>
                  <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{r.event_count} ev.</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </label>
  );
}

function EmptyTree({ onImported, onCreated, onSuper }: { onImported: () => void; onCreated: (id: string) => void; onSuper?: () => void }) {
  const est = useEstela();
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [given, setGiven] = useState("");
  const [surname, setSurname] = useState("");
  const [sex, setSex] = useState("U");
  const ref = useRef<HTMLInputElement>(null);
  async function onFile(ev: React.ChangeEvent<HTMLInputElement>) {
    const f = ev.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try { await importGedcom(f); onImported(); } catch (e) { est.notify((e as Error).message, "var(--danger)"); }
    setBusy(false);
  }
  async function createFirst() {
    if (!given.trim() && !surname.trim()) return;
    setBusy(true);
    try {
      const { id } = await createPerson({ given: given.trim() || undefined, surname: surname.trim() || undefined, sex });
      await setHome(id).catch(() => undefined);
      est.notify("Primera persona creada — ve añadiendo familiares desde el árbol", "var(--ok)");
      onCreated(id);
    } catch (e) { est.notify((e as Error).message, "var(--danger)"); }
    setBusy(false);
  }
  const fld: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", color: "var(--fg)", fontFamily: "inherit", fontSize: 14, boxSizing: "border-box" };
  return (
    <div style={{ textAlign: "center", padding: "60px 20px", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 16 }}>
      <h2 style={{ fontFamily: fonts.serif, fontSize: 26, fontWeight: 600, margin: "0 0 10px" }}>Tu árbol está vacío</h2>
      <p style={{ color: "var(--muted)", fontSize: 14.5, margin: "0 0 22px" }}>Importa un GEDCOM, o empieza de cero creando a la primera persona (tú, o el antepasado que investigas).</p>
      <input ref={ref} type="file" accept=".ged,.gedcom" onChange={onFile} style={{ display: "none" }} />
      {creating ? (
        <div style={{ display: "inline-flex", flexDirection: "column", gap: 10, width: "100%", maxWidth: 340, textAlign: "left" }}>
          <input style={fld} value={given} onChange={(ev) => setGiven(ev.target.value)} placeholder="Nombre" autoFocus />
          <input style={fld} value={surname} onChange={(ev) => setSurname(ev.target.value)} placeholder="Apellidos" />
          <select style={fld} value={sex} onChange={(ev) => setSex(ev.target.value)}>
            <option value="U">Sexo —</option><option value="M">Hombre</option><option value="F">Mujer</option>
          </select>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={createFirst} disabled={busy || (!given.trim() && !surname.trim())} style={{ flex: 1, background: "var(--accent)", color: "#fff", border: "none", borderRadius: 9, padding: "12px 18px", fontFamily: "inherit", fontSize: 14.5, fontWeight: 600, cursor: "pointer", opacity: busy || (!given.trim() && !surname.trim()) ? 0.55 : 1 }}>
              {busy ? "Creando…" : "Crear y empezar el árbol"}
            </button>
            <button onClick={() => setCreating(false)} style={{ background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 9, padding: "12px 16px", fontFamily: "inherit", fontSize: 14, cursor: "pointer" }}>Cancelar</button>
          </div>
        </div>
      ) : (
        <div style={{ display: "inline-flex", gap: 10, flexWrap: "wrap", justifyContent: "center" }}>
          <button onClick={() => setCreating(true)} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 9, padding: "13px 22px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: "pointer" }}>
            Crear la primera persona
          </button>
          <button onClick={() => ref.current?.click()} disabled={busy} style={{ background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 9, padding: "13px 22px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 }}>
            {busy ? "Importando…" : "Importar árbol (GEDCOM)"}
          </button>
          {onSuper && (
            <button onClick={onSuper} style={{ background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 9, padding: "13px 22px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: "pointer" }}>
              Reconstruir desde mis libros
            </button>
          )}
        </div>
      )}
    </div>
  );
}
