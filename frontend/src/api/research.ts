import { api } from "./client";

export interface Repository { id: string; name: string; url: string | null; address: string | null; notes: string | null }
export interface Source {
  id: string; title: string; source_type: string; author: string | null; publication_info: string | null;
  call_number: string | null; url: string | null; repository_id: string | null;
  primary_document_id: string | null; raw: Record<string, unknown> | null;
  repository_name: string | null; document_title: string | null;
  documents: { id: string; title: string }[];
}
export interface ResearchProject {
  id: string; title: string; description: string | null; question: string | null; target_person_id: string | null;
  conclusion: string | null; status: string; created_by: string | null; open_tasks: number;
}
export interface ResearchSearch {
  id: string; project_id: string; scope: string; criteria: string; result: "found" | "not_found" | "inconclusive";
  notes: string | null; created_at: string;
}
export interface ResearchTask {
  id: string; project_id: string | null; title: string; description: string | null; status: string;
  priority: string; due_at: string | null; assignee_user_id: string | null; target_type: string | null;
  target_id: string | null; created_by: string | null;
}
export interface Assertion {
  id: string; subject_type: string; subject_id: string; predicate: string; value: Record<string, unknown>;
  state: string; confidence: number | null; created_by: string | null;
}

export const listRepositories = () => api<Repository[]>("/research/repositories");
export const createRepository = (body: Pick<Repository, "name" | "url" | "address" | "notes">) =>
  api<Repository>("/research/repositories", { method: "POST", body: JSON.stringify(body) });
export const listSources = () => api<Source[]>("/research/sources");
export const createSource = (body: Partial<Source> & { title: string }) =>
  api<Source>("/research/sources", { method: "POST", body: JSON.stringify(body) });
export const listProjects = () => api<ResearchProject[]>("/research/projects");
export const createProject = (body: { title: string; description?: string; question?: string; target_person_id?: string; conclusion?: string }) =>
  api<ResearchProject>("/research/projects", { method: "POST", body: JSON.stringify(body) });
export const listResearchSearches = (projectId: string) => api<ResearchSearch[]>(`/research/projects/${projectId}/searches`);
export const createResearchSearch = (projectId: string, body: Pick<ResearchSearch, "scope" | "criteria" | "result" | "notes">) =>
  api<ResearchSearch>(`/research/projects/${projectId}/searches`, { method: "POST", body: JSON.stringify(body) });
export const listTasks = (projectId?: string) => api<ResearchTask[]>(`/research/tasks${projectId ? `?project_id=${projectId}` : ""}`);
export const createTask = (body: { title: string; project_id?: string; priority?: string }) =>
  api<ResearchTask>("/research/tasks", { method: "POST", body: JSON.stringify(body) });
export const patchTask = (id: string, body: Partial<Pick<ResearchTask, "status" | "priority" | "title">>) =>
  api<ResearchTask>(`/research/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const listAssertions = (subjectType?: string, subjectId?: string) => {
  const params = new URLSearchParams();
  if (subjectType) params.set("subject_type", subjectType);
  if (subjectId) params.set("subject_id", subjectId);
  return api<Assertion[]>(`/research/assertions?${params.toString()}`);
};
