import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import {
  listChanges, getChange, revertChange,
  type ChangeItem, type ChangeDetail, type ChangeRowImage,
} from "../../../api/tree";
import { miniBtn, useConfirm } from "../ui";

const PAGE_SIZE = 50;

const ACTION_LABEL: Record<string, string> = {
  person_create: "Persona creada", person_update: "Identidad editada", person_delete: "Persona eliminada",
  person_merge: "Personas fusionadas", event_add: "Hecho añadido", event_edit: "Hecho editado",
  event_delete: "Hecho borrado", family_event_add: "Hecho de pareja añadido",
  relative_add: "Pariente añadido", relative_unlink: "Pariente desvinculado",
  citation_add: "Fuente añadida", citation_update: "Fuente editada", citation_delete: "Fuente borrada",
  place_update: "Lugar editado", place_merge: "Lugares fusionados", place_geocode: "Lugar geocodificado",
  revert: "Reversión",
};

const TABLE_LABEL: Record<string, string> = {
  persons: "persona", names: "nombre", events: "hecho", families: "familia",
  family_children: "hijo de familia", citations: "cita", places: "lugar",
};

// campos internos que no aportan al diff visible
const HIDDEN_KEYS = new Set(["id", "tenant_id", "created_at", "updated_at", "normalized_key", "raw", "gedcom_xref"]);

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("es-ES", { day: "2-digit", month: "short", year: "numeric" }) +
    " " + d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

export default function HistorialView() {
  const e = useEstela();
  const { confirmDialog, ask } = useConfirm();
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: ChangeItem[] } | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChangeDetail | null>(null);

  const load = useCallback(() => {
    listChanges(page, PAGE_SIZE).then(setData).catch(() => setData({ total: 0, items: [] }));
  }, [page]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    setDetail(null);
    if (!openId) return;
    let live = true; // ignora respuestas de un cambio ya cerrado/reemplazado
    getChange(openId).then((d) => { if (live) setDetail(d); }).catch(() => { if (live) setDetail(null); });
    return () => { live = false; };
  }, [openId]);

  async function doRevert(c: ChangeItem) {
    const ok = await ask({
      title: `¿Revertir «${c.summary ?? ACTION_LABEL[c.action] ?? c.action}»?`,
      body: "Se aplicará el cambio inverso. Si los datos han cambiado desde entonces, se rechazará sin tocar nada.",
      danger: true, confirmLabel: "Revertir", typed: "REVERTIR",
    });
    if (!ok) return;
    try {
      await revertChange(c.id);
      e.notify("Cambio revertido", "var(--ok)");
      load();
      if (openId === c.id) setOpenId(null);
    } catch (err) { e.notify((err as Error).message, "var(--danger)"); }
  }

  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1080 }}>
      {confirmDialog}
      <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Historial</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 22px" }}>
        {data ? `${data.total.toLocaleString()} cambios registrados` : "Cargando…"} — cada edición del árbol queda anotada y puede revertirse.
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {data?.items.map((c) => (
          <div key={c.id} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden" }}>
            <div onClick={() => setOpenId(openId === c.id ? null : c.id)} style={{ display: "flex", alignItems: "center", gap: 12, padding: "13px 16px", cursor: "pointer", flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                  {c.summary ?? ACTION_LABEL[c.action] ?? c.action}
                  {c.reverted_at && <span style={{ marginLeft: 8, fontFamily: fonts.mono, fontSize: 9.5, background: "var(--warn-faint)", color: "var(--warn)", border: "1px dashed var(--warn)", borderRadius: 5, padding: "2px 7px" }}>REVERTIDO</span>}
                  {c.action === "revert" && <span style={{ marginLeft: 8, fontFamily: fonts.mono, fontSize: 9.5, background: "var(--accent-faint)", color: "var(--accent)", borderRadius: 5, padding: "2px 7px" }}>↩</span>}
                </div>
                <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>
                  {fmtDate(c.created_at)}{c.actor_email ? ` · ${c.actor_email}` : ""} · {c.rows_count} fila{c.rows_count !== 1 ? "s" : ""}
                </div>
              </div>
              {c.entity_type === "person" && c.entity_id && (
                <button onClick={(ev) => { ev.stopPropagation(); e.openPerson(c.entity_id!); }} style={miniBtn}>ficha ↗</button>
              )}
              {!c.reverted_at && (
                <button onClick={(ev) => { ev.stopPropagation(); void doRevert(c); }} style={{ ...miniBtn, color: "var(--danger)", borderColor: "var(--danger)" }}>Revertir</button>
              )}
              <span style={{ color: "var(--muted)", fontSize: 12 }}>{openId === c.id ? "▲" : "▼"}</span>
            </div>
            {openId === c.id && (
              <div style={{ borderTop: "1px solid var(--line2)", padding: "12px 16px", background: "var(--bg)" }}>
                {!detail && <div style={{ fontSize: 12.5, color: "var(--muted)" }}>Cargando…</div>}
                {detail?.rows.map((r, i) => <RowDiff key={i} row={r} />)}
              </div>
            )}
          </div>
        ))}
        {data && data.items.length === 0 && (
          <div style={{ color: "var(--muted)", fontSize: 13.5, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 24 }}>
            Aún no hay cambios registrados: aparecerán aquí al editar el árbol.
          </div>
        )}
      </div>

      {pages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} style={{ ...miniBtn, opacity: page <= 1 ? 0.4 : 1 }}>‹</button>
          <span style={{ fontFamily: fonts.mono, fontSize: 12, color: "var(--muted)" }}>{page} / {pages}</span>
          <button onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page >= pages} style={{ ...miniBtn, opacity: page >= pages ? 0.4 : 1 }}>›</button>
        </div>
      )}
    </section>
  );
}

const cell: CSSProperties = { fontFamily: fonts.mono, fontSize: 11.5, padding: "2px 8px", borderRadius: 5 };

function RowDiff({ row }: { row: ChangeRowImage }) {
  const kind = row.before === null ? "creada" : row.after === null ? "borrada" : "editada";
  const color = kind === "creada" ? "var(--ok)" : kind === "borrada" ? "var(--danger)" : "var(--accent)";
  const keys = Object.keys({ ...(row.before ?? {}), ...(row.after ?? {}) })
    .filter((k) => !HIDDEN_KEYS.has(k))
    .filter((k) => kind !== "editada" || String(row.before?.[k] ?? "") !== String(row.after?.[k] ?? ""));
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
        <span style={{ color }}>{TABLE_LABEL[row.table] ?? row.table} {kind}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {keys.map((k) => {
          const b = row.before?.[k], a = row.after?.[k];
          return (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)", minWidth: 110 }}>{k}</span>
              {kind !== "creada" && <span style={{ ...cell, background: "var(--danger)" + "18", color: "var(--danger)", textDecoration: kind === "editada" ? "line-through" : "none" }}>{String(b ?? "∅")}</span>}
              {kind === "editada" && <span style={{ color: "var(--muted)" }}>→</span>}
              {kind !== "borrada" && <span style={{ ...cell, background: "var(--ok-faint)", color: "var(--ok)" }}>{String(a ?? "∅")}</span>}
            </div>
          );
        })}
        {keys.length === 0 && <span style={{ fontSize: 11.5, color: "var(--muted)" }}>(sin campos visibles)</span>}
      </div>
    </div>
  );
}
