import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior } from "d3-zoom";
import {
  type PersonDetail,
  type SearchHit,
  type TreeGraph,
  type TreeStats,
  displayName,
  downloadGedcom,
  getPerson,
  getRoots,
  getStats,
  getSubtree,
  importGedcom,
  lifespan,
  searchPersons,
} from "../../api/tree";
import { NODE_H, NODE_W, computeLayout } from "./layout";

const SEX_COLOR: Record<string, string> = { M: "#38bdf8", F: "#f472b6", U: "#94a3b8" };

export default function TreeView({ onError }: { onError: (e: string) => void }) {
  const [stats, setStats] = useState<TreeStats | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [graph, setGraph] = useState<TreeGraph | null>(null);
  const [detail, setDetail] = useState<PersonDetail | null>(null);
  const [depth, setDepth] = useState(3);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [transform, setTransform] = useState("translate(0,0) scale(1)");

  const svgRef = useRef<SVGSVGElement | null>(null);
  const zoomRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);

  const refreshStats = useCallback(async () => {
    try {
      const s = await getStats();
      setStats(s);
      if (!focusId && s.persons > 0) {
        const roots = await getRoots();
        if (roots.length) setFocusId(roots[0].id);
      }
    } catch (e) {
      onError((e as Error).message);
    }
  }, [focusId, onError]);

  useEffect(() => {
    void refreshStats();
  }, [refreshStats]);

  useEffect(() => {
    if (!focusId) {
      setGraph(null);
      setDetail(null);
      return;
    }
    (async () => {
      try {
        setGraph(await getSubtree(focusId, depth));
        setDetail(await getPerson(focusId));
      } catch (e) {
        onError((e as Error).message);
      }
    })();
  }, [focusId, depth, onError]);

  const layout = useMemo(() => (graph ? computeLayout(graph) : null), [graph]);

  // Attach pan/zoom once.
  useEffect(() => {
    if (!svgRef.current) return;
    const sel = select(svgRef.current);
    const z = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 2.5])
      .on("zoom", (e) => setTransform(e.transform.toString()));
    sel.call(z);
    zoomRef.current = z;
    return () => {
      sel.on(".zoom", null);
    };
  }, []);

  // Recenter on the focus node whenever the layout changes.
  useEffect(() => {
    if (!layout || !svgRef.current || !zoomRef.current || !graph) return;
    const focus = layout.nodes.find((n) => n.id === graph.focus);
    const w = svgRef.current.clientWidth || 900;
    const tx = w / 2 - ((focus?.x ?? 0) + NODE_W / 2);
    select(svgRef.current).call(zoomRef.current.transform, zoomIdentity.translate(tx, 60));
  }, [layout, graph]);

  async function runSearch(q: string) {
    setQuery(q);
    if (q.trim().length < 1) return setResults([]);
    try {
      setResults(await searchPersons(q));
    } catch (e) {
      onError((e as Error).message);
    }
  }

  async function onImport(file: File) {
    try {
      await importGedcom(file);
      setFocusId(null);
      await refreshStats();
    } catch (e) {
      onError((e as Error).message);
    }
  }

  const navigate = (id: string) => {
    setResults([]);
    setQuery("");
    setFocusId(id);
  };

  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div className="row" style={{ justifyContent: "space-between", padding: "0.75rem 1rem", borderBottom: "1px solid #33415540" }}>
        <div className="row">
          <strong>Árbol</strong>
          {stats && (
            <span className="muted">
              {stats.persons} personas · {stats.families} familias · {stats.events} eventos
            </span>
          )}
        </div>
        <div className="row">
          <label className="badge" style={{ cursor: "pointer" }}>
            Importar GEDCOM
            <input
              type="file"
              accept=".ged,.gedcom"
              style={{ display: "none" }}
              onChange={(e) => e.target.files && onImport(e.target.files[0])}
            />
          </label>
          <button className="secondary" disabled={!stats?.persons} onClick={() => downloadGedcom()}>
            Exportar
          </button>
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>
                {d} gen.
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="row" style={{ padding: "0.5rem 1rem", position: "relative" }}>
        <input
          placeholder="Buscar persona…"
          value={query}
          onChange={(e) => runSearch(e.target.value)}
          style={{ flex: 1 }}
        />
        {results.length > 0 && (
          <div className="card" style={{ position: "absolute", top: "3rem", left: "1rem", right: "1rem", zIndex: 5, maxHeight: 260, overflow: "auto", margin: 0 }}>
            {results.map((r) => (
              <div key={r.id} className="row" style={{ justifyContent: "space-between", padding: ".3rem 0", cursor: "pointer" }} onClick={() => navigate(r.id)}>
                <span>{displayName(r)}</span>
                <span className="muted">{lifespan(r)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: "flex", height: "68vh" }}>
        <svg ref={svgRef} style={{ flex: 1, background: "#0b1220", cursor: "grab" }}>
          <g transform={transform}>
            {layout?.parentLinks.map((l, i) => (
              <path key={`p${i}`} d={`M${l.x1},${l.y1} C${l.x1},${(l.y1 + l.y2) / 2} ${l.x2},${(l.y1 + l.y2) / 2} ${l.x2},${l.y2}`} fill="none" stroke="#475569" strokeWidth={1.5} />
            ))}
            {layout?.coupleLinks.map((l, i) => (
              <line key={`c${i}`} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke="#64748b" strokeWidth={1.5} strokeDasharray="4 3" />
            ))}
            {layout?.nodes.map((n) => {
              const isFocus = n.id === graph?.focus;
              return (
                <g key={n.id} transform={`translate(${n.x},${n.y})`} style={{ cursor: "pointer" }} onClick={() => navigate(n.id)}>
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={8}
                    fill={isFocus ? "#1e293b" : "#111c30"}
                    stroke={isFocus ? "#38bdf8" : "#334155"}
                    strokeWidth={isFocus ? 2.5 : 1}
                  />
                  <rect width={6} height={NODE_H} rx={3} fill={SEX_COLOR[n.sex] || SEX_COLOR.U} />
                  <text x={16} y={26} fill="#e2e8f0" fontSize={13} fontWeight={600}>
                    {displayName(n).slice(0, 22)}
                  </text>
                  <text x={16} y={44} fill="#94a3b8" fontSize={11}>
                    {lifespan(n)}
                  </text>
                  {n.has_documents && <circle cx={NODE_W - 14} cy={16} r={4} fill="#34d399" />}
                  {n.deduction_count > 0 && <circle cx={NODE_W - 28} cy={16} r={4} fill="#fbbf24" />}
                </g>
              );
            })}
          </g>
        </svg>

        <div style={{ width: 300, borderLeft: "1px solid #33415540", padding: "1rem", overflow: "auto" }}>
          {!detail && <p className="muted">Importa un GEDCOM o selecciona una persona.</p>}
          {detail && (
            <>
              <h3 style={{ margin: "0 0 .25rem" }}>{detail.names[0] ? displayName(detail.names[0]) : "(sin nombre)"}</h3>
              <PersonRelations title="Eventos">
                {detail.events.length === 0 && <span className="muted">—</span>}
                {detail.events.map((ev, i) => (
                  <div key={i} className="muted" style={{ fontSize: ".85rem" }}>
                    <strong style={{ color: "#cbd5e1" }}>{ev.type}</strong> {ev.date_raw || ""}{" "}
                    {ev.place ? `· ${ev.place}` : ""}
                  </div>
                ))}
              </PersonRelations>
              <Relatives label="Padres" people={detail.parents} onPick={navigate} />
              <Relatives label="Cónyuges" people={detail.spouses} onPick={navigate} />
              <Relatives label="Hijos" people={detail.children} onPick={navigate} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function PersonRelations({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: ".75rem" }}>
      <div className="muted" style={{ fontSize: ".75rem", textTransform: "uppercase", letterSpacing: ".05em" }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function Relatives({
  label,
  people,
  onPick,
}: {
  label: string;
  people: { id: string; given: string | null; surname: string | null; birth_year: number | null; death_year: number | null }[];
  onPick: (id: string) => void;
}) {
  if (people.length === 0) return null;
  return (
    <PersonRelations title={label}>
      {people.map((p) => (
        <div key={p.id} className="row" style={{ justifyContent: "space-between", cursor: "pointer", padding: ".15rem 0" }} onClick={() => onPick(p.id)}>
          <span style={{ color: "#38bdf8" }}>{displayName(p)}</span>
          <span className="muted">{lifespan(p)}</span>
        </div>
      ))}
    </PersonRelations>
  );
}
