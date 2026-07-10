// Client for the corpus-wide tree reconstruction (linkage/reconstruct): launch a proposal,
// poll the latest one, and merge it (whole or selected families) into the tree.
import { api } from "./client";

export interface ReconPerson {
  key: string;
  given: string | null;
  surname: string | null;
  birth_year?: number | null;
  existing_person_id?: string;
  [k: string]: unknown;
}

export interface ReconFamily {
  key: string;
  husband_key: string | null;
  wife_key: string | null;
  child_keys: string[];
  record_ids: string[];
}

export interface ReconStats {
  persons: number;
  families: number;
  generations: number;
  linked_to_existing: number;
  [k: string]: unknown;
}

export interface ReconstructionOut {
  id: string;
  status: string; // running | completed | error
  conservative: boolean;
  include_census: boolean;
  link_to_tree: boolean;
  graph: { persons: ReconPerson[]; families: ReconFamily[] } | null;
  stats: ReconStats | null;
  job_id: string | null;
}

export const startReconstruction = (opts: { conservative?: boolean; include_census?: boolean; link_to_tree?: boolean } = {}) =>
  api<ReconstructionOut>("/linkage/reconstruct", { method: "POST", body: JSON.stringify(opts) });

export const latestReconstruction = () =>
  api<ReconstructionOut | null>("/linkage/reconstruction/latest");

export const mergeReconstruction = (id: string, familyKeys?: string[]) =>
  api<{ persons: number; families: number }>(`/linkage/reconstruction/${id}/merge`, {
    method: "POST", body: JSON.stringify({ family_keys: familyKeys ?? null }),
  });
