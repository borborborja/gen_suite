import { useCallback, useEffect, useState } from "react";
import { applySyncChanges, listInteropConnections, listSyncChanges, resolveSyncChange, type InteropConnection, type SyncChange } from "../../../api/interop";
import { fonts } from "../theme";
import { ghostBtn, primaryBtn } from "../ui";
import { useEstela } from "../store";

const card = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 13 } as const;

export default function SincronizacionView() {
  const { notify } = useEstela();
  const [connections, setConnections] = useState<InteropConnection[]>([]);
  const [conflicts, setConflicts] = useState<Record<string, SyncChange[]>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    void listInteropConnections().then(async (next) => {
      setConnections(next);
      const entries = await Promise.all(next.map(async (connection) => [connection.id, await listSyncChanges(connection.id, "conflict")] as const));
      setConflicts(Object.fromEntries(entries));
    }).catch((error: Error) => notify(error.message || "No se pudo cargar la sincronización", "var(--danger)"));
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  async function run(key: string, action: () => Promise<void>, success: string) {
    setBusy(key);
    try { await action(); notify(success, "var(--ok)"); load(); }
    catch (error) { notify((error as Error).message, "var(--danger)"); }
    finally { setBusy(null); }
  }

  return <section style={{ padding: "32px 44px 64px", maxWidth: 1060 }}>
    <div style={{ marginBottom: 24 }}>
      <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Sincronización</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0", maxWidth: 740 }}>
        La Companion de Gramps configura la conexión y la línea base. Aquí se revisan los cambios que compiten entre sí: nada fusiona nombres, fechas o parentescos sin una decisión humana.
      </p>
    </div>
    {connections.map((connection) => <div key={connection.id} style={{ ...card, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "15px 17px", borderBottom: "1px solid var(--line2)", flexWrap: "wrap" }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>{connection.name}</div>
          <div style={{ fontFamily: fonts.mono, color: "var(--muted)", fontSize: 10.5, marginTop: 3 }}>{connection.provider === "gramps_desktop" ? "GRAMPS DESKTOP" : "GRAMPS WEB"} · {connection.status.toUpperCase()}</div>
        </div>
        <button disabled={busy === connection.id} style={primaryBtn} onClick={() => void run(connection.id, async () => {
          await applySyncChanges(connection.id);
        }, "Cola aplicada")}>{busy === connection.id ? "Aplicando…" : "Aplicar cola"}</button>
      </div>
      {(conflicts[connection.id] || []).map((change) => <Conflict key={change.id} change={change} busy={busy === change.id} onResolve={(resolution) => void run(change.id,
        () => resolveSyncChange(change.id, resolution).then(() => undefined), resolution === "accept_remote" ? "Cambio remoto aplicado" : "Cambio remoto rechazado")} />)}
      {(conflicts[connection.id] || []).length === 0 && <div style={{ padding: "16px 17px", color: "var(--muted)", fontSize: 12.5 }}>No hay conflictos pendientes para esta conexión.</div>}
    </div>)}
    {connections.length === 0 && <div style={{ ...card, padding: 20, color: "var(--muted)", fontSize: 13 }}>
      Aún no hay conexiones. Instala la Companion de Gramps, crea una clave API de escritura y confirma una línea base antes de enviar datos.
    </div>}
  </section>;
}

function Conflict({ change, busy, onResolve }: { change: SyncChange; busy: boolean; onResolve: (resolution: "accept_remote" | "reject_remote") => void }) {
  return <div style={{ padding: "14px 17px", borderBottom: "1px solid var(--line2)" }}>
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <strong style={{ fontSize: 13.5 }}>{change.object_type} · {change.object_key}</strong>
      <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--danger)" }}>CONFLICTO · {change.source_system}</span>
      <span style={{ flex: 1 }} />
      <button disabled={busy} style={ghostBtn} onClick={() => onResolve("reject_remote")}>Conservar el árbol</button>
      <button disabled={busy} style={primaryBtn} onClick={() => onResolve("accept_remote")}>{busy ? "Guardando…" : "Aceptar remoto"}</button>
    </div>
    <pre style={{ margin: "9px 0 0", padding: 9, borderRadius: 7, background: "var(--bg)", overflowX: "auto", color: "var(--muted)", fontSize: 10.5 }}>{JSON.stringify(change.payload, null, 2)}</pre>
  </div>;
}
