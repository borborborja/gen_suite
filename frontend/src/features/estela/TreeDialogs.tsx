import { useEffect, useState, type CSSProperties } from "react";
import { useEstela } from "./store";
import { fonts } from "./theme";
import {
  addRelative, addFamilyEvent, createCitation, getFactTypes, getRelationship, searchPersons,
  displayName, lifespan,
  type SearchHit, type RelationshipOut, type FamilyOut, type FactType, type CitationBody,
} from "../../api/tree";
import { geoSearch } from "../../api/geo";
import { listDocuments, type DocumentOut } from "../../api/documents";
import { field, ghostBtn, modalCard, overlay, primaryBtn, useDebouncedSearch } from "./ui";

const lbl: CSSProperties = { display: "flex", flexDirection: "column", gap: 4, fontSize: 11.5, color: "var(--muted)" };
const fld: CSSProperties = { ...field, background: "var(--bg)", fontSize: 13.5, width: "100%" };

/** Typeahead sobre las personas del árbol; devuelve la persona elegida. */
export function PersonPicker({ value, onPick, placeholder }: {
  value: SearchHit | null;
  onPick: (p: SearchHit | null) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const res = useDebouncedSearch(q, (v) => searchPersons(v));
  if (value) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 11px" }}>
        <span style={{ flex: 1, fontSize: 13.5, fontWeight: 600 }}>{displayName(value)}</span>
        <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>{lifespan(value)}</span>
        <button onClick={() => onPick(null)} style={{ background: "transparent", border: "none", color: "var(--muted)", cursor: "pointer", fontSize: 14 }}>✕</button>
      </div>
    );
  }
  return (
    <div style={{ position: "relative" }}>
      <input
        value={q}
        onChange={(ev) => { setQ(ev.target.value); setOpen(true); }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder ?? "Buscar persona…"}
        style={fld}
      />
      {open && res.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 40, background: "var(--elevated)", border: "1px solid var(--line)", borderRadius: 9, marginTop: 4, boxShadow: "var(--shadow)", maxHeight: 240, overflowY: "auto" }}>
          {res.map((p) => (
            <div key={p.id} onMouseDown={() => { onPick(p); setQ(""); setOpen(false); }} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "9px 12px", cursor: "pointer", borderBottom: "1px solid var(--line2)" }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{displayName(p)}</span>
              <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>{lifespan(p)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Campo de lugar con autocompletado Nominatim; devuelve nombre y coords si se elige del desplegable. */
export function PlaceField({ value, onPick }: { value: string; onPick: (name: string, coords: { lat: number; lng: number } | null) => void }) {
  const [q, setQ] = useState(value);
  const [open, setOpen] = useState(false);
  const results = useDebouncedSearch(q, (v) => geoSearch(v), { delay: 350 });
  return (
    <label style={{ ...lbl, position: "relative" }}>Lugar
      <input style={fld} value={q} onChange={(e) => { setQ(e.target.value); onPick(e.target.value, null); setOpen(true); }} onBlur={() => setTimeout(() => setOpen(false), 150)} placeholder="Escribe y elige…" />
      {open && results.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30, background: "var(--elevated)", border: "1px solid var(--line)", borderRadius: 8, marginTop: 4, boxShadow: "var(--shadow)", maxHeight: 180, overflowY: "auto" }}>
          {results.map((r, i) => (
            <div key={i} onMouseDown={() => { setQ(r.name); onPick(r.name, { lat: r.lat, lng: r.lng }); setOpen(false); }} style={{ padding: "7px 11px", cursor: "pointer", borderBottom: i < results.length - 1 ? "1px solid var(--line2)" : "none" }}>
              <div style={{ fontWeight: 600, fontSize: 12.5, color: "var(--fg)" }}>{r.name}</div>
              <div style={{ fontSize: 10.5, color: "var(--muted)" }}>{r.display_name}</div>
            </div>
          ))}
        </div>
      )}
    </label>
  );
}

/** Hecho de pareja (matrimonio, divorcio…): se ancla en la familia, visible desde ambos cónyuges. */
export function AddFamilyEventDialog({ families, personName, onClose, onDone }: {
  families: FamilyOut[];
  personName: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const e = useEstela();
  const [familyId, setFamilyId] = useState(families[0]?.id ?? "");
  const [factTypes, setFactTypes] = useState<FactType[]>([]);
  const [type, setType] = useState("marriage");
  const [date, setDate] = useState("");
  const [place, setPlace] = useState("");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getFactTypes().then((ts) => setFactTypes(ts.filter((t) => t.scope === "family"))).catch(() => setFactTypes([]));
  }, []);

  async function save() {
    if (!familyId) return;
    setBusy(true);
    try {
      await addFamilyEvent(familyId, { type, date_raw: date || undefined, place: place || undefined, place_lat: coords?.lat, place_lng: coords?.lng, value: value || undefined });
      e.notify("Hecho de pareja añadido", "var(--ok)");
      onDone(); onClose();
    } catch (err) { e.notify((err as Error).message, "var(--danger)"); setBusy(false); }
  }

  return (
    <div onClick={onClose} style={overlay}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ ...modalCard, maxWidth: 420 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 20, fontWeight: 600, margin: 0 }}>Hecho de pareja</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>de <b style={{ color: "var(--fg)" }}>{personName}</b> y su pareja — se verá en la ficha de ambos.</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {families.length > 1 && (
            <label style={lbl}>Pareja
              <select style={fld} value={familyId} onChange={(ev) => setFamilyId(ev.target.value)}>
                {families.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.spouse ? `Con ${displayName(f.spouse)}` : "(pareja sin identificar)"}
                  </option>
                ))}
              </select>
            </label>
          )}
          {families.length === 1 && families[0].spouse && (
            <div style={{ fontSize: 13, color: "var(--muted)" }}>Con <b style={{ color: "var(--fg)" }}>{displayName(families[0].spouse)}</b></div>
          )}
          <label style={lbl}>Tipo
            <select style={fld} value={type} onChange={(ev) => setType(ev.target.value)}>
              {factTypes.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          </label>
          <label style={lbl}>Fecha<input style={fld} value={date} onChange={(ev) => setDate(ev.target.value)} placeholder="12 JUN 1878 / 1878" /></label>
          <PlaceField value={place} onPick={(n, c) => { setPlace(n); setCoords(c); }} />
          <label style={lbl}>Detalle<input style={fld} value={value} onChange={(ev) => setValue(ev.target.value)} /></label>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 18, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={ghostBtn}>Cancelar</button>
          <button onClick={save} disabled={busy || !familyId} style={{ ...primaryBtn, opacity: busy || !familyId ? 0.5 : 1 }}>{busy ? "Guardando…" : "Añadir"}</button>
        </div>
      </div>
    </div>
  );
}

/** Cita manual: vincular una persona o un hecho con un documento/página de la biblioteca. */
export function AddCitationDialog({ targetType, targetId, targetLabel, onClose, onDone }: {
  targetType: "person" | "event";
  targetId: string;
  targetLabel: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const e = useEstela();
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [q, setQ] = useState("");
  const [doc, setDoc] = useState<DocumentOut | null>(null);
  const [pageNo, setPageNo] = useState(1);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { listDocuments("mine").then(setDocs).catch(() => setDocs([])); }, []);
  const filtered = q.trim()
    ? docs.filter((d) => d.title.toLowerCase().includes(q.trim().toLowerCase()))
    : docs;

  async function save() {
    setBusy(true);
    try {
      const body: CitationBody = { note: note.trim() || undefined };
      if (doc) { body.document_id = doc.id; body.page_no = pageNo; }
      await createCitation(targetType, targetId, body);
      e.notify("Fuente añadida", "var(--ok)");
      onDone(); onClose();
    } catch (err) { e.notify((err as Error).message, "var(--danger)"); setBusy(false); }
  }

  return (
    <div onClick={onClose} style={overlay}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ ...modalCard, maxWidth: 460 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 20, fontWeight: 600, margin: 0 }}>Añadir fuente</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>Evidencia de <b style={{ color: "var(--fg)" }}>{targetLabel}</b>: un documento de tu biblioteca y/o una nota.</p>

        {doc ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 9, padding: "9px 12px", marginBottom: 10 }}>
            <span style={{ flex: 1, fontSize: 13.5, fontWeight: 600 }}>{doc.title}</span>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
              pág.
              <input type="number" min={1} max={doc.page_count} value={pageNo}
                onChange={(ev) => setPageNo(Math.max(1, Math.min(doc.page_count, Number(ev.target.value) || 1)))}
                style={{ ...fld, width: 70, padding: "6px 8px" }} />
              <span>/ {doc.page_count}</span>
            </label>
            <button onClick={() => setDoc(null)} style={{ background: "transparent", border: "none", color: "var(--muted)", cursor: "pointer", fontSize: 14 }}>✕</button>
          </div>
        ) : (
          <div style={{ marginBottom: 10 }}>
            <input style={fld} value={q} onChange={(ev) => setQ(ev.target.value)} placeholder="Buscar documento en tu biblioteca…" />
            {filtered.length > 0 && (
              <div style={{ border: "1px solid var(--line)", borderRadius: 9, marginTop: 6, maxHeight: 200, overflowY: "auto" }}>
                {filtered.slice(0, 30).map((d) => (
                  <div key={d.id} onClick={() => { setDoc(d); setPageNo(1); }} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "9px 12px", cursor: "pointer", borderBottom: "1px solid var(--line2)" }}>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>{d.title}</span>
                    <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>{d.page_count} págs</span>
                  </div>
                ))}
              </div>
            )}
            {docs.length === 0 && <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 6 }}>No hay documentos en tu biblioteca; puedes citar solo con una nota.</div>}
          </div>
        )}

        <label style={lbl}>Nota<textarea style={{ ...fld, minHeight: 64, resize: "vertical" }} value={note} onChange={(ev) => setNote(ev.target.value)} placeholder="Folio, acta nº, observaciones…" /></label>

        <div style={{ display: "flex", gap: 8, marginTop: 18, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={ghostBtn}>Cancelar</button>
          <button onClick={save} disabled={busy || (!doc && !note.trim())} style={{ ...primaryBtn, opacity: busy || (!doc && !note.trim()) ? 0.5 : 1 }}>{busy ? "Guardando…" : "Añadir fuente"}</button>
        </div>
      </div>
    </div>
  );
}

const REL_OPTIONS = [
  { v: "father", l: "Padre", sex: "M" },
  { v: "mother", l: "Madre", sex: "F" },
  { v: "spouse", l: "Cónyuge", sex: "U" },
  { v: "child", l: "Hijo/a", sex: "U" },
];

/** Añadir un familiar a `personId`: creando una persona nueva o vinculando una existente. */
export function AddRelativeDialog({ personId, personName, initialRelation, onClose, onDone }: {
  personId: string;
  personName: string;
  initialRelation?: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const e = useEstela();
  const [relation, setRelation] = useState(initialRelation ?? "father");
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [given, setGiven] = useState("");
  const [surname, setSurname] = useState("");
  const [sex, setSex] = useState("");
  const [existing, setExisting] = useState<SearchHit | null>(null);
  const [busy, setBusy] = useState(false);

  const effSex = sex || REL_OPTIONS.find((r) => r.v === relation)?.sex || "U";
  const canSave = mode === "new" ? !!(given.trim() || surname.trim()) : !!existing;

  async function save() {
    setBusy(true);
    try {
      if (mode === "existing" && existing) {
        await addRelative(personId, { relation, relative_id: existing.id });
      } else {
        await addRelative(personId, { relation, given: given.trim() || undefined, surname: surname.trim() || undefined, sex: effSex });
      }
      e.notify("Familiar añadido", "var(--ok)");
      onDone();
      onClose();
    } catch (err) {
      e.notify((err as Error).message, "var(--danger)");
      setBusy(false);
    }
  }

  return (
    <div onClick={onClose} style={overlay}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ ...modalCard, maxWidth: 420 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 20, fontWeight: 600, margin: 0 }}>Añadir familiar</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>de <b style={{ color: "var(--fg)" }}>{personName}</b></p>

        <label style={{ ...lbl, marginBottom: 12 }}>Parentesco
          <select style={fld} value={relation} onChange={(ev) => setRelation(ev.target.value)}>
            {REL_OPTIONS.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
          </select>
        </label>

        <div style={{ display: "inline-flex", background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 9, padding: 3, gap: 2, marginBottom: 14 }}>
          {([["new", "Persona nueva"], ["existing", "Ya está en el árbol"]] as const).map(([k, l]) => (
            <span key={k} onClick={() => setMode(k)} style={{ padding: "7px 12px", borderRadius: 7, cursor: "pointer", fontSize: 12.5, fontWeight: mode === k ? 600 : 500, color: mode === k ? "#fff" : "var(--muted)", background: mode === k ? "var(--accent)" : "transparent" }}>{l}</span>
          ))}
        </div>

        {mode === "new" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={lbl}>Nombre<input style={fld} value={given} onChange={(ev) => setGiven(ev.target.value)} autoFocus /></label>
            <label style={lbl}>Apellidos<input style={fld} value={surname} onChange={(ev) => setSurname(ev.target.value)} /></label>
            <label style={lbl}>Sexo
              <select style={fld} value={effSex} onChange={(ev) => setSex(ev.target.value)}>
                <option value="U">—</option><option value="M">Hombre</option><option value="F">Mujer</option>
              </select>
            </label>
          </div>
        ) : (
          <PersonPicker value={existing} onPick={setExisting} placeholder="Buscar en el árbol…" />
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 18, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={ghostBtn}>Cancelar</button>
          <button onClick={save} disabled={!canSave || busy} style={{ ...primaryBtn, opacity: !canSave || busy ? 0.5 : 1 }}>
            {busy ? "Guardando…" : "Añadir"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Calculadora de parentesco: elige dos personas y muestra la relación + la cadena. */
export function RelationshipDialog({ initialA, onClose }: {
  initialA?: SearchHit | null;
  onClose: () => void;
}) {
  const e = useEstela();
  const [a, setA] = useState<SearchHit | null>(initialA ?? null);
  const [b, setB] = useState<SearchHit | null>(null);
  const [result, setResult] = useState<RelationshipOut | null>(null);
  const [busy, setBusy] = useState(false);

  async function compute() {
    if (!a || !b) return;
    setBusy(true);
    setResult(null);
    try { setResult(await getRelationship(a.id, b.id)); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    setBusy(false);
  }

  return (
    <div onClick={onClose} style={overlay}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ ...modalCard, maxWidth: 520 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 20, fontWeight: 600, margin: 0 }}>¿Qué parentesco tienen?</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>Elige dos personas del árbol y Estela nombra la relación.</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <PersonPicker value={a} onPick={(p) => { setA(p); setResult(null); }} placeholder="Primera persona…" />
          <PersonPicker value={b} onPick={(p) => { setB(p); setResult(null); }} placeholder="Segunda persona…" />
        </div>
        <button onClick={compute} disabled={!a || !b || busy} style={{ ...primaryBtn, marginTop: 14, opacity: !a || !b || busy ? 0.5 : 1 }}>
          {busy ? "Calculando…" : "Calcular parentesco"}
        </button>

        {result && a && b && (
          <div style={{ marginTop: 18, background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 11, padding: 16 }}>
            <div style={{ fontSize: 14.5 }}>
              <b>{displayName(b)}</b> es <b style={{ color: "var(--accent)" }}>{result.label}</b>
              {result.related && result.label !== "la misma persona" ? <> de <b>{displayName(a)}</b></> : null}
            </div>
            {result.path.length > 1 && (
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, marginTop: 12 }}>
                {result.path.map((s, i) => (
                  <span key={s.person.id + String(i)} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    {i > 0 && <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--accent)" }}>→ {s.step}</span>}
                    <span style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 999, padding: "4px 11px", fontSize: 12, fontWeight: 600 }}>{displayName(s.person)}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
