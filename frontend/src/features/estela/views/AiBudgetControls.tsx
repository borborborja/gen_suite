import { useEffect, useState } from "react";
import { listAiCalls, reconcileAiCall, recoverAiCall, upsertBinding, type Binding } from "../../../api/providers";
import type { AiCall } from "../../../api/types";
import type { BillingTask } from "../../../api/types";
import BillingAuthorizationPanel from "./BillingAuthorizationPanel";

const field = { display: "flex", flexDirection: "column" as const, gap: 4, fontSize: 12 };
const input = { padding: 8, borderRadius: 6, background: "var(--bg)", color: "var(--fg)", border: "1px solid var(--line)" };

export function AiPolicyEditor({ binding, model, onChanged }: { binding: Binding; model: string; onChanged: () => Promise<void> }) {
  const previous = (binding.params?.budget_policy ?? {}) as Record<string, unknown>;
  const [values, setValues] = useState({
    model: String(previous.model ?? model),
    context_tokens: String(previous.context_tokens ?? ""),
    max_output_tokens: String(previous.max_output_tokens ?? (binding.task_type === "embedding" ? 0 : 4096)),
    input_usd_per_million: String(previous.input_usd_per_million ?? ""),
    output_usd_per_million: String(previous.output_usd_per_million ?? (binding.task_type === "embedding" ? 0 : "")),
    valid_until: String(previous.valid_until ?? "").slice(0, 10), source: String(previous.source ?? ""),
    output_parameter: String(previous.output_parameter ?? "max_completion_tokens"),
    response_format: String(previous.response_format ?? "json_schema"),
  });
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  function change(key: keyof typeof values, value: string) {
    setValues((old) => ({ ...old, [key]: value })); setConfirmed(false); setMessage("");
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!confirmed || busy) return;
    setBusy(true); setMessage("");
    try {
      await upsertBinding({ task_type: binding.task_type, credential_id: binding.credential_id, model: values.model,
        params: { ...binding.params, budget_policy: { ...values,
          context_tokens: Number(values.context_tokens), max_output_tokens: Number(values.max_output_tokens),
          valid_until: `${values.valid_until}T00:00:00Z`,
        } } });
      setConfirmed(false); setMessage("Perfil confirmado. Los trabajos ya iniciados conservan su perfil anterior.");
      await onChanged();
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  return <details style={{ marginTop: 10 }}>
    <summary>Tarifa y límites de IA {previous.model ? "configurados" : "pendientes de confirmar"}</summary>
    <p style={{ fontSize: 12, color: "var(--muted)" }}>Comprueba estos datos en el proveedor. Se reserva el coste de toda la ventana de contexto antes de cada llamada; la diferencia se libera al recibir consumo válido. Son límites de la estimación autorizada, no de la factura externa.</p>
    <form onSubmit={(event) => void save(event)}>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: 10 }}>
        <label style={field}>Modelo exacto<input required style={input} value={values.model} onChange={(e) => change("model", e.target.value)} /></label>
        <label style={field}>Ventana máxima del proveedor (tokens)<input required style={input} type="number" min="1" max="10000000" step="1" value={values.context_tokens} onChange={(e) => change("context_tokens", e.target.value)} /></label>
        <label style={field}>Salida máxima (0 para embeddings)<input required style={input} type="number" min="0" max="1000000" step="1" value={values.max_output_tokens} onChange={(e) => change("max_output_tokens", e.target.value)} /></label>
        <label style={field}>Techo entrada (USD / millón de tokens)<input required style={input} type="number" min="0.000001" max="10000" step="0.000001" value={values.input_usd_per_million} onChange={(e) => change("input_usd_per_million", e.target.value)} /></label>
        <label style={field}>Techo salida (USD / millón de tokens)<input required style={input} type="number" min="0" max="10000" step="0.000001" value={values.output_usd_per_million} onChange={(e) => change("output_usd_per_million", e.target.value)} /></label>
        <label style={field}>Revisar antes de (00:00 UTC)<input required style={input} type="date" value={values.valid_until} onChange={(e) => change("valid_until", e.target.value)} /></label>
        <label style={field}>Fuente de tarifa y contexto<input required minLength={8} maxLength={1000} style={input} value={values.source} onChange={(e) => change("source", e.target.value)} placeholder="Documentación del proveedor y fecha de revisión" /></label>
        <label style={field}>Parámetro de salida<select style={input} value={values.output_parameter} onChange={(e) => change("output_parameter", e.target.value)}><option value="max_completion_tokens">max_completion_tokens (OpenAI)</option><option value="max_tokens">max_tokens (otros compatibles)</option></select></label>
        <label style={field}>Formato de extracción<select style={input} value={values.response_format} onChange={(e) => change("response_format", e.target.value)}><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option></select></label>
      </fieldset>
      <label style={{ display: "block", fontSize: 12, margin: "12px 0" }}><input type="checkbox" checked={confirmed} disabled={busy} onChange={(e) => setConfirmed(e.target.checked)} /> He comprobado el modelo, su contexto y las tarifas máximas aplicables, incluidos posibles recargos.</label>
      <button disabled={busy || !confirmed} type="submit">{busy ? "Guardando…" : "Confirmar perfil de IA"}</button>
      <p role="status" style={{ fontSize: 12 }}>{message}</p>
    </form>
  </details>;
}

export function AiCallsPanel({ onChanged }: { onChanged: () => Promise<void> }) {
  const [renewal, setRenewal] = useState<{ job: string; task: BillingTask } | null>(null);
  const [calls, setCalls] = useState<AiCall[]>([]);
  const [state, setState] = useState("unknown");
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [evidence, setEvidence] = useState("");
  const [requestId, setRequestId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    listAiCalls(state).then((rows) => { if (active) { setCalls(rows); setMessage(""); } })
      .catch((error: Error) => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, [state]);
  async function refresh() {
    try { setCalls(await listAiCalls(state)); await onChanged(); }
    catch (error) { setMessage((error as Error).message); }
  }
  async function reconcile(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !confirmed || busy) return;
    const value = Number(amount);
    if (!amount.trim() || !Number.isFinite(value) || value < 0) { setMessage("Indica el importe verificado."); return; }
    setBusy(true);
    try {
      await reconcileAiCall(selected, { cost_microusd: Math.round(value * 1_000_000), evidence,
        provider_request_id: requestId || null });
      setSelected(null); setConfirmed(false); setMessage("Conciliación guardada. No se repite la llamada ni se inventa su resultado.");
      await refresh();
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  async function recover(id: string) {
    setBusy(true);
    try { await recoverAiCall(id); setMessage("Respuesta recuperada del archivo privado, sin otra llamada al proveedor."); await refresh(); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  return <details style={{ marginTop: 14 }}>
    <summary>Registro de llamadas y conciliación (administración)</summary>
    <p style={{ fontSize: 12 }}>Una llamada incierta retiene su reserva aunque cambie el mes. Comprueba el consumo con el proveedor antes de conciliarla. El silencio o un timeout no demuestran coste cero.</p>
    <label>Estado <select value={state} onChange={(e) => { setState(e.target.value); setSelected(null); }} disabled={busy}>
      <option value="unknown">Inciertas</option><option value="inflight">En ejecución / interrumpidas</option>
      <option value="succeeded">Con respuesta guardada</option><option value="reconciled">Conciliadas</option>
    </select></label> <button onClick={() => void refresh()} disabled={busy}>Actualizar</button>
    <p style={{ fontSize: 12 }}>Últimas 50 llamadas de este estado.</p>
    {calls.map((call) => <div key={call.id} style={{ borderBottom: "1px solid var(--line)", padding: "10px 0", fontSize: 12 }}>
      <div>{String(call.profile.model ?? "Modelo")} · {new Date(call.created_at).toLocaleString()} · ${(call.reserved_microusd / 1_000_000).toFixed(6)} reservados{call.cost_microusd != null ? ` · $${(call.cost_microusd / 1_000_000).toFixed(6)} liquidados` : ""}</div>
      <div style={{ overflowWrap: "anywhere" }}>{call.id}{call.provider_request_id ? ` · ${call.provider_request_id}` : ""} {call.error_code}</div>
      {call.job_id && ["transcription", "inference", "embedding"].includes(call.task_type) && <button disabled={busy} onClick={() => setRenewal({ job: call.job_id!, task: call.task_type as BillingTask })}>Revisar autorización del trabajo</button>}
      {(call.state === "unknown" || call.state === "inflight") && <button disabled={busy} onClick={() => {
        setSelected(call.id); setAmount(""); setEvidence(""); setRequestId(call.provider_request_id ?? ""); setConfirmed(false);
      }}>Conciliar con evidencia</button>}
      {(call.state === "unknown" || call.state === "inflight") && <button disabled={busy} onClick={() => void recover(call.id)}>Recuperar respuesta guardada</button>}
    </div>)}
    {renewal && <BillingAuthorizationPanel key={`${renewal.job}:${renewal.task}`} scope="job" targetId={renewal.job} task={renewal.task} onChanged={onChanged} />}
    {selected && <form onSubmit={(event) => void reconcile(event)} style={{ marginTop: 12 }}>
      <p style={{ fontSize: 12, overflowWrap: "anywhere" }}>Conciliar {selected}. Las llamadas en ejecución deben llevar al menos 15 minutos antes de poder conciliarse.</p>
      <label style={field}>Coste verificado (USD)<input required type="number" step="0.000001" min="0" max="100000" value={amount} onChange={(e) => { setAmount(e.target.value); setConfirmed(false); }} /></label>
      <label style={field}>Evidencia de facturación (sin claves)<textarea required minLength={10} maxLength={2000} value={evidence} onChange={(e) => { setEvidence(e.target.value); setConfirmed(false); }} /></label>
      <label style={field}>Identificador del proveedor<input maxLength={256} value={requestId} onChange={(e) => { setRequestId(e.target.value); setConfirmed(false); }} /></label>
      <label style={{ display: "block", fontSize: 12, margin: "10px 0" }}><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> He verificado el importe; cero solo si el proveedor confirma que no facturó esta llamada.</label>
      <button disabled={busy || !confirmed}>Guardar conciliación</button> <button type="button" disabled={busy} onClick={() => setSelected(null)}>Cancelar</button>
    </form>}
    <p role="status" style={{ fontSize: 12 }}>{message}</p>
  </details>;
}
