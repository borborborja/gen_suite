import { api } from "./client";

export interface WorkspaceMember {
  user_id: string; email: string; full_name: string | null; role: string;
}

export const listWorkspaceMembers = () => api<WorkspaceMember[]>("/tenants/members");
export const addWorkspaceMember = (body: { email: string; role: string }) =>
  api<WorkspaceMember>("/tenants/members", { method: "POST", body: JSON.stringify(body) });
export const updateWorkspaceMember = (userId: string, role: string) =>
  api<WorkspaceMember>("/tenants/members/" + userId, { method: "PATCH", body: JSON.stringify({ role }) });
export const removeWorkspaceMember = (userId: string) =>
  api<void>("/tenants/members/" + userId, { method: "DELETE" });
