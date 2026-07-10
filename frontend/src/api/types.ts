// Shared API types. One Job shape for every module — the backend returns the same JobOut
// envelope from /jobs, /extraction, /transcription, /linkage and /providers.

export interface JobProgress {
  done?: number;
  total?: number;
  errors?: number;
  phase?: string;
  records?: number;
  failed?: number;
}

export interface Job {
  id: string;
  type: string;
  status: string; // queued | running | completed | error | cancelled
  progress: JobProgress | null;
  result: Record<string, unknown> | null;
  error: string | null;
  document_id?: string | null; // only present on the /jobs listing
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}
