import { useState, type CSSProperties } from "react";
import { useEstela } from "./store";
import { fonts } from "./theme";
import {
  addRelative, getRelationship, searchPersons, displayName, lifespan,
  type SearchHit, type RelationshipOut,
} from "../../api/tree";
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
