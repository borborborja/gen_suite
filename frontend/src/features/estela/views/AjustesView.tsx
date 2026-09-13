import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { useEstela } from "../store";
import { fonts } from "../theme";
import { api } from "../../../api/client";
import { getRasterSettings, setRasterSettings, type RasterSettings } from "../../../api/documents";
import {
  getCatalog, listBindings, listCredentials, createCredential, deleteCredential,
  upsertBinding, getSpend, setBudget,
  type CatalogEntry, type Credential, type Binding,
} from "../../../api/providers";
import { beginMfa, changePassword, confirmMfa, disableMfa, getMe, updateMe, type MeOut } from "../../../api/account";
import { setTokens } from "../../../api/client";
import { listApiKeys, createApiKey, revokeApiKey, type ApiKey } from "../../../api/apiKeys";
import { addWorkspaceMember, listWorkspaceMembers, removeWorkspaceMember, updateWorkspaceMember, type WorkspaceMember } from "../../../api/tenancy";
import { useConfirm } from "../ui";

const IndexGenerationsPanel = lazy(() => import("./IndexGenerationsPanel"));
const StorageInventoryPanel = lazy(() => import("./StorageInventoryPanel"));
const StorageQuotaPanel = lazy(() => import("./StorageQuotaPanel"));
const AiPolicyEditor = lazy(() => import("./AiBudgetControls").then((m) => ({ default: m.AiPolicyEditor })));
const AiCallsPanel = lazy(() => import("./AiBudgetControls").then((m) => ({ default: m.AiCallsPanel })));

const TASK_LABEL: Record<string, string> = {
  transcription: "Lectura del manuscrito (HTR / visión)",
  inference: "Extracción de actas (texto → estructura)",
  embedding: "Embeddings (búsqueda y recuperación)",
};

export default function AjustesView() {
  const e = useEstela();
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [creds, setCreds] = useState<Credential[]>([]);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, cr, b] = await Promise.all([getCatalog(), listCredentials(), listBindings()]);
      setCatalog(c); setCreds(cr); setBindings(b);
    } catch { /* no backend */ }
    setLoaded(true);
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Task → catalog capabilities that can serve it (mirrors backend TASK_CAPABILITY, plus the
  // local OCR/HTR engines which are valid transcription providers without a key).
  const TASK_CAPS: Record<string, string[]> = {
    transcription: ["vision", "ocr_local"], inference: ["text"], embedding: ["embedding"],
  };
  const capsByKey = Object.fromEntries(catalog.map((c) => [c.key, c.capabilities]));
  const credsFor = (task: string) =>
    creds.filter((c) => (capsByKey[c.provider_key] || []).some((cap) => (TASK_CAPS[task] ?? [task]).includes(cap)));
  const bindingFor = (task: string) => bindings.find((b) => b.task_type === task);

  async function pick(task: string, credId: string, model: string | null) {
    await upsertBinding({ task_type: task, credential_id: credId, model: model || undefined });
    await load();
  }


  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 860 }}>
      <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Ajustes</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 28px" }}>Tu cuenta y qué modelo de IA usa Estela para cada tarea.</p>

      <AccountSection />

      <TeamSection />

      <Suspense fallback={<p>Cargando almacenamiento…</p>}><StorageInventoryPanel /></Suspense>
      <Suspense fallback={<p>Cargando cuota…</p>}><StorageQuotaPanel /></Suspense>

      <SpendSection />

      <Suspense fallback={<p>Cargando índices…</p>}><IndexGenerationsPanel /></Suspense>

      <RasterSection />

      <ApiKeysSection />


      {!loaded && <p style={{ color: "var(--muted)" }}>Cargando proveedores…</p>}

      {loaded && creds.length === 0 && (
        <div style={{ background: "var(--warn-faint)", border: "1px solid var(--warn)", borderRadius: 12, padding: 16, color: "var(--warn)", fontSize: 13.5, marginBottom: 14 }}>
          No hay proveedores configurados todavía. Añade una credencial (OpenRouter, OpenAI, Ollama, Jina…) para activar la lectura y extracción.
        </div>
      )}

      {loaded && <CredentialsSection catalog={catalog} creds={creds} onChanged={load} />}

      {/* per-task model selectors */}
      {loaded && creds.length > 0 && ["transcription", "inference", "embedding"].map((task) => {
        const options = credsFor(task);
        const active = bindingFor(task);
        return (
          <div key={task} style={{ marginBottom: 18 }}>
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 4px" }}>{TASK_LABEL[task]}</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
              {options.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13 }}>Ningún proveedor con esta capacidad.</div>}
              {options.map((c) => {
                const on = active?.credential_id === c.id;
                return (
                  <div key={c.id} onClick={() => !on && pick(task, c.id, c.model_default)}
                    style={{ display: "flex", alignItems: "center", gap: 14, background: "var(--surface)", border: `1px solid ${on ? "var(--accent)" : "var(--line)"}`, borderRadius: 11, padding: "13px 16px", cursor: on ? "default" : "pointer" }}>
                    <span style={{ width: 18, height: 18, borderRadius: "50%", flex: "none", border: `2px solid ${on ? "var(--accent)" : "var(--muted)"}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                      {on && <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)" }} />}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 14 }}>{c.label} <span style={{ color: "var(--muted)", fontWeight: 400, fontFamily: fonts.mono, fontSize: 12 }}>{c.provider_key}</span></div>
                      <div style={{ fontFamily: fonts.mono, fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>{(on && active?.model) || c.model_default}</div>
                    </div>
                    {on && <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--accent)", letterSpacing: ".08em" }}>ACTIVO</span>}
                  </div>
                );
              })}
            </div>

            {active && !["tesseract", "kraken", "ollama"].includes(creds.find((c) => c.id === active.credential_id)?.provider_key ?? "") &&
              <Suspense fallback={<p>Cargando controles de IA…</p>}><AiPolicyEditor key={`${active.id}:${active.credential_id}`} binding={active}
                model={active.model ?? creds.find((c) => c.id === active.credential_id)?.model_default ?? ""} onChanged={load} /></Suspense>}


          </div>
        );
      })}

      <h3 style={{ fontSize: 15, fontWeight: 600, margin: "28px 0 10px" }}>Apariencia</h3>
      <div style={{ display: "flex", alignItems: "center", gap: 16, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: "14px 18px" }}>
        <div style={{ flex: 1 }}><div style={{ fontWeight: 600, fontSize: 14 }}>Tema</div><div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 3 }}>Claro u oscuro.</div></div>
        <button onClick={e.toggleTheme} style={{ background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, padding: "10px 16px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer" }}>
          {e.theme === "dark" ? "Cambiar a claro" : "Cambiar a oscuro"}
        </button>
      </div>

      <DangerZone />
    </section>
  );
}

function TeamSection() {
  const e = useEstela();
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [myRole, setMyRole] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("contributor");
  const [busy, setBusy] = useState(false);
  const canManage = myRole === "owner" || myRole === "admin";
  const roles = myRole === "owner"
    ? ["owner", "admin", "editor", "contributor", "viewer", "guest"]
    : ["editor", "contributor", "viewer", "guest"];
  const load = useCallback(() => {
    void Promise.all([listWorkspaceMembers(), getMe()]).then(([next, me]) => {
      setMembers(next); setMyRole(me.active_role || "");
    }).catch(() => setMembers([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function invite() {
    if (!email.trim()) return;
    setBusy(true);
    try {
      await addWorkspaceMember({ email: email.trim(), role });
      setEmail(""); e.notify("Persona añadida al espacio", "var(--ok)"); load();
    } catch (error) { e.notify((error as Error).message, "var(--danger)"); }
    finally { setBusy(false); }
  }
  async function change(member: WorkspaceMember, next: string) {
    try { await updateWorkspaceMember(member.user_id, next); e.notify("Rol actualizado", "var(--ok)"); load(); }
    catch (error) { e.notify((error as Error).message, "var(--danger)"); }
  }
  async function remove(member: WorkspaceMember) {
    try { await removeWorkspaceMember(member.user_id); e.notify("Persona retirada del espacio", "var(--ok)"); load(); }
    catch (error) { e.notify((error as Error).message, "var(--danger)"); }
  }
  const fld: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13, boxSizing: "border-box" };

  return <section style={{ marginBottom: 28 }}>
    <h3 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px" }}>Equipo y permisos</h3>
    <div style={{ border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden" }}>
      {members.map((member, index) => <div key={member.user_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", background: "var(--surface)", borderBottom: index < members.length - 1 || canManage ? "1px solid var(--line2)" : "none", flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 190 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>{member.full_name || member.email}</div>
          {member.full_name && <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>{member.email}</div>}
        </div>
        {canManage && (myRole === "owner" || (member.role !== "owner" && member.role !== "admin"))
          ? <select value={member.role} onChange={(event) => void change(member, event.target.value)} style={fld}>{roles.map((item) => <option key={item} value={item}>{item}</option>)}</select>
          : <span style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)" }}>{member.role.toUpperCase()}</span>}
        {canManage && (myRole === "owner" || (member.role !== "owner" && member.role !== "admin")) && <button onClick={() => void remove(member)} style={{ background: "transparent", color: "var(--danger)", border: "1px solid var(--line)", borderRadius: 7, padding: "6px 9px", cursor: "pointer", font: "inherit", fontSize: 12 }}>Retirar</button>}
      </div>)}
      {members.length === 0 && <div style={{ padding: 14, color: "var(--muted)", fontSize: 12.5 }}>No se pudieron cargar los miembros del espacio.</div>}
      {canManage && <form onSubmit={(event) => { event.preventDefault(); void invite(); }} style={{ display: "flex", gap: 8, padding: 12, background: "var(--bg)", flexWrap: "wrap" }}>
        <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="correo@ejemplo.com" style={{ ...fld, flex: 1, minWidth: 180 }} />
        <select value={role} onChange={(event) => setRole(event.target.value)} style={fld}>{roles.map((item) => <option key={item} value={item}>{item}</option>)}</select>
        <button disabled={busy} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "9px 14px", font: "inherit", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>{busy ? "Añadiendo…" : "Añadir"}</button>
      </form>}
    </div>
    <p style={{ color: "var(--muted)", fontSize: 11.5, margin: "7px 0 0" }}>Las personas colaboradoras presentan propuestas; editoras, administradoras y propietarias pueden aplicar cambios según su nivel. Nunca puede quedar un espacio sin una persona propietaria.</p>
  </section>;
}

// Add/list/delete AI provider credentials — the entry point the bindings below depend on.
function CredentialsSection({ catalog, creds, onChanged }: {
  catalog: CatalogEntry[]; creds: Credential[]; onChanged: () => Promise<void> | void;
}) {
  const e = useEstela();
  const { confirmDialog, ask } = useConfirm();
  const [open, setOpen] = useState(creds.length === 0);
  const [provider, setProvider] = useState("");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const sel = catalog.find((c) => c.key === provider);

  async function add() {
    if (!provider) { setMsg("Elige un proveedor"); return; }
    if (sel?.requires_key && !apiKey.trim()) { setMsg("Este proveedor necesita una clave API"); return; }
    setBusy(true); setMsg(null);
    try {
      await createCredential({
        scope: "tenant", provider_key: provider,
        label: label.trim() || sel?.display_name || provider,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
        model_default: model.trim() || undefined,
      });
      setProvider(""); setLabel(""); setApiKey(""); setBaseUrl(""); setModel("");
      await onChanged();
      e.notify("Proveedor añadido", "var(--ok)");
    } catch (err) { setMsg((err as Error).message); }
    setBusy(false);
  }

  async function remove(c: Credential) {
    const ok = await ask({
      title: `¿Eliminar «${c.label}»?`,
      body: "Las tareas que lo tengan asignado dejarán de funcionar hasta que elijas otro proveedor.",
      danger: true, confirmLabel: "Eliminar",
    });
    if (!ok) return;
    try { await deleteCredential(c.id); await onChanged(); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
  }

  const fld: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5, boxSizing: "border-box", width: "100%" };
  const lbl: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" };

  return (
    <div style={{ marginBottom: 22 }}>
      {confirmDialog}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", margin: "0 0 10px" }}>
        <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Proveedores de IA</h3>
        <button onClick={() => setOpen((v) => !v)} style={{ background: "transparent", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "7px 13px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>
          {open ? "Cerrar" : "+ Añadir proveedor"}
        </button>
      </div>

      {creds.length > 0 && (
        <div style={{ border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden", marginBottom: 12 }}>
          {creds.map((c, i) => (
            <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 14px", background: "var(--surface)", borderBottom: i < creds.length - 1 ? "1px solid var(--line2)" : "none" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>{c.label} <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>{c.provider_key}</span></div>
                <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginTop: 2 }}>
                  {c.key_masked ? `clave ${c.key_masked}` : "sin clave"}{c.model_default ? ` · ${c.model_default}` : ""}{c.base_url ? ` · ${c.base_url}` : ""}
                </div>
              </div>
              <button onClick={() => remove(c)} style={{ flex: "none", background: "transparent", color: "var(--danger)", border: "1px solid var(--danger)", borderRadius: 8, padding: "6px 12px", fontFamily: "inherit", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>Eliminar</button>
            </div>
          ))}
        </div>
      )}

      {open && (
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 18 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <label style={lbl}>Proveedor
              <select style={fld} value={provider} onChange={(ev) => { setProvider(ev.target.value); const c = catalog.find((x) => x.key === ev.target.value); setBaseUrl(""); setModel(c?.default_model ?? ""); }}>
                <option value="">(elige)</option>
                {catalog.map((c) => <option key={c.key} value={c.key}>{c.display_name}{c.requires_key ? "" : " (sin clave)"}</option>)}
              </select>
            </label>
            <label style={lbl}>Nombre para mostrar
              <input style={fld} value={label} onChange={(ev) => setLabel(ev.target.value)} placeholder={sel?.display_name ?? "Mi proveedor"} />
            </label>
            <label style={lbl}>Clave API {sel && !sel.requires_key ? "(no necesaria)" : ""}
              <input style={fld} type="password" value={apiKey} onChange={(ev) => setApiKey(ev.target.value)} placeholder={sel?.requires_key ? "sk-…" : ""} disabled={!!sel && !sel.requires_key} />
            </label>
            <label style={lbl}>Modelo por defecto
              <input style={fld} value={model} onChange={(ev) => setModel(ev.target.value)} placeholder={sel?.default_model ?? ""} />
            </label>
            <label style={{ ...lbl, gridColumn: "1 / -1" }}>URL base (solo si no es la estándar)
              <input style={fld} value={baseUrl} onChange={(ev) => setBaseUrl(ev.target.value)} placeholder={sel?.default_base_url ?? ""} />
            </label>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 14 }}>
            <button onClick={add} disabled={busy} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "10px 18px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer", opacity: busy ? 0.6 : 1 }}>
              {busy ? "Guardando…" : "Guardar proveedor"}
            </button>
            {msg && <span style={{ fontSize: 12.5, color: "var(--danger)" }}>{msg}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

function AccountSection() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [pmsg, setPmsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [mfaUrl, setMfaUrl] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [mfaPassword, setMfaPassword] = useState("");
  const [mfaMessage, setMfaMessage] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [me, setMe] = useState<MeOut | null>(null);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    getMe().then((m) => { setMe(m); setName(m.user.full_name ?? ""); setEmail(m.user.email); setLoaded(true); })
      .catch(() => setLoaded(true));
  }, []);

  async function switchTenant(tid: string) {
    setSwitching(true);
    try {
      const t = await api<{ access_token: string }>(`/auth/session/switch/${tid}`, { method: "POST" });
      setTokens(t.access_token);
      window.location.reload(); // whole app state is per-tenant — a clean reload is the honest reset
    } catch (err) {
      setSwitching(false);
      setMsg({ kind: "err", text: (err as Error).message });
    }
  }

  async function saveProfile() {
    setMsg(null);
    try { await updateMe({ full_name: name, email }); setMsg({ kind: "ok", text: "Perfil guardado" }); }
    catch (err) { setMsg({ kind: "err", text: (err as Error).message }); }
  }
  async function savePassword() {
    setPmsg(null);
    if (nw.length < 8) { setPmsg({ kind: "err", text: "La nueva contraseña debe tener 8+ caracteres" }); return; }
    try { await changePassword(cur, nw); setCur(""); setNw(""); setPmsg({ kind: "ok", text: "Contraseña cambiada" }); }
    catch (err) { setPmsg({ kind: "err", text: (err as Error).message }); }
  }
  async function startMfa() {
    try { const next = await beginMfa(); setMfaUrl(next.otpauth_url); setMfaMessage("Añade la clave en tu app y confirma el código."); }
    catch (err) { setMfaMessage((err as Error).message); }
  }
  async function finishMfa() {
    try { const next = await confirmMfa(mfaCode); setRecoveryCodes(next.recovery_codes); setMfaUrl(null); setMfaCode(""); setMfaMessage("MFA activado. Guarda los códigos de recuperación fuera de la aplicación."); }
    catch (err) { setMfaMessage((err as Error).message); }
  }
  async function removeMfa() {
    try { await disableMfa(mfaPassword, mfaCode); setMfaPassword(""); setMfaCode(""); setRecoveryCodes([]); setMfaMessage("MFA desactivado."); }
    catch (err) { setMfaMessage((err as Error).message); }
  }

  const fld: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5, width: "100%", boxSizing: "border-box" };
  const lbl: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" };
  const btn: React.CSSProperties = { background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "10px 18px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer", alignSelf: "flex-start" };

  return (
    <>
      <h3 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px" }}>Cuenta</h3>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 28 }}>
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 18, display: "flex", flexDirection: "column", gap: 11 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>Perfil</div>
          <label style={lbl}>Nombre<input style={fld} value={name} onChange={(e) => setName(e.target.value)} disabled={!loaded} /></label>
          <label style={lbl}>Email<input style={fld} type="email" value={email} onChange={(e) => setEmail(e.target.value)} disabled={!loaded} /></label>
          <button onClick={saveProfile} style={btn}>Guardar perfil</button>
          {msg && <div style={{ fontSize: 12.5, color: msg.kind === "ok" ? "var(--ok)" : "var(--danger)" }}>{msg.text}</div>}
        </div>
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 18, display: "flex", flexDirection: "column", gap: 11 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>Contraseña</div>
          <label style={lbl}>Contraseña actual<input style={fld} type="password" value={cur} onChange={(e) => setCur(e.target.value)} /></label>
          <label style={lbl}>Nueva contraseña<input style={fld} type="password" value={nw} onChange={(e) => setNw(e.target.value)} /></label>
          <button onClick={savePassword} style={btn}>Cambiar contraseña</button>
          {pmsg && <div style={{ fontSize: 12.5, color: pmsg.kind === "ok" ? "var(--ok)" : "var(--danger)" }}>{pmsg.text}</div>}
        </div>
        <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 18, display: "flex", flexDirection: "column", gap: 11 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>Autenticación en dos pasos</div>
          <div style={{ fontSize: 12.5, color: "var(--muted)" }}>Opcional: usa una app compatible con TOTP y guarda los códigos de recuperación.</div>
          {!mfaUrl && recoveryCodes.length === 0 && <button onClick={startMfa} style={btn}>Configurar MFA</button>}
          {mfaUrl && <>
            <code style={{ fontSize: 10.5, overflowWrap: "anywhere", color: "var(--muted)" }}>{mfaUrl}</code>
            <label style={lbl}>Código de 6 dígitos<input style={fld} inputMode="numeric" value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} /></label>
            <button onClick={finishMfa} disabled={mfaCode.length < 6} style={btn}>Confirmar MFA</button>
          </>}
          {recoveryCodes.length > 0 && <code style={{ fontSize: 11, overflowWrap: "anywhere" }}>{recoveryCodes.join(" · ")}</code>}
          {!mfaUrl && <>
            <label style={lbl}>Contraseña para desactivar<input style={fld} type="password" value={mfaPassword} onChange={(event) => setMfaPassword(event.target.value)} /></label>
            <label style={lbl}>Código MFA<input style={fld} inputMode="numeric" value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} /></label>
            <button onClick={removeMfa} disabled={!mfaPassword || mfaCode.length < 6} style={{ ...btn, background: "var(--danger)" }}>Desactivar MFA</button>
          </>}
          {mfaMessage && <div style={{ fontSize: 12.5, color: "var(--muted)" }}>{mfaMessage}</div>}
        </div>
      </div>

      {me && me.memberships.length > 1 && (
        <>
          <h3 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px" }}>Espacios de investigación</h3>
          <div style={{ border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden", marginBottom: 28 }}>
            {me.memberships.map((m, i) => {
              const active = m.tenant_id === me.active_tenant_id;
              return (
                <div key={m.tenant_id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", background: "var(--surface)", borderBottom: i < me.memberships.length - 1 ? "1px solid var(--line2)" : "none" }}>
                  <div style={{ flex: 1, fontSize: 13.5, fontWeight: active ? 600 : 500 }}>
                    {m.tenant_name} <span style={{ color: "var(--muted)", fontSize: 12, fontWeight: 400 }}>· {m.role}</span>
                  </div>
                  {active
                    ? <span style={{ fontSize: 12, color: "var(--ok)", fontWeight: 600 }}>✓ activo</span>
                    : <button disabled={switching} onClick={() => switchTenant(m.tenant_id)} style={{ background: "transparent", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 13px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer", opacity: switching ? 0.5 : 1 }}>Cambiar</button>}
                </div>
              );
            })}
          </div>
        </>
      )}

      <h3 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px" }}>Modelos de IA</h3>
    </>
  );
}

function SpendSection() {
  const [month, setMonth] = useState(0);
  const [reserved, setReserved] = useState(0);
  const [unknown, setUnknown] = useState(0);
  const [budget, setBudgetState] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    const s = await getSpend();
    setMonth(s.settled_microusd / 10000); setReserved(s.reserved_microusd / 10000); setUnknown(s.unknown_calls);
    setBudgetState(s.budget_cents);
  }, []);
  useEffect(() => { getSpend().then((s) => {
    setMonth(s.settled_microusd / 10000); setReserved(s.reserved_microusd / 10000); setUnknown(s.unknown_calls);
    setBudgetState(s.budget_cents); setDraft(s.budget_cents != null ? (s.budget_cents / 100).toString() : "");
  }).catch((error: Error) => setMsg(error.message)); }, []);
  const pct = budget ? Math.min(100, Math.round(((month + reserved) / budget) * 100)) : 0;
  async function save() {
    try {
      const cents = draft.trim() === "" ? null : Math.round(parseFloat(draft) * 100);
      if (cents !== null && (!Number.isFinite(cents) || cents < 0)) throw new Error("Introduce un importe válido.");
      const s = await setBudget(cents); setBudgetState(s.budget_cents); await refresh(); setMsg("Guardado");
      setTimeout(() => setMsg(null), 2000);
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <>
      <h3 style={{ fontSize: 15, fontWeight: 600, margin: "28px 0 10px" }}>Gasto de IA</h3>
      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 18, marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontFamily: fonts.serif, fontSize: 26, fontWeight: 700 }}>${(month / 100).toFixed(2)}</span>
          <span style={{ color: "var(--muted)", fontSize: 13 }}>consumo estimado liquidado este mes{budget != null ? ` · de un tope de $${(budget / 100).toFixed(2)}` : " · IA de pago desactivada sin presupuesto"}</span>
        </div>
        <p style={{ fontSize: 12 }}>${(reserved / 100).toFixed(6)} reservados en llamadas pendientes.</p>
        {unknown > 0 && <p role="alert" style={{ color: "var(--warn)", fontSize: 12 }}>{unknown} llamadas inciertas o interrumpidas. Las nuevas llamadas de pago están detenidas hasta conciliarlas.</p>}
        {budget != null && (
          <div style={{ height: 8, borderRadius: 999, background: "var(--line2)", overflow: "hidden", marginTop: 10 }}>
            <div style={{ height: "100%", width: `${pct}%`, background: pct >= 100 ? "var(--danger)" : pct >= 80 ? "var(--warn)" : "linear-gradient(90deg,var(--accent),var(--gold))" }} />
          </div>
        )}
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end", marginTop: 14, flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Tope mensual (USD, vacío = desactivar IA de pago)
            <input style={{ background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5, width: 140 }} value={draft} onChange={(e) => setDraft(e.target.value.replace(/[^\d.]/g, ""))} placeholder="p. ej. 20" />
          </label>
          <button onClick={save} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer", height: 38 }}>Guardar tope</button>
          {msg && <span style={{ fontSize: 12.5, color: "var(--ok)" }}>{msg}</span>}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 8 }}>Cada llamada reserva su máximo estimado antes de salir. Las tarifas y ventanas deben confirmarse por modelo; no equivalen a la factura del proveedor. Las reservas inciertas no caducan al cambiar el mes.</div>
        <Suspense fallback={<p>Cargando registro…</p>}><AiCallsPanel onChanged={refresh} /></Suspense>
      </div>
    </>
  );
}

function RasterSection() {
  const [s, setS] = useState<RasterSettings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { getRasterSettings().then(setS).catch(() => {}); }, []);
  async function save(next: RasterSettings) {
    setS(next);
    try { const r = await setRasterSettings(next); setS(r); setMsg("Guardado · afecta a las próximas subidas y re-procesos"); setTimeout(() => setMsg(null), 2500); }
    catch (e) { setMsg((e as Error).message); }
  }
  if (!s) return null;
  const DPIS = [150, 200, 300, 400, 600];
  const note: Record<number, string> = { 150: "baja (rápida/barata)", 200: "media", 300: "alta (recomendada)", 400: "muy alta", 600: "máxima (lenta/cara)" };
  return (
    <>
      <h3 style={{ fontSize: 15, fontWeight: 600, margin: "28px 0 10px" }}>Calidad de escaneo</h3>
      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 18, marginBottom: 14 }}>
        <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 14 }}>Resolución a la que se renderizan los PDF para que la IA los lea. <b style={{ color: "var(--fg)" }}>Es el factor que más afecta a la calidad</b>: con poca resolución la letra antigua es ilegible y el modelo inventa. Más DPI = mejor lectura pero más coste por página.</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
          {DPIS.map((d) => (
            <button key={d} onClick={() => save({ ...s, dpi: d })}
              style={{ padding: "8px 14px", borderRadius: 9, cursor: "pointer", fontFamily: "inherit", fontSize: 13,
                border: s.dpi === d ? "1.5px solid var(--accent)" : "1px solid var(--line)",
                background: s.dpi === d ? "var(--accent-faint, rgba(187,125,26,.1))" : "var(--bg)",
                color: s.dpi === d ? "var(--accent)" : "var(--fg)", fontWeight: s.dpi === d ? 600 : 400 }}>
              {d} DPI<span style={{ display: "block", fontSize: 10.5, color: "var(--muted)", fontWeight: 400 }}>{note[d]}</span>
            </button>
          ))}
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13.5, cursor: "pointer", marginBottom: 8 }}>
          <input type="checkbox" checked={s.autosplit} onChange={(e) => save({ ...s, autosplit: e.target.checked })} />
          Partir automáticamente los pliegos de dos páginas (escaneos de libro abierto) en dos caras
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13.5, cursor: "pointer" }}>
          <input type="checkbox" checked={s.format === "webp"} onChange={(e) => save({ ...s, format: e.target.checked ? "webp" : "jpeg" })} />
          Guardar en WebP (≈30% menos espacio a igual calidad)
        </label>
        {msg && <div style={{ fontSize: 12.5, color: "var(--ok)", marginTop: 10 }}>{msg}</div>}
        <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 10 }}>Para aplicar la nueva calidad a un libro ya subido: en la Biblioteca, «Re-procesar» (re-rasteriza desde el PDF original y limpia sus datos para volver a transcribir).</div>
      </div>
    </>
  );
}

function ApiKeysSection() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [expires, setExpires] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const { confirmDialog, ask } = useConfirm();

  const base = `${window.location.origin}/api`;
  const load = () => listApiKeys().then(setKeys).catch(() => setKeys([]));
  useEffect(() => { load(); }, []);

  async function create() {
    if (!name.trim()) { setMsg("Pon un nombre al token"); return; }
    setBusy(true); setMsg(null);
    try {
      const r = await createApiKey({ name: name.trim(), scope, expires_days: expires ? Number(expires) : undefined });
      setToken(r.token); setName(""); await load();
    } catch (err) { setMsg((err as Error).message); }
    setBusy(false);
  }
  async function revoke(id: string) {
    if (!(await ask({ title: "¿Revocar este token?", body: "Las integraciones que lo usen dejarán de funcionar.", danger: true, confirmLabel: "Revocar" }))) return;
    try { await revokeApiKey(id); await load(); } catch (err) { setMsg((err as Error).message); }
  }

  const fld: React.CSSProperties = { background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", color: "var(--fg)", fontFamily: "inherit", fontSize: 13.5, boxSizing: "border-box" };
  const code: React.CSSProperties = { fontFamily: fonts.mono, fontSize: 11.5, background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 8, padding: "10px 12px", color: "var(--fg)", overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" };

  return (
    <>
      {confirmDialog}
      <h3 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 6px" }}>API externa</h3>
      <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 12px" }}>
        Crea tokens para acceder a la API desde fuera (scripts, integraciones). Envíalos en la cabecera
        <code style={{ fontFamily: fonts.mono, fontSize: 12 }}> Authorization: Bearer gsk_…</code>. Base: <code style={{ fontFamily: fonts.mono, fontSize: 12 }}>{base}</code>
      </p>

      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: 18, marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)", flex: 1, minWidth: 160 }}>Nombre<input style={fld} value={name} onChange={(e) => setName(e.target.value)} placeholder="Mi script" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Permisos<select style={fld} value={scope} onChange={(e) => setScope(e.target.value as "read" | "write")}><option value="read">Solo lectura</option><option value="write">Lectura y escritura</option></select></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 12, color: "var(--muted)" }}>Caduca (días)<input style={{ ...fld, width: 90 }} value={expires} onChange={(e) => setExpires(e.target.value.replace(/\D/g, ""))} placeholder="∞" /></label>
          <button onClick={create} disabled={busy} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "10px 18px", fontFamily: "inherit", fontSize: 13.5, fontWeight: 600, cursor: "pointer", height: 38 }}>{busy ? "Creando…" : "Crear token"}</button>
        </div>
        {msg && <div style={{ fontSize: 12.5, color: "var(--danger)", marginTop: 8 }}>{msg}</div>}
        {token && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 12.5, color: "var(--ok)", marginBottom: 6 }}>✓ Token creado — cópialo ahora, no se vuelve a mostrar:</div>
            <div style={code}>{token}</div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button onClick={() => { navigator.clipboard?.writeText(token); }} style={{ background: "transparent", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, padding: "7px 14px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>Copiar token</button>
              <button onClick={() => setToken(null)} style={{ background: "transparent", color: "var(--muted)", border: "1px solid var(--line)", borderRadius: 8, padding: "7px 14px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>Ocultar</button>
            </div>
            <div style={{ ...code, marginTop: 10 }}>{`curl -H "Authorization: Bearer $TOKEN" ${base}/tree/stats`}</div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>Sustituye <code>$TOKEN</code> por el token de arriba (no se incrusta aquí por seguridad).</div>
          </div>
        )}
      </div>

      {keys.length > 0 && (
        <div style={{ border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden", marginBottom: 4 }}>
          {keys.map((k, i) => (
            <div key={k.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderBottom: i < keys.length - 1 ? "1px solid var(--line2)" : "none", background: "var(--surface)", opacity: k.revoked_at ? 0.5 : 1 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>{k.name} <span style={{ fontFamily: fonts.mono, fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>{k.token_prefix}…</span></div>
                <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: "var(--muted)", marginTop: 2 }}>
                  {k.scope === "write" ? "lectura+escritura" : "solo lectura"}
                  {k.last_used_at ? ` · usado ${new Date(k.last_used_at).toLocaleDateString()}` : " · sin usar"}
                  {k.expires_at ? ` · caduca ${new Date(k.expires_at).toLocaleDateString()}` : ""}
                  {k.revoked_at ? " · REVOCADO" : ""}
                </div>
              </div>
              {!k.revoked_at && <button onClick={() => revoke(k.id)} style={{ flex: "none", background: "transparent", color: "var(--danger)", border: "1px solid var(--danger)", borderRadius: 8, padding: "7px 13px", fontFamily: "inherit", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>Revocar</button>}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function DangerZone() {
  const e = useEstela();
  const [done, setDone] = useState<string | null>(null);
  const { confirmDialog, ask } = useConfirm();
  const items: { scope: string; label: string; desc: string }[] = [
    { scope: "discoveries", label: "Borrar descubrimientos", desc: "Elimina los candidatos de coincidencia (no toca el árbol ni los libros)." },
    { scope: "library", label: "Borrar biblioteca", desc: "Elimina libros, páginas, transcripciones y actas extraídas." },
    { scope: "tree", label: "Borrar árbol", desc: "Elimina personas, familias, eventos y citas." },
    { scope: "all", label: "Borrar TODO", desc: "Árbol + biblioteca + descubrimientos + lugares. No toca tus proveedores de IA." },
  ];
  async function reset(scope: string, label: string) {
    const ok = await ask({
      title: label,
      body: "Esta acción es IRREVERSIBLE.",
      danger: true, typed: "BORRAR", confirmLabel: "Borrar definitivamente",
    });
    if (!ok) return;
    try { await api(`/tenants/reset`, { method: "POST", body: JSON.stringify({ scope }) }); setDone(scope); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
  }
  return (
    <>
      {confirmDialog}
      <h3 style={{ fontSize: 15, fontWeight: 600, margin: "28px 0 10px", color: "var(--danger)" }}>Zona peligrosa</h3>
      <div style={{ border: "1px solid var(--danger)", borderRadius: 12, padding: 6, background: "rgba(192,57,43,.04)" }}>
        {items.map((it) => (
          <div key={it.scope} style={{ display: "flex", alignItems: "center", gap: 16, padding: "12px 14px", borderBottom: it.scope !== "all" ? "1px solid var(--line2)" : "none" }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 14, color: it.scope === "all" ? "var(--danger)" : "var(--fg)" }}>{it.label}</div>
              <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 3 }}>{it.desc}</div>
            </div>
            {done === it.scope ? (
              <span style={{ fontSize: 12.5, color: "var(--ok)" }}>✓ Borrado — recarga</span>
            ) : (
              <button onClick={() => reset(it.scope, it.label)} style={{ flex: "none", background: "transparent", color: "var(--danger)", border: "1px solid var(--danger)", borderRadius: 8, padding: "9px 16px", fontFamily: "inherit", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
                {it.label.replace("Borrar", "Borrar")}
              </button>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
