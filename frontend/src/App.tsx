import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { api, clearTokens, hasToken, setTokens } from "./api/client";
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
interface TokenPair {
  access_token: string;
  refresh_token: string;
}

// Optional dev auto-login: set VITE_DEV_BYPASS=true (plus VITE_DEV_EMAIL / VITE_DEV_PASSWORD)
// in frontend/.env.local. Nothing is hardcoded in the bundle; production builds without these
// vars always show the login screen.
const DEV_BYPASS = import.meta.env.VITE_DEV_BYPASS === "true";
const DEV_EMAIL = import.meta.env.VITE_DEV_EMAIL as string | undefined;
const DEV_PASSWORD = import.meta.env.VITE_DEV_PASSWORD as string | undefined;

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  // set on explicit logout so the dev bypass doesn't immediately sign back in
  const [loggedOut, setLoggedOut] = useState(false);

  const loadMe = useCallback(async () => {
    try {
      const m = await api<Me>("/auth/me");
      // exactly one membership and none active → auto-select it so we land in Estela, not the picker
      const target = m.active_tenant_id ? null : (m.memberships.length === 1 ? m.memberships[0].tenant_id : null);
      if (target) {
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
    if (hasToken()) { void loadMe().finally(() => setBooting(false)); return; }
    if (DEV_BYPASS && DEV_EMAIL && DEV_PASSWORD) { void autoLogin(); return; }
    setBooting(false);
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
    setMe(null);
    setError(null);
    setLoggedOut(true); // show the login screen even with the dev bypass enabled
  }, []);

  const onAuthed = useCallback(async () => { setLoggedOut(false); await loadMe(); }, [loadMe]);

  if (booting) {
    return (
      <Shell>
        <h1 style={styles.brand}>Estela</h1>
        <p style={styles.muted}>Entrando…</p>
        {error && <p style={styles.error}>{error}</p>}
      </Shell>
    );
  }
  // initialError: only session-level messages (expired, dev auto-login failed) — the form's own
  // submit errors live inside <Auth>, so they always show even right after a logout.
  if (!me) return <Auth onAuthed={onAuthed} initialError={loggedOut ? null : error} />;
  // With an active tenant, the Estela UI is the product surface. Without one,
  // a minimal setup screen lets the user create or select a tenant.
  if (me.active_tenant_id) return <EstelaApp account={{ email: me.user.email, onLogout: logout }} />;
  return <TenantSetup me={me} reload={loadMe} onLogout={logout} error={error} setError={setError} />;
}

// ── shared minimal styling for the pre-Estela screens (login / tenant setup) ──
const styles: Record<string, CSSProperties> = {
  shell: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#faf7f2", color: "#1f1b16", fontFamily: "'Inter', system-ui, sans-serif" },
  panel: { width: "100%", maxWidth: 400, padding: "32px 28px", textAlign: "center" },
  brand: { fontFamily: "'Source Serif 4', Georgia, serif", fontSize: 34, fontWeight: 600, letterSpacing: "-.02em", margin: "0 0 4px" },
  sub: { color: "#8a8074", fontSize: 14, margin: "0 0 28px" },
  muted: { color: "#8a8074", fontSize: 14 },
  error: { color: "#c0392b", fontSize: 13.5, margin: "12px 0 0" },
  input: { width: "100%", boxSizing: "border-box", background: "#fff", border: "1px solid #e2dbd0", borderRadius: 9, padding: "12px 14px", fontFamily: "inherit", fontSize: 14.5, marginBottom: 10 },
  primary: { width: "100%", background: "#d9531e", color: "#fff", border: "none", borderRadius: 9, padding: "12px 16px", fontFamily: "inherit", fontSize: 15, fontWeight: 600, cursor: "pointer" },
  ghost: { background: "transparent", border: "none", color: "#8a8074", fontFamily: "inherit", fontSize: 13.5, cursor: "pointer", textDecoration: "underline" },
  card: { background: "#fff", border: "1px solid #e2dbd0", borderRadius: 12, padding: 18, textAlign: "left", marginBottom: 14 },
};

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={styles.shell}>
      <div style={styles.panel}>{children}</div>
    </div>
  );
}

function Auth({
  onAuthed,
  initialError,
}: {
  onAuthed: () => Promise<void>;
  initialError: string | null;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  async function submit() {
    setError(null);
    setBusy(true);
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
    setBusy(false);
  }

  return (
    <Shell>
      <h1 style={styles.brand}>Estela</h1>
      <p style={styles.sub}>Investigación genealógica sobre tus libros parroquiales</p>
      <form onSubmit={(ev) => { ev.preventDefault(); if (email && password.length >= 8) void submit(); }}>
        <input style={styles.input} placeholder="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input
          style={styles.input}
          placeholder="contraseña (mín. 8 caracteres)"
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" style={{ ...styles.primary, opacity: !email || password.length < 8 || busy ? 0.6 : 1 }} disabled={!email || password.length < 8 || busy}>
          {busy ? "Un momento…" : mode === "login" ? "Entrar" : "Crear cuenta"}
        </button>
      </form>
      <p style={{ marginTop: 16 }}>
        <button style={styles.ghost} onClick={() => { setError(null); setMode(mode === "login" ? "register" : "login"); }}>
          {mode === "login" ? "¿No tienes cuenta? Regístrate" : "¿Ya tienes cuenta? Entra"}
        </button>
      </p>
      {error && <p style={styles.error}>{error}</p>}
    </Shell>
  );
}

function TenantSetup({
  me,
  reload,
  onLogout,
  error,
  setError,
}: {
  me: Me;
  reload: () => Promise<void>;
  onLogout: () => Promise<void>;
  error: string | null;
  setError: (e: string | null) => void;
}) {
  const [name, setName] = useState("");

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
      const t = await api<{ id: string }>("/tenants", { method: "POST", body: JSON.stringify({ name }) });
      setName("");
      await switchTenant(t.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Shell>
      <h1 style={styles.brand}>Estela</h1>
      <p style={styles.sub}>{me.user.email} — elige o crea un espacio de investigación</p>
      {me.memberships.length > 0 && (
        <div style={styles.card}>
          {me.memberships.map((m) => (
            <div key={m.tenant_id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 0" }}>
              <span style={{ fontSize: 14.5 }}>{m.tenant_name} <span style={{ ...styles.muted, fontSize: 12 }}>· {m.role}</span></span>
              <button style={{ ...styles.primary, width: "auto", padding: "7px 14px", fontSize: 13 }} onClick={() => switchTenant(m.tenant_id)}>Abrir</button>
            </div>
          ))}
        </div>
      )}
      <div style={styles.card}>
        <div style={{ ...styles.muted, fontSize: 12.5, marginBottom: 8 }}>Nuevo espacio</div>
        <input style={styles.input} placeholder="Nombre (p. ej. Familia Vidal)" value={name} onChange={(e) => setName(e.target.value)} />
        <button style={{ ...styles.primary, opacity: name ? 1 : 0.6 }} disabled={!name} onClick={createTenant}>Crear</button>
      </div>
      {error && <p style={styles.error}>{error}</p>}
      <p style={{ marginTop: 18 }}>
        <button style={styles.ghost} onClick={() => void onLogout()}>Salir</button>
      </p>
    </Shell>
  );
}
