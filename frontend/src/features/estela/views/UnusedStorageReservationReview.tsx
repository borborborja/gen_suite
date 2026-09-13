import { useEffect, useRef, useState } from "react";
import { cancelUnusedStorageReservation, previewUnusedStorageCancellation } from "../../../api/storageQuota";
import type { StorageCancellationPreview, StorageWrite, StorageWriteAction } from "../../../api/types";
import { field } from "../ui";

export default function UnusedStorageReservationReview({ write, onClose, onResolved }: {
  write: StorageWrite; onClose: () => void; onResolved: (action: StorageWriteAction) => void;
}) {
  const [reason, setReason] = useState(""); const [preview, setPreview] = useState<StorageCancellationPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false); const [lateLoss, setLateLoss] = useState(false);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const changing = useRef(false); const mounted = useRef(true); const identity = useRef<{ hash: string; id: string }>();
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function act(confirm: boolean) {
    if (changing.current || reason.trim().length < 10 || (confirm && (!preview || !confirmed || (preview.requires_late_response_loss_ack && !lateLoss)))) return;
    changing.current = true; setBusy(true); setMessage("");
    try {
      if (!confirm) {
        const next = await previewUnusedStorageCancellation(write.id, reason.trim());
        if (mounted.current) { setPreview(next); setConfirmed(false); setLateLoss(false); }
      } else if (preview) {
        if (identity.current?.hash !== preview.preview_hash) identity.current = { hash: preview.preview_hash, id: crypto.randomUUID() };
        const action = await cancelUnusedStorageReservation(write.id, { client_id: identity.current.id, preview_hash: preview.preview_hash,
          reason: preview.reason, authorize: true, acknowledge_late_response_loss: lateLoss });
        if (mounted.current) onResolved(action);
      }
    } catch (error) { if (mounted.current) setMessage((error as Error).message); }
    finally { changing.current = false; if (mounted.current) setBusy(false); }
  }
  return <section aria-label="Revisar cancelación de reserva" style={{ border: "1px solid var(--line)", padding: 12, marginTop: 12 }}>
    <h4>Cancelar una reserva no iniciada</h4>
    <p>Recibo {write.id}. Esta acción impide que este recibo inicie S3; no borra objetos ni cancela el trabajo completo. Reanudar el trabajo puede crear otra reserva.</p>
    <label>Motivo de cancelación <textarea style={field} value={reason} minLength={10} maxLength={2000} disabled={busy}
      onChange={(event) => { setReason(event.target.value); setPreview(null); setConfirmed(false); setLateLoss(false); }} /></label>
    <button disabled={busy || reason.trim().length < 10} onClick={() => void act(false)}>Revisar efectos de cancelación</button>
    {preview && <>
      <p>Se liberaría una reserva de {preview.reserved_bytes.toLocaleString("es-ES")} bytes. No se descuenta contenido existente ni se declara un borrado en S3.</p>
      {preview.ai_call_id && <>
        <p role="alert">La llamada {preview.ai_call_id} ya está conciliada. Su coste no cambia. Retirar la reserva puede impedir guardar una respuesta tardía; no se volverá a pagar esta misma llamada automáticamente.</p>
        <label><input type="checkbox" checked={lateLoss} disabled={busy} onChange={(event) => setLateLoss(event.target.checked)} /> Renuncio a guardar una respuesta tardía de esta llamada conciliada</label>
      </>}
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /> Confirmo cancelar únicamente esta reserva no iniciada y conservar su auditoría</label>
      <button disabled={busy || !confirmed || (preview.requires_late_response_loss_ack && !lateLoss)} onClick={() => void act(true)}>Confirmar cancelación de reserva</button>
    </>}
    <button disabled={busy} onClick={onClose}>Cerrar revisión sin cancelar</button>
    <p role="status">{message}</p>
  </section>;
}
