import { api, authFetch } from "./client";
import { downloadPrivateExport } from "./documents";

export type TreeImportKind = "gedcom7" | "gedzip";
export interface TreeImport {
  id: string; client_id: string; kind: TreeImportKind; filename: string; byte_size: number; checksum: string;
  status: string; error: string | null; review_ready: boolean; created_at: string;
  apply_job_id: string | null; apply_status: string | null; apply_error: string | null;
  result: { individuals: number; families: number; sources: number; citations: number; documents: number } | null;
}
export interface TreeImportReport {
  review_token: string; input_sha256: string; input_bytes: number; format: string;
  counts: Record<string, number>; warnings: string[]; change_counts: Record<string, number>;
  changes: { code: string; record: string | null; path: string }[]; changes_truncated: boolean;
  media: { documents: number; files: number; remote_not_fetched: number; local_not_attached: number;
    alternate_representations_original_only: number; unreferenced_archive_entries: number };
}
export const listTreeImports = () => api<TreeImport[]>("/tree/imports");
export const getTreeImportAudit = (id: string) => api<TreeImportReport>(`/tree/imports/${id}/audit`);
export async function uploadTreeImport(file: File, kind: TreeImportKind, clientId: string): Promise<TreeImport> {
  const query = new URLSearchParams({ filename: file.name, kind, client_id: clientId });
  const response = await authFetch(`/tree/imports?${query}`, { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `No se pudo cargar el archivo (${response.status})`);
  }
  return response.json();
}
export const applyTreeImport = (id: string, report: TreeImportReport, retry = false) => api<TreeImport>(`/tree/imports/${id}/apply`, {
  method: "POST", body: JSON.stringify({ review_token: report.review_token, acknowledge_append: true, retry_failed: retry }),
});
export const downloadTreeImport = (item: TreeImport) => downloadPrivateExport(`/tree/imports/${item.id}/original`,
  `${item.id}.${item.kind === "gedzip" ? "gdz" : "ged"}`, item.byte_size);
