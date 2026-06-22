import { api } from "./client";

export interface ExternalSource {
  key: string;
  name: string;
  category: string;
  category_label: string;
  region: string | null;
  note: string | null;
  url: string;
}

export function listSources(p: { given?: string; surname?: string; place?: string; year_from?: string; year_to?: string; region?: string }) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v) qs.set(k, v);
  return api<ExternalSource[]>(`/sources?${qs.toString()}`);
}
