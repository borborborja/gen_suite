let accessToken: string | null = null;

export function setTokens(access: string) {
  accessToken = access;
}

export function clearTokens() {
  accessToken = null;
}

export function hasToken(): boolean {
  return !!accessToken;
}

// A single in-flight refresh shared by all concurrent 401s, so a burst of expired requests
// triggers exactly one /auth/refresh. Resolves to true if tokens were renewed.
let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  try {
    const res = await fetch("/api/auth/session/refresh", {
      method: "POST",
      credentials: "same-origin",
    });
    if (!res.ok) return false;
    const t = await res.json();
    setTokens(t.access_token);
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
    const token = accessToken;
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  return fetch(`/api${path}`, { ...opts, headers, credentials: "same-origin" });
}

// Authenticated fetch with the same single-flight 401→refresh→retry behavior as api(), but
// returning the raw Response. Use this for anything that isn't plain JSON (uploads/FormData,
// blobs/images, SSE streams) instead of re-implementing the token header by hand.
export async function authFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  let res = await doFetch(path, opts, true);
  if (res.status === 401 && accessToken) {
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
  if (res.status === 401 && auth && accessToken) {
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
