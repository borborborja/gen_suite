import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useEstela, avatar } from "../store";
import { fonts } from "../theme";
import { ArrowLeft, SearchPlus } from "../icons";
import {
  getPerson, displayName, lifespan, getFactTypes, updatePerson, addEvent, editEvent, deleteEvent,
  addRelative, unlinkRelative, deletePerson, getGaps, getCitations, geocodePlaces,
  getPersonFamilies, deleteCitation,
  type PersonDetail, type EventOut, type Related, type FactType, type ResearchGap,
  type CitationOut, type FamilyOut,
} from "../../../api/tree";
import LifeMap, { type MapPoint } from "../LifeMap";
import { listMedia, uploadMedia, updateMedia, deleteMedia, mediaObjectUrl, type MediaItem } from "../../../api/media";
import { useConfirm } from "../ui";
import { AddRelativeDialog, AddFamilyEventDialog, AddCitationDialog, PlaceField } from "../TreeDialogs";
import PrintReport from "../PrintReport";

function fsUrl(s: ResearchGap["search"]): string {
  const p = new URLSearchParams();
  if (s.given) p.set("q.givenName", s.given);
  if (s.surname) p.set("q.surname", s.surname);
  if (s.place) p.set("q.anyPlace", s.place);
  return `https://www.familysearch.org/search/record/results?${p.toString()}`;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const EVENT_LABEL: Record<string, string> = {
  birth: "Nacimiento", baptism: "Bautismo", marriage: "Matrimonio", death: "Defunción",
  burial: "Entierro", confirmation: "Confirmación", residence: "Residencia", census: "Censo",
  divorce: "Divorcio", engagement: "Compromiso", occupation: "Oficio",
};

function chip(inferred: boolean): CSSProperties {
  const base: CSSProperties = { fontFamily: fonts.mono, fontSize: 9.5, letterSpacing: ".06em", padding: "3px 8px", borderRadius: 5, fontWeight: 500, textTransform: "uppercase" };
  return inferred
    ? { ...base, background: "var(--warn-faint)", color: "var(--warn)", border: "1px dashed var(--warn)" }
    : { ...base, background: "var(--ok-faint)", color: "var(--ok)", border: "1px solid var(--ok)" };
}

function initials(given: string | null, surname: string | null): string {
  return ((given?.[0] ?? "") + (surname?.[0] ?? "")).toUpperCase() || "··";
}

export default function PersonaView() {
  const e = useEstela();
  const [p, setP] = useState<PersonDetail | null>(null);
  const [err, setErr] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editEvt, setEditEvt] = useState<EventOut | null>(null);
  const [gaps, setGaps] = useState<ResearchGap[]>([]);
  const [cites, setCites] = useState<CitationOut[]>([]);
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [addRel, setAddRel] = useState(false);
  const [families, setFamilies] = useState<FamilyOut[]>([]);
  const [famEvtOpen, setFamEvtOpen] = useState(false);
  const [citTarget, setCitTarget] = useState<{ type: "person" | "event"; id: string; label: string } | null>(null);
  const [printOpen, setPrintOpen] = useState(false);
  const { confirmDialog, ask } = useConfirm();

  // Blob object-URLs must be revoked when replaced (person change, reload) or on unmount,
  // or every photo ever viewed stays pinned in memory for the session. The unmount cleanup
  // reads a ref — a setState updater would be dropped by React on an unmounted component.
  const urlsRef = useRef<Record<string, string>>({});
  // Response-order guard: switching persons fast must never let a slow response for the
  // previous person overwrite the current one (or revoke its fresh object-URLs).
  const seq = useRef(0);
  const revokeAll = (u: Record<string, string>) => Object.values(u).forEach((x) => URL.revokeObjectURL(x));
  const setUrlsRevoking = (next: Record<string, string>) => {
    urlsRef.current = next;
    setUrls((prev) => { revokeAll(prev); return next; });
  };
  useEffect(() => () => revokeAll(urlsRef.current), []);

  const loadMedia = (id: string, mine: number) => {
    listMedia(id).then(async (items) => {
      if (seq.current !== mine) return;
      setMedia(items);
      const next: Record<string, string> = {};
      await Promise.all(items.map(async (m) => {
        try { next[m.id] = await mediaObjectUrl(m.id); } catch { /* skip */ }
      }));
      if (seq.current !== mine) { revokeAll(next); return; }
      setUrlsRevoking(next);
    }).catch(() => { if (seq.current === mine) { setMedia([]); setUrlsRevoking({}); } });
  };

  const fetchAll = (id: string) => {
    const mine = ++seq.current;
    const guard = <T,>(set: (v: T) => void) => (v: T) => { if (seq.current === mine) set(v); };
    getPerson(id).then(guard(setP)).catch(() => { if (seq.current === mine) setErr(true); });
    getGaps(id).then(guard(setGaps)).catch(() => guard(setGaps)([]));
    getCitations(id).then(guard(setCites)).catch(() => guard(setCites)([]));
    getPersonFamilies(id).then(guard(setFamilies)).catch(() => guard(setFamilies)([]));
    loadMedia(id, mine);
  };
  const reload = () => { if (UUID_RE.test(e.selPerson)) fetchAll(e.selPerson); };
  useEffect(() => {
    seq.current++;
    setP(null); setErr(false); setEditOpen(false); setGaps([]); setCites([]); setMedia([]); setFamilies([]); setUrlsRevoking({});
    if (!UUID_RE.test(e.selPerson)) { setErr(true); return; }
    fetchAll(e.selPerson);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchAll is stable-by-construction (seq guard)
  }, [e.selPerson]);

  if (err) return (
    <section style={{ padding: "32px 44px" }}>
      <Back />
      <p style={{ color: "var(--muted)" }}>Abre una persona desde Mi árbol para ver su ficha real.</p>
    </section>
  );
  if (!p) return <section style={{ padding: "32px 44px" }}><Back /><p style={{ color: "var(--muted)" }}>Cargando…</p></section>;

  const primary = p.names.find((n) => n.is_primary) ?? p.names[0];
  const name = displayName({ given: primary?.given ?? null, surname: primary?.surname ?? null });
  const birth = p.events.find((ev) => ev.type === "birth");
  const death = p.events.find((ev) => ev.type === "death");
  const span = lifespan({ birth_year: birth?.date_year ?? null, death_year: death?.date_year ?? null });
  const place = p.events.find((ev) => ev.place)?.place ?? null;
  const hasInferred = p.names.some((n) => n.is_inferred) || p.events.some((ev) => ev.is_inferred);
  const pAv = avatar(hasInferred ? "inferred" : "confirmed");
  const latin = p.names.find((n) => !n.is_primary && n.given);

  const family: { id: string; name: string; rel: string; relation: string }[] = [
    ...p.parents.map((r) => famItem(r, r.sex === "F" ? "Madre" : "Padre", "parent")),
    ...(p.siblings ?? []).map((r) => famItem(r, r.sex === "F" ? "Hermana" : "Hermano", "sibling")),
    ...p.spouses.map((r) => famItem(r, "Cónyuge", "spouse")),
    ...p.children.map((r) => famItem(r, r.sex === "F" ? "Hija" : "Hijo", "child")),
  ];

  const failed = (err: unknown) => e.notify((err as Error).message || "No se pudo guardar", "var(--danger)");
  async function removeRelative(relativeId: string, relation: string) {
    if (!(await ask({ title: "¿Desvincular este familiar?", body: "La persona no se borra, solo el parentesco.", confirmLabel: "Desvincular" }))) return;
    try { await unlinkRelative(p!.id, relativeId, relation); reload(); } catch (err) { failed(err); }
  }
  async function removeEvent(eventId: string) {
    if (!(await ask({ title: "¿Borrar este hecho?", danger: true, confirmLabel: "Borrar" }))) return;
    try { await deleteEvent(eventId); reload(); } catch (err) { failed(err); }
  }
  async function removePerson() {
    if (!(await ask({ title: `¿Eliminar a ${name}?`, body: "Se borran sus nombres, hechos y parentescos. No se puede deshacer.", danger: true, confirmLabel: "Eliminar" }))) return;
    try { await deletePerson(p!.id); e.go("arbol"); } catch (err) { failed(err); }
  }
  async function onUploadPhoto(file: File) {
    try { await uploadMedia(p!.id, file); reload(); } catch { e.notify("No se pudo subir la foto"); }
  }
  async function onSetPrimary(id: string) {
    try { await updateMedia(id, { make_primary: true }); reload(); } catch (err) { failed(err); }
  }
  async function onDeletePhoto(id: string) {
    if (!(await ask({ title: "¿Borrar esta foto?", danger: true, confirmLabel: "Borrar" }))) return;
    try { await deleteMedia(id); reload(); } catch (err) { failed(err); }
  }
  async function onGeocode() {
    e.notify("Geocodificando lugares… (1/seg, puede tardar)");
    try { const r = await geocodePlaces(60); e.notify(`Geocodificados ${r.geocoded} lugares · ${r.remaining} pendientes`); reload(); }
    catch { e.notify("No se pudieron geocodificar los lugares"); }
  }

  const mapPoints: MapPoint[] = p.events
    .filter((ev) => ev.place_lat != null && ev.place_lng != null)
    .map((ev) => ({ lat: ev.place_lat!, lng: ev.place_lng!, label: EVENT_LABEL[ev.type] ?? ev.type, sub: [ev.date_raw || ev.date_year, ev.place].filter(Boolean).join(" · ") }));
  const hasPlacesWithoutCoords = p.events.some((ev) => ev.place && ev.place_lat == null);

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1080 }}>
      {confirmDialog}
      <Back />
      <div style={{ display: "flex", gap: 22, alignItems: "flex-start", flexWrap: "wrap", marginBottom: 30 }}>
        {(() => {
          const prim = media.find((m) => m.is_primary) ?? media[0];
          const purl = prim ? urls[prim.id] : undefined;
          return purl
            ? <img src={purl} onClick={() => setLightbox(purl)} alt="" style={{ width: 84, height: 84, borderRadius: 18, flex: "none", objectFit: "cover", cursor: "zoom-in" }} />
            : <div style={{ width: 84, height: 84, borderRadius: 18, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, background: pAv.bg, color: pAv.fg }}>{initials(primary?.given ?? null, primary?.surname ?? null)}</div>;
        })()}
        <div style={{ flex: 1, minWidth: 240 }}>
          <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 36, margin: 0, letterSpacing: "-.02em", lineHeight: 1.05 }}>{name}</h1>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 10, color: "var(--muted)", fontSize: 14 }}>
            {latin && <><span style={{ fontFamily: fonts.serif, fontStyle: "italic", color: "var(--fg)" }}>aparece como «{latin.given}»</span><span style={{ opacity: 0.4 }}>·</span></>}
            {span && <><span>{span}</span><span style={{ opacity: 0.4 }}>·</span></>}
            {place && <span>{place}</span>}
          </div>
          {hasInferred && (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginTop: 14, background: "var(--warn-faint)", border: "1px solid var(--warn)", color: "var(--warn)", borderRadius: 999, padding: "6px 13px", fontSize: 12.5, fontWeight: 600 }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--warn)" }} />Tiene datos inferidos
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: "none" }}>
          <button onClick={() => discover(p.id, e)} style={{ display: "inline-flex", alignItems: "center", gap: 9, background: "var(--accent)", color: "#fff", border: "none", borderRadius: 9, padding: "13px 20px", fontFamily: "inherit", fontSize: 14.5, fontWeight: 600, cursor: "pointer", boxShadow: "0 5px 18px rgba(217,83,30,.3)" }}>
            <SearchPlus size={17} />Buscar registros
          </button>
          <button onClick={() => setEditOpen((v) => !v)} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8, background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 9, padding: "11px 20px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
            {editOpen ? "Cerrar edición" : "✎ Editar"}
          </button>
          <button onClick={() => setPrintOpen(true)} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8, background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 9, padding: "11px 20px", fontFamily: "inherit", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>
            🖨 Imprimir ficha
          </button>
          {editOpen && (
            <button onClick={removePerson} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8, background: "transparent", color: "var(--danger)", border: "1px solid var(--danger)", borderRadius: 9, padding: "11px 20px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer" }}>
              Eliminar persona
            </button>
          )}
        </div>
      </div>

      {editOpen && <EditPanel person={p} onChange={reload} />}

      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 22 }}>
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 22 }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Línea de vida</h3>
            {editOpen && (
              <button
                onClick={() => families.length === 0
                  ? e.notify("Añade primero un cónyuge para registrar hechos de pareja", "var(--warn)")
                  : setFamEvtOpen(true)}
                style={{ background: "transparent", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 12px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}
              >⚭ Hecho de pareja</button>
            )}
          </div>
          {p.events.length === 0 && <p style={{ color: "var(--muted)", fontSize: 13.5 }}>Sin eventos registrados.</p>}
          <div style={{ position: "relative", paddingLeft: 28 }}>
            {p.events.length > 0 && <div style={{ position: "absolute", left: 7, top: 6, bottom: 6, width: 2, background: "var(--line)" }} />}
            <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
              {p.events.map((ev: EventOut, i) => (
                <div key={ev.id || i} style={{ position: "relative" }}>
                  <div style={{ position: "absolute", left: -28, top: 3, width: 16, height: 16, borderRadius: "50%", border: "3px solid var(--surface)", background: ev.is_inferred ? "var(--warn)" : "var(--ok)" }} />
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>
                      {EVENT_LABEL[ev.type] ?? ev.type}
                      {ev.family_id && (
                        <span title="Hecho de pareja" style={{ marginLeft: 8, fontFamily: fonts.mono, fontSize: 10, fontWeight: 500, color: "var(--accent)", background: "var(--accent-faint)", borderRadius: 5, padding: "2px 7px" }}>
                          ⚭ {ev.spouse_name ?? "pareja"}
                        </span>
                      )}
                    </span>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      {editOpen && (
                        <>
                          <button title="Citar fuente" onClick={() => setCitTarget({ type: "event", id: ev.id, label: EVENT_LABEL[ev.type] ?? ev.type })} style={miniBtn}>❞</button>
                          <button title="Editar" onClick={() => setEditEvt(ev)} style={miniBtn}>✎</button>
                          <button title="Borrar" onClick={() => removeEvent(ev.id)} style={{ ...miniBtn, color: "var(--danger)" }}>✕</button>
                        </>
                      )}
                      <span style={chip(ev.is_inferred)}>{ev.is_inferred ? "Inferido" : "Confirmado"}</span>
                    </div>
                  </div>
                  <div style={{ fontSize: 13, color: "var(--muted)", margin: "4px 0 0" }}>{[ev.date_raw || ev.date_year, ev.place].filter(Boolean).join(" · ")}</div>
                  {editEvt?.id === ev.id && (
                    <EventEditor event={ev} scope={ev.family_id ? "family" : "person"} onClose={() => setEditEvt(null)} onSaved={() => { setEditEvt(null); reload(); }} />
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Familia</h3>
            <button onClick={() => setAddRel(true)} style={{ background: "transparent", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 12px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>＋ Añadir</button>
          </div>
          {family.length === 0 && <p style={{ color: "var(--muted)", fontSize: 13.5 }}>Sin familiares registrados.</p>}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {family.map((fm) => (
              <div key={fm.id + fm.rel} style={{ display: "flex", alignItems: "center", gap: 11, padding: 8, borderRadius: 9 }}>
                <div onClick={() => e.openPerson(fm.id)} style={{ display: "flex", alignItems: "center", gap: 11, flex: 1, minWidth: 0, cursor: "pointer" }}>
                  <div style={{ width: 34, height: 34, borderRadius: 8, flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 13, background: "var(--ok-faint)", color: "var(--ok)" }}>{fm.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13.5, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{fm.name}</div>
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>{fm.rel}</div>
                  </div>
                </div>
                {editOpen && <button title="Desvincular" onClick={() => removeRelative(fm.id, fm.relation)} style={{ ...miniBtn, color: "var(--danger)" }}>✕</button>}
              </div>
            ))}
          </div>
        </div>
      </div>

      {(mapPoints.length > 0 || hasPlacesWithoutCoords) && (
        <div style={{ marginTop: 22, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, gap: 12, flexWrap: "wrap" }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Mapa de su vida</h3>
            {hasPlacesWithoutCoords && (
              <button onClick={onGeocode} style={{ background: "transparent", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 14px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>
                Geocodificar lugares
              </button>
            )}
          </div>
          {mapPoints.length > 0
            ? <LifeMap points={mapPoints} />
            : <p style={{ color: "var(--muted)", fontSize: 13.5, margin: 0 }}>Hay lugares sin coordenadas. Pulsa «Geocodificar lugares» para situarlos en el mapa.</p>}
        </div>
      )}

      {(media.length > 0 || editOpen) && (
        <div style={{ marginTop: 22, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Fotos</h3>
            {editOpen && (
              <label style={{ ...saveBtn, display: "inline-flex", alignItems: "center", gap: 6 }}>
                + Subir foto
                <input type="file" accept="image/*" style={{ display: "none" }} onChange={(ev) => { const f = ev.target.files?.[0]; if (f) onUploadPhoto(f); ev.target.value = ""; }} />
              </label>
            )}
          </div>
          {media.length === 0
            ? <p style={{ color: "var(--muted)", fontSize: 13.5, margin: 0 }}>Sin fotos. Sube un retrato para usarlo como imagen de esta persona.</p>
            : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(110px, 1fr))", gap: 12 }}>
                {media.map((m) => (
                  <div key={m.id} style={{ position: "relative", borderRadius: 10, overflow: "hidden", border: m.is_primary ? "2px solid var(--accent)" : "1px solid var(--line)" }}>
                    {urls[m.id]
                      ? <img src={urls[m.id]} onClick={() => setLightbox(urls[m.id])} alt={m.caption ?? ""} style={{ width: "100%", height: 110, objectFit: "cover", display: "block", cursor: "zoom-in" }} />
                      : <div style={{ width: "100%", height: 110, background: "var(--bg)" }} />}
                    {m.is_primary && <span style={{ position: "absolute", top: 6, left: 6, fontFamily: fonts.mono, fontSize: 9, background: "var(--accent)", color: "#fff", padding: "2px 6px", borderRadius: 4 }}>PORTADA</span>}
                    {editOpen && (
                      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, display: "flex", justifyContent: "space-between", padding: 5, background: "rgba(0,0,0,.45)" }}>
                        {!m.is_primary
                          ? <button title="Hacer portada" onClick={() => onSetPrimary(m.id)} style={{ ...miniBtn, color: "#fff", borderColor: "rgba(255,255,255,.4)" }}>★</button>
                          : <span />}
                        <button title="Borrar" onClick={() => onDeletePhoto(m.id)} style={{ ...miniBtn, color: "#fff", borderColor: "rgba(255,255,255,.4)" }}>✕</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
        </div>
      )}

      {p.notes && (
        <div style={{ marginTop: 22, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
          <h3 style={{ margin: "0 0 10px", fontSize: 16, fontWeight: 600 }}>Notas</h3>
          <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: "var(--fg)", whiteSpace: "pre-wrap" }}>{p.notes}</p>
        </div>
      )}

      <div style={{ marginTop: 22, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Fuentes</h3>
          <button onClick={() => setCitTarget({ type: "person", id: p.id, label: name })} style={{ background: "transparent", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 12px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>＋ Añadir fuente</button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>La evidencia que respalda los datos de esta persona. Cada cita abre el documento original.</p>
        {cites.length === 0 && <p style={{ color: "var(--muted)", fontSize: 13.5, margin: 0 }}>Sin fuentes todavía. Añade de dónde sale cada dato: libro, página y nota.</p>}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {cites.map((c) => (
            <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 14, background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 10, padding: "12px 14px", flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                  {[EVENT_LABEL[c.record_type ?? ""] ?? c.record_type, c.date_raw].filter(Boolean).join(" · ")
                    || c.document_title || "Nota"}
                </div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>{c.summary || c.note || c.document_title || "—"}</div>
              </div>
              {c.document_id
                ? <button onClick={() => e.openDoc(c.document_id!, c.page_no ?? undefined)} style={{ flex: "none", background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "8px 14px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>
                    Ver fuente{c.page_no ? ` · pág. ${c.page_no}` : ""}
                  </button>
                : <span style={{ flex: "none", fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>sin imagen</span>}
              {editOpen && (
                <button title="Borrar cita" style={{ ...miniBtn, color: "var(--danger)" }} onClick={async () => {
                  if (!(await ask({ title: "¿Borrar esta cita?", confirmLabel: "Borrar", danger: true }))) return;
                  try { await deleteCitation(c.id); reload(); } catch (err) { e.notify((err as Error).message, "var(--danger)"); }
                }}>✕</button>
              )}
            </div>
          ))}
        </div>
      </div>

      {gaps.length > 0 && (
        <div style={{ marginTop: 22, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 24 }}>
          <h3 style={{ margin: "0 0 4px", fontSize: 16, fontWeight: 600 }}>Fuentes que faltan</h3>
          <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>Dónde buscar lo que aún no consta. Estela cruza con tu biblioteca.</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {gaps.map((g, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 14, background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 10, padding: "12px 14px", flexWrap: "wrap" }}>
                <span style={{ flex: 1, minWidth: 200, fontSize: 13.5 }}>{g.text}</span>
                {g.have_book && g.book_id ? (
                  <button onClick={() => e.openDoc(g.book_id!)} style={{ flex: "none", background: "var(--ok)", color: "#fff", border: "none", borderRadius: 8, padding: "8px 14px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>
                    Tienes el libro · abrir
                  </button>
                ) : (
                  <span style={{ flex: "none", fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>no en tu biblioteca</span>
                )}
                <a href={fsUrl(g.search)} target="_blank" rel="noreferrer" style={{ flex: "none", textDecoration: "none", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 14px", fontSize: 12.5, fontWeight: 600 }}>FamilySearch ↗</a>
              </div>
            ))}
          </div>
        </div>
      )}

      {addRel && (
        <AddRelativeDialog personId={p.id} personName={name} onClose={() => setAddRel(false)} onDone={reload} />
      )}
      {famEvtOpen && (
        <AddFamilyEventDialog families={families} personName={name} onClose={() => setFamEvtOpen(false)} onDone={reload} />
      )}
      {citTarget && (
        <AddCitationDialog targetType={citTarget.type} targetId={citTarget.id} targetLabel={citTarget.label}
          onClose={() => setCitTarget(null)} onDone={reload} />
      )}
      {printOpen && <PrintReport personId={p.id} onClose={() => setPrintOpen(false)} />}

      {lightbox && (
        <div onClick={() => setLightbox(null)} style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(15,11,6,.85)", display: "flex", alignItems: "center", justifyContent: "center", padding: 40, cursor: "zoom-out" }}>
          <img src={lightbox} alt="" style={{ maxWidth: "92%", maxHeight: "92%", borderRadius: 10, boxShadow: "0 24px 80px rgba(0,0,0,.6)" }} />
        </div>
      )}
    </section>
  );
}

function famItem(r: Related, rel: string, relation: string) {
  return { id: r.id, name: displayName({ given: r.given, surname: r.surname }), rel, relation };
}

const miniBtn: CSSProperties = { background: "transparent", border: "1px solid var(--line)", borderRadius: 6, width: 24, height: 24, lineHeight: 1, padding: 0, fontSize: 12, cursor: "pointer", color: "var(--muted)" };
const fld: CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5, width: "100%", boxSizing: "border-box" };
const lbl: CSSProperties = { display: "flex", flexDirection: "column", gap: 4, fontSize: 11.5, color: "var(--muted)" };
const saveBtn: CSSProperties = { background: "var(--ok)", color: "#fff", border: "none", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer" };

const RELATIONS = [
  { v: "father", l: "Padre" }, { v: "mother", l: "Madre" },
  { v: "spouse", l: "Cónyuge" }, { v: "child", l: "Hijo/a" },
];

function EditPanel({ person, onChange }: { person: PersonDetail; onChange: () => void }) {
  const est = useEstela();
  const primary = person.names.find((n) => n.is_primary) ?? person.names[0];
  const [given, setGiven] = useState(primary?.given ?? "");
  const [surname, setSurname] = useState(primary?.surname ?? "");
  const [sex, setSex] = useState(person.sex);
  const [notes, setNotes] = useState(person.notes ?? "");
  const [factTypes, setFactTypes] = useState<FactType[]>([]);
  const [ft, setFt] = useState("residence");
  const [fdate, setFdate] = useState("");
  const [fvalue, setFvalue] = useState("");
  const [place, setPlace] = useState("");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [rel, setRel] = useState("father");
  const [rgiven, setRgiven] = useState("");
  const [rsurname, setRsurname] = useState("");
  const [rsex, setRsex] = useState("U");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    getFactTypes().then((ts) => setFactTypes(ts.filter((t) => t.scope === "person"))).catch(() => setFactTypes([]));
  }, []);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 2500); };

  const failed = (err: unknown) => est.notify((err as Error).message || "No se pudo guardar", "var(--danger)");
  async function saveIdentity() {
    try {
      await updatePerson(person.id, { given, surname, sex, notes }); flash("Identidad guardada"); onChange();
    } catch (err) { failed(err); }
  }
  async function saveFact() {
    if (!ft) return;
    try {
      await addEvent(person.id, { type: ft, date_raw: fdate || undefined, place: place || undefined, place_lat: coords?.lat, place_lng: coords?.lng, value: fvalue || undefined });
      setFdate(""); setFvalue(""); setPlace(""); setCoords(null); flash("Hecho añadido"); onChange();
    } catch (err) { failed(err); }
  }
  async function saveRel() {
    if (!rgiven && !rsurname) return;
    try {
      await addRelative(person.id, { relation: rel, given: rgiven || undefined, surname: rsurname || undefined, sex: rsex });
      setRgiven(""); setRsurname(""); flash("Pariente añadido"); onChange();
    } catch (err) { failed(err); }
  }

  const card: CSSProperties = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 20 };
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 18, marginBottom: 24 }}>
      <div style={card}>
        <h3 style={{ margin: "0 0 14px", fontSize: 14.5, fontWeight: 600 }}>Identidad</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={lbl}>Nombre<input style={fld} value={given} onChange={(e) => setGiven(e.target.value)} /></label>
          <label style={lbl}>Apellidos<input style={fld} value={surname} onChange={(e) => setSurname(e.target.value)} /></label>
          <label style={lbl}>Sexo<select style={fld} value={sex} onChange={(e) => setSex(e.target.value)}><option value="U">—</option><option value="M">Hombre</option><option value="F">Mujer</option></select></label>
          <label style={lbl}>Notas<textarea style={{ ...fld, minHeight: 64, resize: "vertical" }} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Anotaciones de investigación…" /></label>
          <button onClick={saveIdentity} style={saveBtn}>Guardar</button>
        </div>
      </div>

      <div style={card}>
        <h3 style={{ margin: "0 0 14px", fontSize: 14.5, fontWeight: 600 }}>Añadir hecho <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", fontWeight: 400 }}>GEDCOM</span></h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={lbl}>Tipo<select style={fld} value={ft} onChange={(e) => setFt(e.target.value)}>{factTypes.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</select></label>
          <label style={lbl}>Fecha<input style={fld} value={fdate} onChange={(e) => setFdate(e.target.value)} placeholder="11 DIC 1977 / 1850" /></label>
          <PlaceField value={place} onPick={(n, c) => { setPlace(n); setCoords(c); }} />
          <label style={lbl}>Detalle (oficio, valor…)<input style={fld} value={fvalue} onChange={(e) => setFvalue(e.target.value)} /></label>
          <button onClick={saveFact} style={saveBtn}>Añadir hecho</button>
        </div>
      </div>

      <div style={card}>
        <h3 style={{ margin: "0 0 14px", fontSize: 14.5, fontWeight: 600 }}>Añadir pariente</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={lbl}>Relación<select style={fld} value={rel} onChange={(e) => setRel(e.target.value)}>{RELATIONS.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}</select></label>
          <label style={lbl}>Nombre<input style={fld} value={rgiven} onChange={(e) => setRgiven(e.target.value)} /></label>
          <label style={lbl}>Apellidos<input style={fld} value={rsurname} onChange={(e) => setRsurname(e.target.value)} /></label>
          <label style={lbl}>Sexo<select style={fld} value={rsex} onChange={(e) => setRsex(e.target.value)}><option value="U">—</option><option value="M">Hombre</option><option value="F">Mujer</option></select></label>
          <button onClick={saveRel} style={saveBtn}>Añadir pariente</button>
        </div>
      </div>
      {msg && <div style={{ gridColumn: "1 / -1", fontSize: 12.5, color: "var(--ok)" }}>✓ {msg}</div>}
    </div>
  );
}

function EventEditor({ event, scope = "person", onClose, onSaved }: { event: EventOut; scope?: "person" | "family"; onClose: () => void; onSaved: () => void }) {
  const est = useEstela();
  const [factTypes, setFactTypes] = useState<FactType[]>([]);
  const [type, setType] = useState(event.type);
  const [date, setDate] = useState(event.date_raw ?? (event.date_year ? String(event.date_year) : ""));
  const [value, setValue] = useState(event.value ?? "");
  const [place, setPlace] = useState(event.place ?? "");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(
    event.place_lat != null && event.place_lng != null ? { lat: event.place_lat, lng: event.place_lng } : null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getFactTypes().then((ts) => setFactTypes(ts.filter((t) => t.scope === scope))).catch(() => setFactTypes([]));
  }, [scope]);
  async function save() {
    setBusy(true);
    try {
      await editEvent(event.id, { type, date_raw: date || undefined, place: place || undefined,
        place_lat: coords?.lat, place_lng: coords?.lng, value: value || undefined });
      onSaved();
    } catch (err) {
      est.notify((err as Error).message || "No se pudo guardar el hecho", "var(--danger)");
    } finally { setBusy(false); }
  }
  return (
    <div style={{ marginTop: 10, background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 10, padding: 14, display: "flex", flexDirection: "column", gap: 9 }}>
      <label style={lbl}>Tipo<select style={fld} value={type} onChange={(e) => setType(e.target.value)}>{factTypes.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</select></label>
      <label style={lbl}>Fecha<input style={fld} value={date} onChange={(e) => setDate(e.target.value)} placeholder="11 DIC 1977 / 1850" /></label>
      <PlaceField value={place} onPick={(n, c) => { setPlace(n); setCoords(c); }} />
      <label style={lbl}>Detalle<input style={fld} value={value} onChange={(e) => setValue(e.target.value)} /></label>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={save} disabled={busy} style={saveBtn}>{busy ? "Guardando…" : "Guardar cambios"}</button>
        <button onClick={onClose} style={{ ...saveBtn, background: "transparent", color: "var(--fg)", border: "1px solid var(--line)" }}>Cancelar</button>
      </div>
    </div>
  );
}

async function discover(personId: string, e: ReturnType<typeof useEstela>) {
  // Enqueue the discovery job FIRST and surface failures — don't navigate as if it worked.
  try {
    const { discover } = await import("../../../api/linkage");
    await discover(personId);
  } catch (err) {
    e.notify("No se pudo lanzar la búsqueda: " + (err as Error).message, "var(--danger)");
    return;
  }
  e.notify("Buscando parientes en tus libros…");
  e.go("descubrimientos");
  // the discovery job runs async on the worker — refresh the list as it lands
  setTimeout(() => e.reloadDiscoveries(), 4000);
  setTimeout(() => e.reloadDiscoveries(), 9000);
}

function Back() {
  const e = useEstela();
  return (
    <div onClick={() => e.go("arbol")} style={{ display: "inline-flex", alignItems: "center", gap: 7, color: "var(--muted)", fontSize: 13, cursor: "pointer", marginBottom: 20 }}>
      <ArrowLeft size={16} />Volver a Mi árbol
    </div>
  );
}
