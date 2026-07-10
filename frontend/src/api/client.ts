const ACCESS = "gs_access";
const REFRESH = "gs_refresh";

export function setTokens(access: string, refresh: string) {
  localStorage.setItem(ACCESS, access);
  localStorage.setItem(REFRESH, refresh);
}

export function clearTokens() {
  localStorage.removeItem(ACCESS);
  localStorage.removeItem(REFRESH);
}

export function hasToken(): boolean {
  return !!localStorage.getItem(ACCESS);
}

// A single in-flight refresh shared by all concurrent 401s, so a burst of expired requests
// triggers exactly one /auth/refresh. Resolves to true if tokens were renewed.
let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refresh = localStorage.getItem(REFRESH);
  if (!refresh) return false;
  try {
    const res = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) return false;
    const t = await res.json();
    setTokens(t.access_token, t.refresh_token);
    return true;
  } catch {
    return false;
  }
}

async function tryRefresh(): Promise<boolean> {
  if (!refreshing) refreshing = refreshTokens().finally(() => { refreshing = null; });
  return refreshing;
}

async function doFetch(path: string, opts: RequestInit, auth: boolean): Promise<Response> {
  const headers: Record<string, string> = {
    ...((opts.headers as Record<string, string>) || {}),
  };
  if (auth) {
    const token = localStorage.getItem(ACCESS);
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  return fetch(`/api${path}`, { ...opts, headers });
}

// Authenticated fetch with the same single-flight 401→refresh→retry behavior as api(), but
// returning the raw Response. Use this for anything that isn't plain JSON (uploads/FormData,
// blobs/images, SSE streams) instead of re-implementing the token header by hand.
export async function authFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  let res = await doFetch(path, opts, true);
  if (res.status === 401 && localStorage.getItem(REFRESH)) {
    if (await tryRefresh()) {
      res = await doFetch(path, opts, true);
    } else {
      clearTokens();
      window.dispatchEvent(new Event("gs-auth-expired"));
    }
  }
  return res;
}

export async function api<T = any>(
  path: string,
  opts: RequestInit = {},
  auth = true,
): Promise<T> {
  const withJson: RequestInit = {
    ...opts,
    headers: { "Content-Type": "application/json", ...((opts.headers as Record<string, string>) || {}) },
  };
  let res = await doFetch(path, withJson, auth);

  // Access token likely expired — refresh once (deduped) and retry the original request. If the
  // refresh fails, the session is truly gone: clear tokens and signal the app to show login.
  if (res.status === 401 && auth && localStorage.getItem(REFRESH)) {
    if (await tryRefresh()) {
      res = await doFetch(path, withJson, auth);
    } else {
      clearTokens();
      window.dispatchEvent(new Event("gs-auth-expired"));
    }
  }

  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null as T;
  return res.json() as Promise<T>;
}
