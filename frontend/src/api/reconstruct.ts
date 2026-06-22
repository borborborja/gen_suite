import { api } from "./client";

export interface ReconPerson {
  key: string;
  given: string | null;
  surname: string | null;
  sex: string;
  birth_year: number | null;
  death_year: number | null;
  mention_ids: string[];
  record_ids: string[];
  existing_person_id?: string;
}
export interface ReconFamily {
  key: string;
  husband_key: string | null;
  wife_key: string | null;
  child_keys: string[];
  record_ids: string[];
}
export interface ReconGraph { persons: ReconPerson[]; families: ReconFamily[] }
export interface ReconStats { persons: number; families: number; generations: number; linked_to_existing: number }

export interface Reconstruction {
  id: string;
  status: string;
  conservative: boolean;
  include_census: boolean;
  link_to_tree: boolean;
  graph: ReconGraph | null;
  stats: ReconStats | null;
  job_id: string | null;
}

export const reconstruct = (body: { conservative: boolean; include_census: boolean; link_to_tree: boolean }) =>
  api<Reconstruction>("/linkage/reconstruct", { method: "POST", body: JSON.stringify(body) });

export const getLatestReconstruction = () =>
  api<Reconstruction | null>("/linkage/reconstruction/latest");

export const mergeReconstruction = (id: string, family_keys?: string[]) =>
  api<{ persons: number; families: number }>(`/linkage/reconstruction/${id}/merge`, {
    method: "POST", body: JSON.stringify({ family_keys: family_keys ?? null }),
  });

// Frontend-only preference (Ajustes toggle) for conservative super-discovery.
export const CONSERVATIVE_KEY = "gs_recon_conservative";
export const getConservative = () => localStorage.getItem(CONSERVATIVE_KEY) !== "false";
export const setConservative = (v: boolean) => localStorage.setItem(CONSERVATIVE_KEY, String(v));
