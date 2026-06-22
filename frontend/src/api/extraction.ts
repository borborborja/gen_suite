// Client for the record-extraction API (plan §2). Mirrors api/transcription.ts: start a job to
// turn a document's transcriptions into structured records + person mentions, then list them.
import { api } from "./client";
import type { JobOut } from "./linkage";

export interface MentionLite {
  role: string;
  given: string | null;
  surname: string | null;
  name_raw: string | null;
  sex: string | null;
}

export interface ExtractedRecordOut {
  id: string;
  record_type: string;
  date_raw: string | null;
  date_year: number | null;
  date_month: number | null;
  date_day: number | null;
  summary: string | null;
  place: string | null;
  confidence: number | null;
  status: string;
  page_id: string | null;
  page_end_id: string | null;     // second sheet when the entry spans pages
  is_continued: boolean;
  record_no: string | null;       // entry number as written
  sequence_warning: string | null; // gap/duplicate/out-of-order note
  mentions: MentionLite[];        // the people named in this act (role + name)
}

export interface ExtractRequest {
  document_id: string;
  engine?: string;
  model?: string;
  credential_id?: string;
  api_key?: string;
  base_url?: string;
  modality?: "sync" | "batch";  // Batch API: async, ~50% cheaper
}

export const startExtraction = (body: ExtractRequest) =>
  api<JobOut>("/extraction/jobs", { method: "POST", body: JSON.stringify(body) });

export const cancelExtraction = (jobId: string) =>
  api<JobOut>(`/extraction/jobs/${jobId}/cancel`, { method: "POST" });

export const documentRecords = (documentId: string) =>
  api<ExtractedRecordOut[]>(`/extraction/documents/${documentId}`);

// Re-extract a corrected transcription (supersedes old records, re-runs extraction).
export const reextract = (transcriptionId: string) =>
  api<JobOut>(`/extraction/reextract/${transcriptionId}`, { method: "POST" });

// Manually join a record with the first record on the next page (when auto-stitch missed a split).
export const mergeNext = (recordId: string) =>
  api<ExtractedRecordOut>(`/extraction/records/${recordId}/merge-next`, { method: "POST" });

// Unlink a spanning record from its second page.
export const splitRecord = (recordId: string) =>
  api<ExtractedRecordOut>(`/extraction/records/${recordId}/split`, { method: "POST" });
