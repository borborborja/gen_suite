import { useEffect, useRef, useState } from "react";
import { approveBilling, billingHistory, previewBilling } from "../../../api/authorizations";
import type { BillingHistory, BillingPreview, BillingScope, BillingTask } from "../../../api/types";
import { field } from "../ui";

const TASK = { transcription: "transcripción", inference: "interpretación", embedding: "búsqueda semántica" };
const SCOPE = { research_run: "investigación", embedding_generation: "generación del índice", job: "trabajo" };
const dollars = (value: number) => `$${(value / 1_000_000).toFixed(6)}`;
type Props = { scope: BillingScope; targetId: string; task: BillingTask; onChanged?: () => Promise<void> };

export default function BillingAuthorizationPanel(props: Props) {
  const [opened, setOpened] = useState(false);
  return <details style={{ marginTop: 12 }} onToggle={(event) => setOpened(event.currentTarget.open)}>
    <summary>Renovar autorización económica de {TASK[props.task]} (administración)</summary>
    {opened && <Editor key={`${props.scope}:${props.targetId}:${props.task}`} {...props} />}
  </details>;
}

function Editor({ scope, targetId, task, onChanged }: Props) {
  const [history, setHistory] = useState<BillingHistory | null>(null);
  const [values, setValues] = useState({ input_usd_per_million: "", output_usd_per_million: "", valid_until: "", source: "" });
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<BillingPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const mounted = useRef(true);
  const requestKey = useRef<{ signature: string; id: string }>();
  useEffect(() => {
    mounted.current = true;
    billingHistory(scope, targetId, task).then((value) => {
      if (!mounted.current) return;
      setHistory(value); setValues({ input_usd_per_million: value.current_policy.input_usd_per_million,
        output_usd_per_million: value.current_policy.output_usd_per_million, valid_until: "", source: value.current_policy.source });
    }).catch((error: Error) => { if (mounted.current) setMessage(error.message); });
    return () => { mounted.current = false; };
  }, [scope, targetId, task]);
  function invalidate() { setPreview(null); setConfirmed(false); setMessage(""); }
  const proposal = () => ({ scope, target_id: targetId, task_type: task, ...values, valid_until: `${values.valid_until}T00:00:00Z` });
  async function compare(event: React.FormEvent) {
    event.preventDefault(); if (busy) return;
    setBusy(true); invalidate();
    try {
      const value = await previewBilling(proposal());
      if (mounted.current) setPreview(value);
    } catch (error) { if (mounted.current) setMessage((error as Error).message); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function approve() {
    if (!preview || !confirmed || busy || !reason.trim()) return;
    const body = { ...proposal(), preview_hash: preview.preview_hash, authorize: true as const, reason };
    const signature = JSON.stringify(body);
    if (requestKey.current?.signature !== signature) requestKey.current = { signature, id: crypto.randomUUID() };
    setBusy(true); setMessage("");
    try {
      await approveBilling({ ...body, client_id: requestKey.current.id });
      if (!mounted.current) return;
      setPreview(null); setConfirmed(false);
      setMessage("Autorización guardada. No se ha iniciado ni reanudado ningún trabajo; las llamadas anteriores conservan su coste.");
      const next = await billingHistory(scope, targetId, task);
      if (!mounted.current) return;
      setHistory(next);
      await onChanged?.();
    } catch (error) {
      if (mounted.current) {
        setMessage((error as Error).message); setConfirmed(false);
        // Keep the exact idempotency key for a transport failure; server rejections
        // require a fresh comparison (including concurrent authorization changes).
        if (!(error instanceof TypeError)) setPreview(null);
      }
    } finally { if (mounted.current) setBusy(false); }
  }
  return <div style={{ fontSize: 13 }}>
    <p>Solo renueva tarifas y caducidad. Conserva modelo, credencial, límites, formatos y huellas de peticiones. No amplía presupuestos ni libera reservas inciertas. Para cambiar modelo o límites, prepara otra ejecución.</p>
    {history && <>
      <p>Se aplica a toda la {SCOPE[history.owner.scope]}: {history.owner.title} ({history.owner.id}). Autorización vigente: versión {history.authorization_version} · {history.expired ? "CADUCADA" : "en plazo"} · hasta {history.current_policy.valid_until}.</p>
      <p>Perfil fijo: {history.execution_profile.engine} / {history.current_policy.model} · contexto {history.current_policy.context_tokens} · salida {history.current_policy.max_output_tokens}. Si la ejecución avanza mientras revisas, puede ser necesario pausar y volver a comparar.</p>
      <form onSubmit={(event) => void compare(event)}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, display: "grid", gap: 8 }}>
          <label>Nueva tarifa máxima de entrada (USD / millón)<input style={field} required type="number" min="0.000001" max="10000" step="0.000001" value={values.input_usd_per_million} onChange={(e) => { setValues({ ...values, input_usd_per_million: e.target.value }); invalidate(); }} /></label>
          <label>Nueva tarifa máxima de salida (USD / millón)<input style={field} required type="number" min={history.current_policy.max_output_tokens ? "0.000001" : "0"} max="10000" step="0.000001" value={values.output_usd_per_million} onChange={(e) => { setValues({ ...values, output_usd_per_million: e.target.value }); invalidate(); }} /></label>
          <label>Nueva revisión antes de (00:00 UTC)<input style={field} required type="date" value={values.valid_until} onChange={(e) => { setValues({ ...values, valid_until: e.target.value }); invalidate(); }} /></label>
          <label>Fuente verificada de la nueva tarifa<input style={field} required minLength={8} maxLength={1000} value={values.source} onChange={(e) => { setValues({ ...values, source: e.target.value }); invalidate(); }} /></label>
          <label>Motivo de la renovación<textarea style={field} required maxLength={2000} value={reason} onChange={(e) => { setReason(e.target.value); invalidate(); }} /></label>
          <button disabled={!reason.trim()}>Comparar antes de autorizar</button>
        </fieldset>
      </form>
      {preview && <div>
        <p>Confirma el cambio para toda la {SCOPE[preview.owner.scope]} «{preview.owner.title}», versión económica {preview.authorization_version} → {preview.authorization_version + 1}.</p>
        <table><thead><tr><th>Concepto</th><th>Anterior</th><th>Propuesto</th></tr></thead><tbody>
          <tr><td>Entrada USD / millón</td><td>{preview.before.input_usd_per_million}</td><td>{preview.after.input_usd_per_million}</td></tr>
          <tr><td>Salida USD / millón</td><td>{preview.before.output_usd_per_million}</td><td>{preview.after.output_usd_per_million}</td></tr>
          <tr><td>Caducidad UTC</td><td>{preview.before.valid_until}</td><td>{preview.after.valid_until}</td></tr>
          <tr><td>Reserva máxima por llamada</td><td>{dollars(preview.before_reservation_microusd)}</td><td>{dollars(preview.after_reservation_microusd)}</td></tr>
        </tbody></table>
        <label style={{ display: "block", margin: "12px 0" }}><input type="checkbox" disabled={busy} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> He verificado las nuevas tarifas y autorizo esta renovación para las próximas llamadas.</label>
        <button disabled={busy || !confirmed} onClick={() => void approve()}>Autorizar renovación sin reanudar</button>
      </div>}
      <details><summary>Historial económico (últimas {history.history.length} autorizaciones)</summary>
        {history.history.map((row) => <p key={row.id}>v{row.version} · {new Date(row.created_at).toLocaleString()} · {row.reason} · responsable {row.created_by}</p>)}
        {history.history.length >= 20 && <p>La API permite paginar el historial completo.</p>}
      </details>
    </>}
    <p role="status">{message}</p>
  </div>;
}
