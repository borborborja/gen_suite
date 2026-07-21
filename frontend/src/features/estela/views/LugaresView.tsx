import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import {
  listPlaces, getPlace, patchPlace, mergePlace, geocodePlace, listPlaceEvents,
  PLACE_TYPE_LABEL,
  type PlaceRow, type PlaceDetail, type PlaceEventRow, type PlaceRef,
} from "../../../api/tree";
import { field, ghostBtn, miniBtn, modalCard, overlay, primaryBtn, useConfirm, useDebouncedSearch } from "../ui";

const PAGE_SIZE = 50;
const EVENT_LABEL: Record<string, string> = {
  birth: "Nacimiento", baptism: "Bautismo", marriage: "Matrimonio", death: "Defunción",
  burial: "Entierro", residence: "Residencia", census: "Censo",
};

export default function LugaresView() {
  const e = useEstela();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"name" | "events">("events");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: PlaceRow[] } | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    listPlaces({ q: q.trim() || undefined, sort, order, page, page_size: PAGE_SIZE })
      .then(setData).catch(() => setData({ total: 0, items: [] }));
  }, [q, sort, order, page]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, q]);

  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const toggleSort = (k: "name" | "events") => {
    setPage(1);
    if (sort === k) setOrder((o) => (o === "asc" ? "desc" : "asc"));
    else { setSort(k); setOrder(k === "events" ? "desc" : "asc"); }
  };
  const arrow = (k: string) => (sort === k ? (order === "asc" ? " ↑" : " ↓") : "");
  const th: CSSProperties = { textAlign: "left", padding: "11px 14px", fontSize: 11.5, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".05em", cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" };
  const td: CSSProperties = { padding: "11px 14px", fontSize: 13.5, borderTop: "1px solid var(--line2)" };

  return (
    <section style={{ padding: "32px 44px 64px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 22 }}>
        <div>
          <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Lugares</h1>
          <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0" }}>
            {data ? `${data.total.toLocaleString()} lugares en tu árbol` : "Cargando…"} — renombra, fusiona duplicados y organiza la jerarquía.
          </p>
        </div>
        <input value={q} onChange={(ev) => { setQ(ev.target.value); setPage(1); }} placeholder="Buscar lugar…" style={{ ...field, width: 240 }} />
      </div>

      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 13, overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th} onClick={() => toggleSort("name")}>Lugar{arrow("name")}</th>
                <th style={th}>Tipo</th>
                <th style={th}>Pertenece a</th>
                <th style={th} onClick={() => toggleSort("events")}>Eventos{arrow("events")}</th>
                <th style={th}>Coordenadas</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((p) => (
                <tr key={p.id} onClick={() => setOpen(p.id)} style={{ cursor: "pointer" }}>
                  <td style={{ ...td, fontWeight: 600 }}>{p.name}{p.children_count > 0 && <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginLeft: 8 }}>+{p.children_count} dentro</span>}</td>
                  <td style={{ ...td, color: "var(--muted)" }}>{p.place_type ? PLACE_TYPE_LABEL[p.place_type] ?? p.place_type : "—"}</td>
                  <td style={{ ...td, color: "var(--muted)" }}>{p.parent_name ?? "—"}</td>
                  <td style={{ ...td, fontFamily: fonts.mono, fontSize: 12.5 }}>{p.event_count}</td>
                  <td style={{ ...td, fontFamily: fonts.mono, fontSize: 11.5, color: p.lat != null ? "var(--ok)" : "var(--muted)" }}>
                    {p.lat != null ? `${p.lat.toFixed(3)}, ${p.lng!.toFixed(3)}` : "sin geocodificar"}
                  </td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr><td style={{ ...td, color: "var(--muted)" }} colSpan={5}>Sin lugares{q ? " que coincidan" : " todavía — aparecen al añadir hechos con lugar o importar un GEDCOM"}.</td></tr>
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

      {open && <PlacePanel placeId={open} onClose={() => setOpen(null)} onChanged={load} onOpenPerson={(id) => e.openPerson(id)} />}
    </section>
  );
}

const lbl: CSSProperties = { display: "flex", flexDirection: "column", gap: 4, fontSize: 11.5, color: "var(--muted)" };
const fld: CSSProperties = { ...field, background: "var(--bg)", fontSize: 13.5, width: "100%" };

function PlacePanel({ placeId, onClose, onChanged, onOpenPerson }: {
  placeId: string; onClose: () => void; onChanged: () => void; onOpenPerson: (id: string) => void;
}) {
  const e = useEstela();
  const { confirmDialog, ask } = useConfirm();
  const [p, setP] = useState<PlaceDetail | null>(null);
  const [events, setEvents] = useState<PlaceEventRow[]>([]);
  const [name, setName] = useState("");
  const [ptype, setPtype] = useState("");
  const [busy, setBusy] = useState(false);
  const [mergeTarget, setMergeTarget] = useState<PlaceRef | null>(null);
  const [parentPick, setParentPick] = useState(false);

  const reload = useCallback(() => {
    getPlace(placeId).then((d) => { setP(d); setName(d.name); setPtype(d.place_type ?? ""); }).catch(onClose);
    listPlaceEvents(placeId).then((r) => setEvents(r.items)).catch(() => setEvents([]));
  }, [placeId, onClose]);
  useEffect(() => { reload(); }, [reload]);

  async function run(fn: () => Promise<unknown>, okMsg: string) {
    setBusy(true);
    try { await fn(); e.notify(okMsg, "var(--ok)"); reload(); onChanged(); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
    setBusy(false);
  }

  if (!p) return null;
  return (
    <div onClick={onClose} style={{ ...overlay, alignItems: "flex-start", overflowY: "auto", padding: "50px 20px" }}>
      {confirmDialog}
      <div onClick={(ev) => ev.stopPropagation()} style={{ ...modalCard, maxWidth: 640 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 2 }}>
          <h2 style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 600, margin: 0 }}>{p.name}</h2>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer" }}>✕</button>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 16 }}>
          {p.breadcrumb.length > 0
            ? [...p.breadcrumb.map((b) => b.name), p.name].join(" → ")
            : "Sin jerarquía asignada"}
          {" · "}{p.event_count} eventos
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
          <label style={lbl}>Nombre<input style={fld} value={name} onChange={(ev) => setName(ev.target.value)} /></label>
          <label style={lbl}>Tipo
            <select style={fld} value={ptype} onChange={(ev) => setPtype(ev.target.value)}>
              <option value="">—</option>
              {Object.entries(PLACE_TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 }}>
          <button disabled={busy} onClick={() => run(() => patchPlace(p.id, { name: name.trim(), place_type: ptype || undefined }), "Lugar guardado")} style={primaryBtn}>Guardar</button>
          <button disabled={busy} onClick={() => setParentPick(true)} style={ghostBtn}>{p.parent_name ? `Pertenece a ${p.parent_name} · cambiar` : "Asignar a…"}</button>
          {p.parent_id && <button disabled={busy} onClick={() => run(() => patchPlace(p.id, { clear_parent: true }), "Jerarquía quitada")} style={ghostBtn}>Quitar padre</button>}
          <button disabled={busy} onClick={() => setMergeTarget({ id: "", name: "", place_type: null })} style={ghostBtn}>Fusionar en…</button>
          <button disabled={busy} onClick={() => run(() => geocodePlace(p.id), "Coordenadas fijadas")} style={ghostBtn} title="Busca coordenadas usando la jerarquía como contexto">
            {p.lat != null ? "Re-geocodificar" : "Geocodificar"}
          </button>
        </div>

        {p.children.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 8 }}>Contiene</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {p.children.map((c) => <span key={c.id} style={{ background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 999, padding: "4px 11px", fontSize: 12 }}>{c.name}</span>)}
            </div>
          </div>
        )}

        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 8 }}>Eventos en este lugar</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 260, overflowY: "auto" }}>
          {events.map((ev) => (
            <div key={ev.id} style={{ display: "flex", alignItems: "center", gap: 10, background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 9, padding: "8px 12px" }}>
              <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{EVENT_LABEL[ev.type] ?? ev.type}{ev.person_name ? ` · ${ev.person_name}` : ""}</span>
              <span style={{ fontFamily: fonts.mono, fontSize: 11.5, color: "var(--muted)" }}>{ev.date_raw ?? ev.date_year ?? ""}</span>
              {ev.person_id && <button onClick={() => onOpenPerson(ev.person_id!)} style={{ ...miniBtn }}>ficha ↗</button>}
            </div>
          ))}
          {events.length === 0 && <div style={{ fontSize: 12.5, color: "var(--muted)" }}>Sin eventos.</div>}
        </div>

        {parentPick && (
          <PlacePickDialog title={`¿A qué lugar pertenece ${p.name}?`} excludeId={p.id} onClose={() => setParentPick(false)}
            onPick={(t) => { setParentPick(false); void run(() => patchPlace(p.id, { parent_id: t.id }), `Ahora pertenece a ${t.name}`); }} />
        )}
        {mergeTarget && (
          <PlacePickDialog title={`Fusionar «${p.name}» dentro de…`} excludeId={p.id} onClose={() => setMergeTarget(null)}
            onPick={async (t) => {
              setMergeTarget(null);
              const ok = await ask({ title: `¿Fusionar «${p.name}» dentro de «${t.name}»?`, body: `Los ${p.event_count} eventos pasan a ${t.name} y «${p.name}» desaparece. No se puede deshacer desde aquí.`, danger: true, confirmLabel: "Fusionar" });
              if (!ok) return;
              try { await mergePlace(p.id, t.id); e.notify("Lugares fusionados", "var(--ok)"); onChanged(); onClose(); }
              catch (err) { e.notify((err as Error).message, "var(--danger)"); }
            }} />
        )}
      </div>
    </div>
  );
}

function PlacePickDialog({ title, excludeId, onPick, onClose }: {
  title: string; excludeId: string; onPick: (p: PlaceRef) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const results = useDebouncedSearch(q, async (v) => {
    const r = await listPlaces({ q: v, page_size: 20 });
    return r.items.filter((it) => it.id !== excludeId);
  }, { minLength: 1 });
  return (
    <div onClick={onClose} style={{ ...overlay, zIndex: 120 }}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ ...modalCard, maxWidth: 400 }}>
        <h3 style={{ margin: "0 0 12px", fontSize: 15.5, fontWeight: 600 }}>{title}</h3>
        <input autoFocus style={fld} value={q} onChange={(ev) => setQ(ev.target.value)} placeholder="Buscar lugar…" />
        <div style={{ marginTop: 8, maxHeight: 240, overflowY: "auto" }}>
          {results.map((r) => (
            <div key={r.id} onClick={() => onPick(r)} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "9px 11px", cursor: "pointer", borderBottom: "1px solid var(--line2)" }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{r.name}</span>
              <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)" }}>{r.event_count} ev.</span>
            </div>
          ))}
          {q.trim() && results.length === 0 && <div style={{ fontSize: 12.5, color: "var(--muted)", padding: 8 }}>Sin resultados.</div>}
        </div>
        <button onClick={onClose} style={{ ...ghostBtn, marginTop: 12 }}>Cancelar</button>
      </div>
    </div>
  );
}
