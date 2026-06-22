// Client for the linkage/discovery API (plan §5). Mirrors api/transcription.ts. The Estela
// DiscoveryView currently renders sample data from features/estela/data.ts; swapping it onto
// these calls is the wiring step once a corpus has been extracted.
import { api } from "./client";

export interface JobOut {
  id: string;
  type: string;
  status: string;
  progress: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface MentionOut {
  id: string;
  role: string;
  name_raw: string | null;
  given: string | null;
  surname: string | null;
}

export interface RecordOut {
  id: string;
  record_type: string;
  date_raw: string | null;
  date_year: number | null;
  summary: string | null;
  parish_raw: string | null;
  transcription_id: string | null;
  page_id: string | null;
  document_id: string | null;
  page_no: number | null;
  folio_label: string | null;
  confidence: number | null;
  mentions: MentionOut[];
}

export interface TreePersonOut {
  id: string;
  given: string | null;
  surname: string | null;
  birth_year: number | null;
  death_year: number | null;
}

export interface EvidenceSignal {
  value: number;
  weight: number;
  reason: string;
}

export interface CandidateOut {
  id: string;
  tree_person_id: string;
  person_mention_id: string;
  record_id: string | null;
  score: number;
  status: string;
  method: string;
  relation: string; // "self" | "sibling"
  evidence: { score: number; signals: Record<string, EvidenceSignal>; needs_llm: boolean } | null;
  record: RecordOut | null;
  mention: MentionOut | null;
  tree_person: TreePersonOut | null;
  created_at: string;
}

export interface ProposalOut {
  mention_id: string;
  role: string;
  name_raw: string | null;
  given: string | null;
  surname: string | null;
  suggested_relation: string;
}

export const listProposals = (candidateId: string) =>
  api<ProposalOut[]>(`/linkage/candidates/${candidateId}/proposals`);

export const acceptProposal = (candidateId: string, mentionId: string) =>
  api<{ person_id: string; mention_id: string }>(
    `/linkage/candidates/${candidateId}/proposals/${mentionId}/accept`,
    { method: "POST" },
  );

export interface Coreferent {
  mention_id: string;
  record_id: string;
  name_raw: string | null;
  role: string;
  record_type: string | null;
  date_year: number | null;
  score: number;
}
// Within-corpus: other acts that mention the SAME person (M4).
export const coreferents = (mentionId: string) =>
  api<Coreferent[]>(`/linkage/mentions/${mentionId}/coreferents`);

export interface DecisionOut {
  id: string;
  status: string;
  resolved_person_id: string | null;
}

export const discover = (person_id: string, max_candidates = 50) =>
  api<JobOut>("/linkage/discover", {
    method: "POST",
    body: JSON.stringify({ person_id, max_candidates }),
  });

// Sibling-set discovery: find other baptisms with the same parents → siblings + the parents they confirm.
export const discoverFamily = (person_id: string, max_candidates = 50) =>
  api<JobOut>("/linkage/discover-family", {
    method: "POST",
    body: JSON.stringify({ person_id, max_candidates }),
  });

export function listCandidates(person_id?: string, status?: string) {
  const qs = new URLSearchParams();
  if (person_id) qs.set("person_id", person_id);
  if (status) qs.set("status_filter", status);
  const q = qs.toString();
  return api<CandidateOut[]>(`/linkage/candidates${q ? `?${q}` : ""}`);
}

export const confirmCandidate = (id: string) =>
  api<DecisionOut>(`/linkage/candidates/${id}/confirm`, { method: "POST" });

export const rejectCandidate = (id: string) =>
  api<DecisionOut>(`/linkage/candidates/${id}/reject`, { method: "POST" });
