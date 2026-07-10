import { api, authFetch } from "./client";

export interface DocumentOut {
  id: string;
  title: string;
  doc_type: string;
  visibility: string;
  source_kind: string;
  page_count: number;
  rights_declaration: string | null;
  image_policy: string; // retain | data_only
  may_contain_living: boolean;
  source_origin: string | null;
  source_ref: string | null;        // external origin (e.g. FamilySearch URL)
  derived_from_id: string | null;   // parent document for a derived (compacted) one
  default_record_type: string | null;
  pending_job_id: string | null;
  year_from: number | null;
  year_to: number | null;
  book_number: number | null;       // ordinal within the parish series
  is_index: boolean;
  indexes_for_id: string | null;
  created_at: string;
}
export interface PageOut {
  id: string;
  page_no: number;
  folio_label: string | null;       // the page's own printed/written number ("23v")
  kind: string;                     // record | index | cover | blank
  content_type: string | null;
  width: number | null;
  height: number | null;
  byte_size: number | null;
  source_ref: string | null;        // exact image origin (e.g. FamilySearch ARK)
}

export interface UploadFlags {
  visibility?: string;
  rights?: string;
  source_origin?: string;
  may_contain_living?: boolean;
  image_policy?: "retain" | "data_only";
  record_type?: string;
  municipality?: string;
  municipality_lat?: number;
  municipality_lng?: number;
  year_from?: number;
  year_to?: number;
  book_number?: number;
  is_index?: boolean;
}

export interface RecordType { key: string; label: string; family: string }
export const getRecordTypes = () => api<RecordType[]>("/extraction/record-types");

export interface SeriesGap {
  place_id: string | null;
  place_name: string | null;
  record_type: string | null;
  present: number[];
  missing: number[];
  books: { book_number: number; id: string; title: string }[];
}
export const getSeriesGaps = () => api<SeriesGap[]>("/documents/series-gaps");

export const listDocuments = (scope: "mine" | "public" | "all" = "mine") =>
  api<DocumentOut[]>(`/documents?scope=${scope}`);
export const getDocument = (id: string) => api<DocumentOut>(`/documents/${id}`);
export const getPages = (id: string) => api<PageOut[]>(`/documents/${id}/pages`);
export const publishDoc = (id: string, rights: string) =>
  api<DocumentOut>(`/documents/${id}/publish`, {
    method: "POST",
    body: JSON.stringify({ rights_declaration: rights }),
  });
export const unpublishDoc = (id: string) =>
  api<DocumentOut>(`/documents/${id}/unpublish`, { method: "POST" });
export const deleteDoc = (id: string) => api(`/documents/${id}`, { method: "DELETE" });
export const discardImages = (id: string) =>
  api<DocumentOut>(`/documents/${id}/discard-images`, { method: "POST" });
export const compactPdf = (id: string) =>
  api<{ id: string; status: string }>(`/documents/${id}/compact-pdf`, { method: "POST" });

export async function uploadDocument(
  title: string,
  files: FileList | File[],
  flags: UploadFlags = {},
): Promise<DocumentOut> {
  const fd = new FormData();
  fd.append("title", title);
  fd.append("visibility", flags.visibility || "private");
  if (flags.rights) fd.append("rights_declaration", flags.rights);
  if (flags.source_origin) fd.append("source_origin", flags.source_origin);
  fd.append("may_contain_living", String(!!flags.may_contain_living));
  fd.append("image_policy", flags.image_policy || "retain");
  if (flags.record_type) fd.append("record_type", flags.record_type);
  if (flags.municipality) fd.append("municipality", flags.municipality);
  if (flags.municipality_lat != null) fd.append("municipality_lat", String(flags.municipality_lat));
  if (flags.municipality_lng != null) fd.append("municipality_lng", String(flags.municipality_lng));
  if (flags.year_from != null) fd.append("year_from", String(flags.year_from));
  if (flags.year_to != null) fd.append("year_to", String(flags.year_to));
  if (flags.book_number != null) fd.append("book_number", String(flags.book_number));
  if (flags.is_index) fd.append("is_index", "true");
  for (const f of Array.from(files)) fd.append("files", f);
  const res = await authFetch("/documents", { method: "POST", body: fd });
  if (!res.ok) throw new Error((await res.text()) || `${res.status}`);
  return res.json();
}

export async function fetchPageObjectUrl(id: string, pageNo: number, thumb = false): Promise<string> {
  const res = await authFetch(`/documents/${id}/pages/${pageNo}/content${thumb ? "?thumb=1" : ""}`);
  if (!res.ok) throw new Error(`${res.status}`);
  return URL.createObjectURL(await res.blob());
}

export const setPageKind = (id: string, pageNo: number, kind: string) =>
  api<PageOut>(`/documents/${id}/pages/${pageNo}/kind`, { method: "PATCH", body: JSON.stringify({ kind }) });

export interface IndexEntry { id: string; name_raw: string | null; folio_label: string | null; record_no: string | null; year: number | null; matched: boolean | null }
export interface IndexReport { entries: IndexEntry[]; total: number; matched: number; missing: number }
export const parseIndex = (id: string) =>
  api<{ id: string; status: string }>(`/documents/${id}/parse-index`, { method: "POST" });
export const getDocumentIndex = (id: string) => api<IndexReport>(`/documents/${id}/index`);

export const splitDocument = (id: string, breaks: number[], books?: Record<string, unknown>[]) =>
  api<DocumentOut[]>(`/documents/${id}/split`, { method: "POST", body: JSON.stringify({ breaks, books }) });

export interface RasterSettings { dpi: number; format: string; autosplit: boolean }
export const getRasterSettings = () => api<RasterSettings>("/documents/raster-settings");
export const setRasterSettings = (s: RasterSettings) =>
  api<RasterSettings>("/documents/raster-settings", { method: "PUT", body: JSON.stringify(s) });
export const rerasterize = (id: string) =>
  api<{ id: string; status: string }>(`/documents/${id}/rerasterize`, { method: "POST" });
