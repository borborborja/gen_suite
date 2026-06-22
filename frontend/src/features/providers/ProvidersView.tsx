import { useCallback, useEffect, useState } from "react";
import {
  type Binding,
  type CatalogEntry,
  type Credential,
  createCredential,
  deleteCredential,
  getCatalog,
  listBindings,
  listCredentials,
  upsertBinding,
} from "../../api/providers";

const TASKS = ["transcription", "embedding", "inference"];

export default function ProvidersView({ onError }: { onError: (e: string) => void }) {
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [creds, setCreds] = useState<Credential[]>([]);
  const [bindings, setBindings] = useState<Binding[]>([]);

  const [provider, setProvider] = useState("openai");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");

  const load = useCallback(async () => {
    try {
      const [c, cr, b] = await Promise.all([getCatalog(), listCredentials(), listBindings()]);
      setCatalog(c);
      setCreds(cr);
      setBindings(b);
    } catch (e) {
      onError((e as Error).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const entry = catalog.find((c) => c.key === provider);

  async function add() {
    try {
      await createCredential({
        scope: "tenant",
        provider_key: provider,
        label: label || (entry?.display_name ?? provider),
        api_key: apiKey || undefined,
      });
      setLabel("");
      setApiKey("");
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  }

  async function bind(task: string, credId: string) {
    if (!credId) return;
    try {
      await upsertBinding({ task_type: task, credential_id: credId });
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  }

  const bindingFor = (task: string) => bindings.find((b) => b.task_type === task)?.credential_id || "";

  return (
    <div className="card">
      <strong>Proveedores de IA</strong>
      <p className="muted" style={{ fontSize: ".8rem" }}>
        Clave/modelo por proveedor; asigna uno a cada tarea. Las claves se guardan cifradas.
      </p>

      <div className="row" style={{ flexWrap: "wrap", marginBottom: ".5rem" }}>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {catalog.map((c) => (
            <option key={c.key} value={c.key}>
              {c.display_name}
            </option>
          ))}
        </select>
        <input placeholder="Etiqueta" value={label} onChange={(e) => setLabel(e.target.value)} />
        {entry?.requires_key && (
          <input
            placeholder="API key"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        )}
        <button onClick={add}>Añadir</button>
      </div>

      <ul className="list">
        {creds.map((c) => (
          <li key={c.id}>
            <span>
              {c.label} <span className="badge">{c.provider_key}</span>{" "}
              <span className="muted">{c.key_masked || "sin clave"}</span>
            </span>
            <button className="secondary" onClick={() => deleteCredential(c.id).then(load).catch((e) => onError((e as Error).message))}>
              🗑
            </button>
          </li>
        ))}
        {creds.length === 0 && <li className="muted">Sin credenciales.</li>}
      </ul>

      <div style={{ marginTop: ".75rem" }}>
        <div className="muted" style={{ fontSize: ".75rem", textTransform: "uppercase" }}>
          Asignación por tarea
        </div>
        {TASKS.map((task) => (
          <div key={task} className="row" style={{ justifyContent: "space-between", padding: ".25rem 0" }}>
            <span>{task}</span>
            <select value={bindingFor(task)} onChange={(e) => bind(task, e.target.value)}>
              <option value="">—</option>
              {creds.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>
    </div>
  );
}
