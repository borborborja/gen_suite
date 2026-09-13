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
interface PublicTree {
  title: string;
  published_at: string | null;
  payload: { persons?: Array<{ id: string; given: string | null; surname: string | null; events?: Array<{ type: string; date_raw: string | null; value: string | null }> }>; families?: unknown[] };
}

// Optional dev auto-login: set VITE_DEV_BYPASS=true (plus VITE_DEV_EMAIL / VITE_DEV_PASSWORD)
// in frontend/.env.local. Nothing is hardcoded in the bundle; production builds without these
// vars always show the login screen.
const DEV_BYPASS = import.meta.env.VITE_DEV_BYPASS === "true";
const DEV_EMAIL = import.meta.env.VITE_DEV_EMAIL as string | undefined;
const DEV_PASSWORD = import.meta.env.VITE_DEV_PASSWORD as string | undefined;

export default function App() {
  const publicMatch = /^\/p\/([a-z0-9-]+)$/i.exec(window.location.pathname);
  if (publicMatch) return <PublicShare slug={publicMatch[1]} />;
  if (window.location.pathname === "/reset") return <ResetPassword />;
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
        const t = await api<{ access_token: string }>(`/auth/session/switch/${target}`, { method: "POST" });
        setTokens(t.access_token);
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
      const t = await api<{ access_token: string }>("/auth/session/login", {
        method: "POST",
        body: JSON.stringify({ email: DEV_EMAIL, password: DEV_PASSWORD }),
      }, false);
      setTokens(t.access_token);
      await loadMe();
    } catch (e) {
      setError((e as Error).message);
    }
    setBooting(false);
  }, [loadMe]);

  useEffect(() => {
    if (hasToken()) { void loadMe().finally(() => setBooting(false)); return; }
    void api<{ access_token: string }>("/auth/session/refresh", { method: "POST" }, false)
      .then((t) => { setTokens(t.access_token); return loadMe(); })
      .catch(() => {
        if (DEV_BYPASS && DEV_EMAIL && DEV_PASSWORD) return autoLogin();
        return undefined;
      })
      .finally(() => setBooting(false));
  }, [loadMe, autoLogin]);

  // The API client fires this when a refresh fails (session truly expired) — drop to the login view.
  useEffect(() => {
    const onExpired = () => { setMe(null); setError("Tu sesión ha caducado. Vuelve a entrar."); };
    window.addEventListener("gs-auth-expired", onExpired);
    return () => window.removeEventListener("gs-auth-expired", onExpired);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/session/logout", { method: "POST" }, false);
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

function PublicShare({ slug }: { slug: string }) {
  const [tree, setTree] = useState<PublicTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const token = window.location.hash.slice(1);
    // Remove the capability from copy/paste history as soon as it has been read.
    window.history.replaceState(null, "", `/p/${slug}`);
    if (!token) { setError("Este enlace no contiene una clave de acceso válida."); return; }
    void api<PublicTree>(`/public/share/${encodeURIComponent(slug)}`, {
      method: "POST", body: JSON.stringify({ token }),
    }, false).then(setTree).catch(() => setError("El enlace no existe, ha caducado o fue revocado."));
  }, [slug]);
  return <Shell>
    <h1 style={styles.brand}>{tree?.title || "Árbol compartido"}</h1>
    {error && <p style={styles.error}>{error}</p>}
    {!tree && !error && <p style={styles.muted}>Cargando árbol…</p>}
    {tree && <div style={styles.card}>
      <p style={styles.sub}>{tree.payload.persons?.length || 0} personas publicadas</p>
      {(tree.payload.persons || []).map((person) => <div key={person.id} style={{ padding: "9px 0", borderBottom: "1px solid #e2dbd0" }}>
        <strong>{[person.given, person.surname].filter(Boolean).join(" ") || "Sin nombre"}</strong>
        {(person.events || []).map((event, index) => <div key={index} style={styles.muted}>{event.type}{event.date_raw ? ` · ${event.date_raw}` : ""}{event.value ? ` · ${event.value}` : ""}</div>)}
      </div>)}
    </div>}
  </Shell>;
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
  const inviteToken = new URLSearchParams(window.location.search).get("token");
  const [mode, setMode] = useState<"login" | "register" | "request">(inviteToken ? "register" : "login");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (mode === "request") {
        await api("/auth/access-requests", { method: "POST", body: JSON.stringify({ email, full_name: fullName || undefined }) }, false);
        setError("Solicitud enviada. Recibirás una invitación si se aprueba el acceso.");
        return;
      }
      const t = await api<{ access_token: string }>(`/auth/session/${mode}`, {
        method: "POST",
        body: JSON.stringify({ email, password, invite_token: mode === "register" ? inviteToken : undefined, mfa_code: mfaCode || undefined }),
      }, false);
      setTokens(t.access_token);
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
      <form onSubmit={(ev) => { ev.preventDefault(); if (email && (mode === "request" || password.length >= 8)) void submit(); }}>
        <input style={styles.input} placeholder="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        {mode === "request" && <input style={styles.input} placeholder="nombre (opcional)" value={fullName} onChange={(e) => setFullName(e.target.value)} />}
        {mode !== "request" && <>
        <input
          style={styles.input}
          placeholder="contraseña (mín. 8 caracteres)"
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {mode === "login" && <input style={styles.input} placeholder="código MFA (si lo activaste)" inputMode="numeric" autoComplete="one-time-code" value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} />}
        </>}
        <button type="submit" style={{ ...styles.primary, opacity: !email || (mode !== "request" && password.length < 8) || busy ? 0.6 : 1 }} disabled={!email || (mode !== "request" && password.length < 8) || busy}>
          {busy ? "Un momento…" : mode === "login" ? "Entrar" : mode === "register" ? "Crear cuenta" : "Solicitar acceso"}
        </button>
      </form>
      <p style={{ marginTop: 16 }}>
        <button style={styles.ghost} onClick={() => { setError(null); setMode(mode === "login" ? "request" : "login"); }}>
          {mode === "login" ? "¿Necesitas acceso? Solicítalo" : "¿Ya tienes cuenta? Entra"}
        </button>
      </p>
      {mode === "request" && <p><button style={styles.ghost} onClick={() => setMode("register")}>¿Tienes una invitación o eres la administración inicial? Crear cuenta</button></p>}
      {inviteToken && mode !== "register" && <p><button style={styles.ghost} onClick={() => setMode("register")}>Usar la invitación recibida</button></p>}
      {error && <p style={styles.error}>{error}</p>}
    </Shell>
  );
}

function ResetPassword() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  async function submit() {
    try {
      await api("/auth/password-reset/confirm", { method: "POST", body: JSON.stringify({ token, new_password: password }) }, false);
      setMessage("Contraseña restablecida. Ya puedes entrar.");
    } catch (error) { setMessage((error as Error).message); }
  }
  return <Shell><h1 style={styles.brand}>Restablecer contraseña</h1>
    <p style={styles.sub}>El enlace solo puede usarse una vez.</p>
    <input style={styles.input} type="password" autoComplete="new-password" placeholder="nueva contraseña (mín. 8 caracteres)" value={password} onChange={(event) => setPassword(event.target.value)} />
    <button style={styles.primary} disabled={!token || password.length < 8} onClick={() => void submit()}>Guardar contraseña</button>
    {message && <p style={styles.error}>{message}</p>}
  </Shell>;
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
      const t = await api<{ access_token: string }>(`/auth/session/switch/${tid}`, { method: "POST" });
      setTokens(t.access_token);
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
