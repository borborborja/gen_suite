import { api } from "./client";
import { downloadPrivateExport } from "./documents";
import type { GedcomExportAudit, TreeExport, TreeExportKind } from "./types";

export const listTreeExports = () => api<TreeExport[]>("/tree/exports");
export const createTreeExport = (kind: TreeExportKind) => api<{ id: string; status: string; kind: TreeExportKind }>(
  "/tree/exports", { method: "POST", body: JSON.stringify({ kind }) });
export const getTreeExportAudit = (id: string) => api<GedcomExportAudit>(`/tree/exports/${id}/audit`);
export const downloadTreeExport = (item: TreeExport, report: GedcomExportAudit) => {
  if (item.byte_size == null || item.checksum !== report.artifact_sha256 || item.byte_size !== report.artifact_byte_size)
    return Promise.reject(new Error("El informe no corresponde a este archivo; vuelve a revisarlo"));
  return downloadPrivateExport(`/tree/exports/${item.id}/content?review_token=${encodeURIComponent(report.review_token)}`,
    `${item.id}.${item.kind === "gedzip" ? "gdz" : "ged"}`, item.byte_size);
};
