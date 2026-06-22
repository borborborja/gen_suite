import { api } from "./client";

export interface MeOut {
  user: { id: string; email: string; full_name: string | null; is_server_admin: boolean };
  active_tenant_id: string | null;
  active_role: string | null;
  memberships: { tenant_id: string; tenant_name: string; tenant_slug: string; role: string }[];
}

export const getMe = () => api<MeOut>("/auth/me");

export const updateMe = (body: { full_name?: string; email?: string }) =>
  api<MeOut>("/auth/me", { method: "PATCH", body: JSON.stringify(body) });

export const changePassword = (current_password: string, new_password: string) =>
  api<void>("/auth/change-password", { method: "POST", body: JSON.stringify({ current_password, new_password }) });
