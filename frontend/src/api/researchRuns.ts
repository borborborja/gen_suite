import { api } from "./client";
import type { ResearchExecution, ResearchStage, ResearchRunEvent } from "./types";

export interface ResearchRunCreate {
  client_id: string; title: string; document_ids: string[]; project_id?: string;
  budget_microusd: number; discover_all_tree: boolean; transcribe: boolean; extract: boolean;
  index: boolean; pdf: boolean; archive: boolean; lang: string;
}
export const listResearchRuns = (offset = 0) => api<ResearchExecution[]>(`/research/runs?offset=${offset}&limit=20`);
export const createResearchRun = (body: ResearchRunCreate) => api<ResearchExecution>("/research/runs", { method: "POST", body: JSON.stringify(body) });
export const getResearchRun = (id: string) => api<ResearchExecution>(`/research/runs/${id}`);
export const researchRunStages = (id: string, offset = 0) => api<ResearchStage[]>(`/research/runs/${id}/stages?offset=${offset}&limit=50`);
export const researchRunEvents = (id: string) => api<ResearchRunEvent[]>(`/research/runs/${id}/events?limit=10`);
export const controlResearchRun = (id: string, action: "start" | "pause" | "resume" | "retry" | "cancel", expected_version: number, reason = "") =>
  api<ResearchExecution>(`/research/runs/${id}/${action}`, { method: "POST", body: JSON.stringify({ expected_version, reason }) });
export const updateResearchRunBudget = (id: string, expected_version: number, budget_microusd: number, reason: string) =>
  api<ResearchExecution>(`/research/runs/${id}/budget`, { method: "PATCH", body: JSON.stringify({ expected_version, budget_microusd, reason }) });
