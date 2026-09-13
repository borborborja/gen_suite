import { useEffect, useRef, useState } from "react";
import { getStorageWriteActions } from "../../../api/storageQuota";
import type { StorageWriteAction } from "../../../api/types";

export default function StorageWriteActionHistory({ version }: { version: number }) {
  const [open, setOpen] = useState(false); const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<StorageWriteAction[]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => {
    if (!open) return;
    const revision = ++epoch.current; setBusy(true); setError("");
    void getStorageWriteActions(offset).then((next) => { if (revision === epoch.current) setRows(next); })
      .catch((issue: Error) => { if (revision === epoch.current) setError(issue.message); })
      .finally(() => { if (revision === epoch.current) setBusy(false); });
    return () => { epoch.current += 1; };
  }, [open, offset, version]);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Auditoría de reservas canceladas</summary>
    {rows.map((row) => <p key={row.id}>Recibo {row.write_id} · {row.reason} · reserva liberada: {row.released_bytes.toLocaleString("es-ES")} bytes · {new Date(row.created_at).toLocaleString("es-ES")} · usuario {row.created_by}</p>)}
    {!rows.length && !busy && <p>No hay cancelaciones en esta página.</p>}
    <button disabled={busy || offset === 0} onClick={() => setOffset(0)}>Inicio de auditoría</button>
    <button disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Auditoría anterior</button>
    <button disabled={busy || rows.length < 20} onClick={() => setOffset(offset + 20)}>Auditoría siguiente</button>
    {error && <p role="alert">{error}</p>}
  </details>;
}
