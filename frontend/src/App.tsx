import { useCallback, useEffect, useState } from "react";
import { api, clearTokens, hasToken, setTokens } from "./api/client";
import TreeView from "./features/tree/TreeView";
import DocumentsView from "./features/documents/DocumentsView";
import ProvidersView from "./features/providers/ProvidersView";
import EstelaApp from "./features/estela/EstelaApp";

interface Membership {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  role: string;
}
interface Me {
  user: { id: string; email: string; full_name: string | null; is_server_admin: boolean };
  active_tenant_id: string | null;
  active_role: string | null;
  memberships: Membership[];
}
interface Member {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
}
interface TokenPair {
  access_token: string;
  refresh_token: string;
}

// ── TEMPORARY: login disabled while gen_suite is not a public product. The app auto-signs-in to a
// single dev account and lands straight in Estela. Restore the <Auth> gate (remove DEV_BYPASS) when
// reinstating login. Credentials live only in this client during the private phase.
const DEV_BYPASS = true;
const DEV_EMAIL = "test@example.com";
const DEV_PASSWORD = "estela12345";
// Land directly on the tenant that holds the imported tree (Vallbona). Temporary, dev-only.
const DEV_TENANT_ID = "36a4aac0-2b87-42d6-97d9-7206e59b82b8";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [booting, setBooting] = useState(DEV_BYPASS);

  const loadMe = useCallback(async () => {
    try {
      const m = await api<Me>("/auth/me");
      // land on the tree's tenant (DEV) or the first membership, so we open Estela not the picker
      const target = DEV_BYPASS
        ? (m.memberships.find((x) => x.tenant_id === DEV_TENANT_ID)?.tenant_id ?? m.memberships[0]?.tenant_id)
        : m.memberships[0]?.tenant_id;
      if (target && m.active_tenant_id !== target) {
        const t = await api<TokenPair>(`/auth/switch/${target}`, { method: "POST" });
        setTokens(t.access_token, t.refresh_token);
        setMe(await api<Me>("/auth/me"));
      } else {
        setMe(m);
      }
    } catch {
      clearTokens();
      setMe(null);
    }
  }, []);

  const autoLogin = useCallback(async () => {
    try {
      const t = await api<TokenPair>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: DEV_EMAIL, password: DEV_PASSWORD }),
      }, false);
      setTokens(t.access_token, t.refresh_token);
      await loadMe();
    } catch (e) {
      setError((e as Error).message);
    }
    setBooting(false);
  }, [loadMe]);

  useEffect(() => {
    if (DEV_BYPASS) { void autoLogin(); return; }  // always fresh login, ignore stale tokens
    if (hasToken()) void loadMe().finally(() => setBooting(false));
  }, [loadMe, autoLogin]);

  // The API client fires this when a refresh fails (session truly expired) — drop to the login view.
  useEffect(() => {
    const onExpired = () => { setMe(null); setError("Tu sesión ha caducado. Vuelve a entrar."); };
    window.addEventListener("gs-auth-expired", onExpired);
    return () => window.removeEventListener("gs-auth-expired", onExpired);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: localStorage.getItem("gs_refresh") }),
      });
    } catch {
      /* ignore */
    }
    clearTokens();
    await loadMe();
  }, [loadMe]);

  if (booting) {
    return (
      <div className="wrap" style={{ textAlign: "center", paddingTop: "20vh" }}>
        <h1><span className="brand">Estela</span></h1>
        <p className="muted">Entrando…</p>
        {error && <p className="error">{error}</p>}
      </div>
    );
  }
  if (!me) {
    if (DEV_BYPASS) {
      return (
        <div className="wrap" style={{ textAlign: "center", paddingTop: "20vh" }}>
          <p className="error">No se pudo entrar automáticamente. ¿Está el backend arriba?</p>
          {error && <p className="error">{error}</p>}
        </div>
      );
    }
    return <Auth onAuthed={loadMe} error={error} setError={setError} />;
  }
  // With an active tenant, the Estela UI is the product surface. Without one,
  // fall back to the tenant-setup dashboard so the user can create/select a tenant.
  if (me.active_tenant_id) return <EstelaApp account={{ email: me.user.email, onLogout: logout }} />;
  return <Dashboard me={me} reload={loadMe} setError={setError} error={error} />;
}

function Auth({
  onAuthed,
  error,
  setError,
}: {
  onAuthed: () => Promise<void>;
  error: string | null;
  setError: (e: string | null) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function submit() {
    setError(null);
    try {
      const t = await api<TokenPair>(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }, false);
      setTokens(t.access_token, t.refresh_token);
      await onAuthed();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="wrap">
      <h1>
        <span className="brand">gen_suite</span> · investigación genealógica
      </h1>
      <div className="card" style={{ maxWidth: 420 }}>
        <div className="tabs">
          <button className={mode === "login" ? "" : "secondary"} onClick={() => setMode("login")}>
            Entrar
          </button>
          <button className={mode === "register" ? "" : "secondary"} onClick={() => setMode("register")}>
            Registrarse
          </button>
        </div>
        <div className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input
            placeholder="contraseña (mín. 8)"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button onClick={submit} disabled={!email || password.length < 8}>
            {mode === "login" ? "Entrar" : "Crear cuenta"}
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      </div>
    </div>
  );
}

function Dashboard({
  me,
  reload,
  error,
  setError,
}: {
  me: Me;
  reload: () => Promise<void>;
  error: string | null;
  setError: (e: string | null) => void;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [tenantName, setTenantName] = useState("");

  const loadMembers = useCallback(async () => {
    if (!me.active_tenant_id) return setMembers([]);
    try {
      setMembers(await api<Member[]>("/tenants/members"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [me.active_tenant_id, setError]);

  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  async function switchTenant(tid: string) {
    setError(null);
    try {
      const t = await api<TokenPair>(`/auth/switch/${tid}`, { method: "POST" });
      setTokens(t.access_token, t.refresh_token);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function createTenant() {
    setError(null);
    try {
      const t = await api<{ id: string }>("/tenants", {
        method: "POST",
        body: JSON.stringify({ name: tenantName }),
      });
      setTenantName("");
      await switchTenant(t.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function logout() {
    try {
      await api("/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: localStorage.getItem("gs_refresh") }),
      });
    } catch {
      /* ignore */
    }
    clearTokens();
    await reload();
  }

  const active = me.memberships.find((m) => m.tenant_id === me.active_tenant_id);

  return (
    <div className="wrap">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>
          <span className="brand">gen_suite</span>
        </h1>
        <div className="row">
          <span className="muted">{me.user.email}</span>
          {me.user.is_server_admin && <span className="badge admin">server-admin</span>}
          <button className="secondary" onClick={logout}>
            Salir
          </button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="card">
        <strong>Tenant activo:</strong>{" "}
        {active ? (
          <>
            {active.tenant_name} <span className="badge">{active.role}</span>
          </>
        ) : (
          <span className="muted">ninguno — crea uno o selecciona abajo</span>
        )}
      </div>

      {me.active_tenant_id && <TreeView onError={setError} />}
      {me.active_tenant_id && <DocumentsView onError={setError} />}
      {me.active_tenant_id && (me.active_role === "tenant_admin" || me.user.is_server_admin) && (
        <ProvidersView onError={setError} />
      )}

      <div className="card">
        <h3>Mis tenants</h3>
        <ul className="list">
          {me.memberships.map((m) => (
            <li key={m.tenant_id}>
              <span>
                {m.tenant_name} <span className="badge">{m.role}</span>
              </span>
              {m.tenant_id !== me.active_tenant_id && (
                <button className="secondary" onClick={() => switchTenant(m.tenant_id)}>
                  Activar
                </button>
              )}
            </li>
          ))}
          {me.memberships.length === 0 && <li className="muted">Sin tenants todavía.</li>}
        </ul>
        <div className="row" style={{ marginTop: ".75rem" }}>
          <input
            placeholder="Nombre del nuevo tenant"
            value={tenantName}
            onChange={(e) => setTenantName(e.target.value)}
          />
          <button onClick={createTenant} disabled={!tenantName}>
            Crear tenant
          </button>
        </div>
      </div>

      {me.active_tenant_id && (
        <div className="card">
          <h3>Miembros del tenant</h3>
          <ul className="list">
            {members.map((m) => (
              <li key={m.user_id}>
                <span>{m.email}</span>
                <span className="badge">{m.role}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="muted">
        Fundación (Fase 0). Próximo: importar GEDCOM y el visor del árbol.
      </p>
    </div>
  );
}
