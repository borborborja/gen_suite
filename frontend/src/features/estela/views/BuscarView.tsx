import { useEffect, useState, type CSSProperties } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import { SearchIcon } from "../icons";
import { searchRecords, suggest, type RecordHit, type RecordFilters, type Suggestion } from "../../../api/search";
import { searchPersons, displayName, lifespan, type SearchHit as PersonHit } from "../../../api/tree";
import { listSources, type ExternalSource } from "../../../api/sources";
import { getRecordTypes, listDocuments, type RecordType, type DocumentOut } from "../../../api/documents";
import { geoSearch, type GeoResult } from "../../../api/geo";
import { roleLabel } from "../labels";
import { useDebouncedSearch } from "../ui";

const tabs = [
  { key: "archivo", label: "En el archivo" },
  { key: "arbol", label: "En mi árbol" },
  { key: "fuentes", label: "Fuentes externas" },
];

// Search filter options, labelled from the shared dictionary. "principal" is generic here
// because the filter spans all record types.
const ROLE_KEYS = ["principal", "head", "father", "mother", "spouse", "son", "daughter",
  "child", "sibling", "godfather", "godmother", "witness", "testator"];
const ROLES = [
  { v: "", l: "(cualquier rol)" },
  ...ROLE_KEYS.map((k) => ({ v: k, l: roleLabel(k) })),
];

interface Filters {
  text: string; given: string; surname: string; recordType: string; documentId: string;
  place: string; region: string; yearFrom: string; yearTo: string; role: string;
  semantic: boolean; exact: boolean;
}
const EMPTY: Filters = { text: "", given: "", surname: "", recordType: "", documentId: "", place: "", region: "", yearFrom: "", yearTo: "", role: "", semantic: false, exact: false };

export default function BuscarView() {
  const e = useEstela();
  const [f, setF] = useState<Filters>(EMPTY);
  const [types, setTypes] = useState<RecordType[]>([]);
  const [records, setRecords] = useState<RecordHit[]>([]);
  const [persons, setPersons] = useState<PersonHit[]>([]);
  const [sources, setSources] = useState<ExternalSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [sugs, setSugs] = useState<{ field: "surname" | "given" | "place"; value: string; count: number }[]>([]);

  useEffect(() => { getRecordTypes().then(setTypes).catch(() => setTypes([])); }, []);
  useEffect(() => { listDocuments("mine").then(setDocs).catch(() => setDocs([])); }, []);
  const set = (patch: Partial<Filters>) => setF((prev) => ({ ...prev, ...patch }));
  const typeLabel = (k: string) => types.find((t) => t.key === k)?.label ?? k;

  const active = !!(f.text.trim() || f.given.trim() || f.surname.trim() || f.recordType || f.documentId || f.place.trim() || f.region.trim() || f.yearFrom || f.yearTo || f.role);

  // "Did you mean?" suggestions for the name/place fields (only on the archivo tab).
  useEffect(() => {
    if (e.searchTab !== "archivo") { setSugs([]); return; }
    const cand: { field: "surname" | "given" | "place"; value: string }[] = [
      { field: "surname", value: f.surname.trim() },
      { field: "given", value: f.given.trim() },
      { field: "place", value: f.place.trim() },
    ];
    const fields = cand.filter((x) => x.value.length >= 2);
    if (fields.length === 0) { setSugs([]); return; }
    const t = setTimeout(async () => {
      try {
        const all = await Promise.all(fields.map(async (x) => {
          const res = await suggest(x.field, x.value);
          return res
            .filter((s: Suggestion) => s.value.toLowerCase() !== x.value.toLowerCase())
            .slice(0, 5)
            .map((s) => ({ field: x.field, value: s.value, count: s.count }));
        }));
        setSugs(all.flat());
      } catch { setSugs([]); }
    }, 400);
    return () => clearTimeout(t);
  }, [f.surname, f.given, f.place, e.searchTab]);

  useEffect(() => {
    if (!active) { setRecords([]); setPersons([]); setSources([]); setErr(null); return; }
    setLoading(true); setErr(null);
    const t = setTimeout(async () => {
      try {
        if (e.searchTab === "archivo") {
          const rf: RecordFilters = {
            q: f.text || undefined, given: f.given || undefined, surname: f.surname || undefined,
            record_type: f.recordType || undefined, document_id: f.documentId || undefined,
            place: f.place || undefined, year_from: f.yearFrom || undefined, year_to: f.yearTo || undefined,
            role: f.role || undefined, semantic: f.semantic || undefined,
            fuzzy: f.exact ? false : undefined,
          };
          setRecords(await searchRecords(rf));
        } else if (e.searchTab === "arbol") {
          setPersons(await searchPersons(f.text, {
            given: f.given || undefined, surname: f.surname || undefined,
            year_from: f.yearFrom || undefined, year_to: f.yearTo || undefined,
          }));
        } else {
          // Fuentes externas: derive given/surname from free text if the explicit fields are empty.
          let given = f.given, surname = f.surname;
          if (!given && !surname && f.text.trim()) {
            const toks = f.text.trim().split(/\s+/);
            given = toks[0]; surname = toks.slice(1).join(" ");
          }
          setSources(await listSources({
            given, surname, place: f.place, year_from: f.yearFrom, year_to: f.yearTo, region: f.region,
          }));
        }
      } catch (e2) { setErr((e2 as Error).message || "error de búsqueda"); }
      setLoading(false);
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(f), e.searchTab]);

  const byCategory = sources.reduce<Record<string, ExternalSource[]>>((acc, s) => {
    (acc[s.category_label] ??= []).push(s); return acc;
  }, {});

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1040 }}>
      <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Buscar</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 22px" }}>Filtra por tipo de acta, fechas, municipio, nombre o rol. Los mismos filtros buscan en tu archivo, tu árbol y las fuentes externas.</p>

      {/* free text + semantic toggle */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: "14px 18px", marginBottom: 12, boxShadow: "var(--shadow)" }}>
        <SearchIcon size={20} stroke="var(--muted)" />
        <input autoFocus value={f.text} onChange={(ev) => set({ text: ev.target.value })} placeholder={'Texto libre — admite "frase exacta", OR y -excluir'} style={{ flex: 1, background: "transparent", border: "none", outline: "none", color: "var(--fg)", fontFamily: fonts.sans, fontSize: 16 }} />
        {e.searchTab === "archivo" && (
          <>
            <label title="Solo coincidencias exactas (desactiva tolerancia a erratas y variantes)" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)", cursor: "pointer", flex: "none" }}>
              <input type="checkbox" checked={f.exact} onChange={(ev) => set({ exact: ev.target.checked })} /> exacta
            </label>
            <label title="Ordenar por similitud semántica en vez de coincidencia exacta" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)", cursor: "pointer", flex: "none" }}>
              <input type="checkbox" checked={f.semantic} onChange={(ev) => set({ semantic: ev.target.checked })} /> semántica
            </label>
          </>
        )}
      </div>

      {/* structured filters */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, marginBottom: 18 }}>
        <Field label="Nombre"><input style={fld} value={f.given} onChange={(ev) => set({ given: ev.target.value })} placeholder="Manuel" /></Field>
        <Field label="Apellido"><input style={fld} value={f.surname} onChange={(ev) => set({ surname: ev.target.value })} placeholder="Tapia" /></Field>
        {e.searchTab === "archivo" && (
          <Field label="Tipo de acta"><select style={fld} value={f.recordType} onChange={(ev) => set({ recordType: ev.target.value })}><option value="">(cualquiera)</option>{types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</select></Field>
        )}
        {e.searchTab === "archivo" && (
          <Field label="Rol"><select style={fld} value={f.role} onChange={(ev) => set({ role: ev.target.value })}>{ROLES.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}</select></Field>
        )}
        {e.searchTab === "archivo" && (
          <Field label="Libro"><select style={fld} value={f.documentId} onChange={(ev) => set({ documentId: ev.target.value })}><option value="">(cualquiera)</option>{docs.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}</select></Field>
        )}
        <Field label="Municipio"><PlaceInput value={f.place} onChange={(v) => set({ place: v })} /></Field>
        {e.searchTab === "fuentes" && (
          <Field label="Provincia / Región"><input style={fld} value={f.region} onChange={(ev) => set({ region: ev.target.value })} placeholder="Córdoba" /></Field>
        )}
        <Field label="Año desde"><input style={fld} value={f.yearFrom} onChange={(ev) => set({ yearFrom: ev.target.value.replace(/\D/g, "") })} placeholder="1850" /></Field>
        <Field label="Año hasta"><input style={fld} value={f.yearTo} onChange={(ev) => set({ yearTo: ev.target.value.replace(/\D/g, "") })} placeholder="1860" /></Field>
      </div>

      {e.searchTab === "archivo" && sugs.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 18, marginTop: -6 }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>¿Quizás quisiste decir?</span>
          {sugs.map((s, i) => (
            <button key={s.field + s.value + i} onClick={() => set({ [s.field === "place" ? "place" : s.field]: s.value } as Partial<Filters>)}
              style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 999, padding: "5px 12px", fontFamily: "inherit", fontSize: 12.5, color: "var(--fg)", cursor: "pointer" }}>
              {s.value}{s.count > 0 ? <span style={{ color: "var(--muted)", fontFamily: fonts.mono, fontSize: 10.5 }}> · {s.count}</span> : null}
            </button>
          ))}
        </div>
      )}

      <div style={{ display: "flex", borderBottom: "1px solid var(--line2)", marginBottom: 20 }}>
        {tabs.map((t) => {
          const a = e.searchTab === t.key;
          return <span key={t.key} onClick={() => e.setSearchTab(t.key)} style={{ padding: "12px 4px", marginRight: 22, cursor: "pointer", fontSize: 15, fontWeight: a ? 600 : 500, color: a ? "var(--fg)" : "var(--muted)", borderBottom: a ? "2px solid var(--accent)" : "2px solid transparent", marginBottom: -1 }}>{t.label}</span>;
        })}
      </div>

      {!active && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Introduce un nombre, tipo de acta, lugar o fechas para empezar.</div>}
      {loading && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Buscando…</div>}
      {err && !loading && (
        <div style={{ background: "var(--danger-faint, rgba(192,57,43,.08))", border: "1px solid var(--danger)", color: "var(--danger)", borderRadius: 10, padding: "11px 15px", fontSize: 13 }}>
          La búsqueda falló: {err}. No es que no haya resultados — revisa que el servidor esté activo.
        </div>
      )}

      {active && e.searchTab === "fuentes" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          <p style={{ color: "var(--muted)", fontSize: 13, margin: 0 }}>Estela abre cada archivo externo con la búsqueda ya rellenada (nombre, lugar y fechas). Fuentes españolas; añade una provincia para ver las provinciales.</p>
          {Object.entries(byCategory).map(([cat, list]) => (
            <div key={cat}>
              <div style={{ fontFamily: fonts.mono, fontSize: 10.5, letterSpacing: ".12em", color: "var(--muted)", marginBottom: 10, textTransform: "uppercase" }}>{cat}</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(280px,1fr))", gap: 12 }}>
                {list.map((s) => (
                  <a key={s.key} href={s.url} target="_blank" rel="noreferrer" style={{ textDecoration: "none", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: "14px 16px", display: "flex", flexDirection: "column", gap: 5 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                      <span style={{ fontWeight: 600, fontSize: 14, color: "var(--fg)" }}>{s.name}</span>
                      <span style={{ color: "var(--accent)", fontSize: 13, flex: "none" }}>Abrir ↗</span>
                    </div>
                    {s.note && <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.4 }}>{s.note}</div>}
                    {s.region && <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--accent)" }}>{s.region}</span>}
                  </a>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {active && !loading && e.searchTab === "archivo" && records.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Sin actas que coincidan.</div>}
      {active && e.searchTab === "archivo" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {records.map((r) => (
            <div key={r.mention_id ?? r.record_id} onClick={() => e.openDoc(r.document_id, r.page_no ?? undefined)} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: "14px 18px", cursor: "pointer" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600, fontSize: 14.5 }}>{typeLabel(r.record_type)}{(r.date_raw || r.date_year) ? ` · ${r.date_raw || r.date_year}` : ""}</span>
                {r.place && <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{r.place}</span>}
                <span style={{ marginLeft: "auto", fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{r.document_title || "Documento"}{r.page_no ? ` · pág. ${r.page_no}` : ""}</span>
              </div>
              <div style={{ fontSize: 13.5, marginTop: 6 }}>
                <span style={{ fontWeight: 600 }}>{displayName({ given: r.given, surname: r.surname })}</span>
                {r.role && <span style={{ color: "var(--muted)" }}> · {roleLabel(r.role, r.record_type)}</span>}
              </div>
              {r.summary && <div style={{ fontFamily: fonts.serif, fontSize: 13, color: "var(--muted)", marginTop: 4, lineHeight: 1.45 }}>{r.summary}</div>}
            </div>
          ))}
        </div>
      )}

      {active && !loading && e.searchTab === "arbol" && persons.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Sin personas que coincidan.</div>}
      {active && e.searchTab === "arbol" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(248px,1fr))", gap: 14 }}>
          {persons.map((p) => (
            <div key={p.id} onClick={() => e.openPerson(p.id)} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 13, padding: 16, cursor: "pointer" }}>
              <div style={{ fontWeight: 600, fontSize: 14.5 }}>{displayName(p)}</div>
              <div style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)", marginTop: 3 }}>{lifespan(p)}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

const fld: CSSProperties = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5, width: "100%", boxSizing: "border-box" };

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--muted)", position: "relative" }}>
      {label}{children}
    </label>
  );
}

function PlaceInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const results = useDebouncedSearch<GeoResult>(value, (v) => geoSearch(v), { delay: 350 });
  return (
    <>
      <input style={fld} value={value} onChange={(ev) => { onChange(ev.target.value); setOpen(true); }} onBlur={() => setTimeout(() => setOpen(false), 150)} placeholder="Belmez…" />
      {open && results.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30, background: "var(--elevated)", border: "1px solid var(--line)", borderRadius: 8, marginTop: 4, boxShadow: "var(--shadow)", maxHeight: 180, overflowY: "auto" }}>
          {results.map((r, i) => (
            <div key={i} onMouseDown={() => { onChange(r.name); setOpen(false); }} style={{ padding: "7px 11px", cursor: "pointer", borderBottom: i < results.length - 1 ? "1px solid var(--line2)" : "none" }}>
              <div style={{ fontWeight: 600, fontSize: 12.5, color: "var(--fg)" }}>{r.name}</div>
              <div style={{ fontSize: 10.5, color: "var(--muted)" }}>{r.display_name}</div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
