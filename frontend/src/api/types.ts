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

export interface IngestOptions {
  source_origin?: string;
  may_contain_living?: boolean;
  record_type?: string | null;
  municipality?: string | null;
  municipality_lat?: number | null;
  municipality_lng?: number | null;
  year_from?: number | null;
  year_to?: number | null;
  book_number?: number | null;
  is_index?: boolean;
}
export interface IngestSession {
  id: string; title: string; status: string;
  expected_files: number; declared_files: number;
  reserved_bytes: number; declared_bytes: number; received_bytes: number;
  job_id: string | null; chunk_bytes: number; created_at: string; options: IngestOptions;
  job_status?: string | null; job_error?: string | null;
  updated_at?: string; inactive_candidate?: boolean;
}
export interface IngestCleanupPreview {
  session_id: string; title: string; status: string; files: number; published_books: number;
  mode: "compact" | "discard"; received_bytes: number; reserved_bytes: number;
  retained_reservation_bytes: number; released_reservation_bytes: number; preview_hash: string;
}
export interface IngestCleanup {
  id: string; session_id: string; job_id: string; mode: "compact" | "discard"; phase: string;
  job_status: string; error: string | null; inventory_epoch: number;
  planned_objects: number; planned_bytes: number; deleted_objects: number; deleted_bytes: number;
  retained_reservation_bytes: number; reason: string; created_by: string; created_at: string; completed_at: string | null;
}
export interface IngestEntry {
  path: string; group: string; title: string; kind: "pdf" | "image_set";
  position: number; byte_size: number; last_modified: number; options: IngestOptions;
}
export interface IngestFile {
  id: string; book_id: string; path: string; position: number; byte_size: number; last_modified: number;
  received_bytes: number; status: string; checksum: string | null; error: string | null;
  parts?: Record<string, { sha256: string; size: number }>;
}
export interface IngestBook {
  id: string; group: string; title: string; kind: "pdf" | "image_set"; options: IngestOptions;
  status: string; error: string | null; document_id: string | null;
}
export interface AiCall {
  id: string;
  job_id: string | null;
  task_type: string;
  state: "inflight" | "unknown" | "succeeded" | "reconciled";
  profile: Record<string, unknown>;
  reserved_microusd: number;
  cost_microusd: number | null;
  provider_request_id: string | null;
  error_code: string | null;
  created_at: string;
  settled_at: string | null;
  reconciliation: Record<string, unknown> | null;
}
export type BillingScope = "research_run" | "embedding_generation" | "job";
export type BillingTask = "transcription" | "inference" | "embedding";
export interface BillingPolicy {
  model: string; context_tokens: number; max_output_tokens: number;
  input_usd_per_million: string; output_usd_per_million: string; valid_until: string; source: string;
  output_parameter: string; response_format: string;
}
export interface BillingOwner { scope: BillingScope; id: string; title: string; status: string; version: string | number | null }
export interface BillingAuthorization {
  id: string; version: number; task_type: BillingTask; scope: BillingScope; target_id: string;
  base_profile_hash: string; budget_policy: BillingPolicy; created_by: string; created_at: string; reason: string;
}
export interface BillingHistory {
  owner: BillingOwner; execution_profile: { engine: string; model: string | null };
  current_policy: BillingPolicy; expired: boolean; authorization_version: number; history: BillingAuthorization[];
}
export interface BillingProposal {
  scope: BillingScope; target_id: string; task_type: BillingTask;
  input_usd_per_million: string; output_usd_per_million: string; valid_until: string; source: string;
}
export interface BillingPreview {
  owner: BillingOwner; task_type: BillingTask; base_profile_hash: string; authorization_version: number;
  before: BillingPolicy; after: BillingPolicy; preview_hash: string;
  before_reservation_microusd: number; after_reservation_microusd: number;
}
export interface ResearchExecution {
  id: string; title: string; project_id: string | null; version: number;
  status: "planned" | "running" | "paused" | "needs_attention" | "completed" | "cancelled";
  budget_microusd: number; spent_microusd: number; reserved_microusd: number;
  profiles: Record<string, { engine: string; model: string | null; budget_policy?: Record<string, unknown> }>;
  config: Record<string, unknown>; stage_counts: Record<string, number>;
  document_count: number; person_count: number; error: string | null;
  created_at: string; finished_at: string | null;
}
export interface ResearchStage {
  id: string; position: number; kind: string; title: string; status: string;
  document_id: string | null; person_id: string | null; job_id: string | null;
  attempts: number; result: Record<string, unknown> | null; error: string | null;
}
export interface ResearchRunEvent {
  id: string; kind: string; actor_id: string | null; detail: Record<string, unknown>; created_at: string;
}

export interface StorageInventorySummary {
  id: string; job_id: string; job_status: string | null; phase: string; bucket_role: "private" | "public";
  reason: string; created_by: string; created_at: string; started_at: string | null; completed_at: string | null;
  invalidated_at: string | null; storage_configuration_matches: boolean; error: string | null;
  consistent_snapshot: false; quota_enforced: false; measurement: "observed_payload_bytes";
}
export interface StorageInventory extends StorageInventorySummary {
  observed_payload_bytes: number; listing_finished: boolean; upload_inspection_complete: boolean;
  counts: { current_versions: number; noncurrent_versions: number; delete_markers: number;
    multipart_uploads: number; multipart_parts: number; uninspected_uploads: number };
  bytes: { current_versions: number; noncurrent_versions: number; multipart_parts: number };
  groups: { bucket_role: "private" | "public"; category: string; payload_bytes: number; versions: number;
    delete_markers: number; multipart_uploads: number; multipart_parts: number }[];
}

export interface StorageQuota {
  phase: "uninitialized" | "ready" | "draining" | "counting"; used_bytes: number; reserved_bytes: number;
  limit_bytes: number; pending_writes: number; location_matches: boolean; baseline_at: string | null;
  quota_enforced: true; measurement: "conservative_payload_bytes"; scan: StorageInventory | null;
}
export interface StorageWrite {
  id: string; kind: string; state: string; reserved_bytes: number; actual_bytes: number | null;
  job_id: string | null; created_at: string; finished_at: string | null; error: string | null;
}
export interface StorageCancellationPreview {
  write_id: string; kind: string; state: "reserved"; reserved_bytes: number; job_id: string | null;
  ai_call_id: string | null; ai_state: string | null; requires_late_response_loss_ack: boolean;
  reason: string; preview_hash: string; storage_changed: false; job_cancelled: false;
}
export interface StorageWriteAction {
  id: string; write_id: string; created_by: string; created_at: string; reason: string; released_bytes: number;
  evidence: StorageCancellationPreview & { after_state: "cancelled"; late_response_loss_ack: boolean };
}
export interface GedcomExportAudit {
  format: string;
  scope: "genealogy_interchange";
  is_backup: false;
  sha256: string;
  review_token: string;
  counts: { persons: number; families: number; sources: number; repositories: number; citations: number; citations_exported: number };
  extensions: string[];
  excluded_records: Record<string, number>;
  unattached_citations: number;
  relocated_child_citations: number;
  raw_structures: number;
  unresolved_raw_references: number;
  warnings: string[];
  snapshot?: { isolation: string; captured_at: string };
  artifact_sha256?: string;
  artifact_byte_size?: number;
  media_documents?: number;
  media_files?: number;
  conversions?: { date_phrases: number; legacy_structures: number; name_type_phrases: number };
}

export type TreeExportKind = "gedcom551" | "gedcom7" | "gedzip";
export interface TreeExport {
  id: string; kind: TreeExportKind; status: string; created_at: string;
  progress: { stage?: string; done?: number; total?: number } | null;
  error: string | null; checksum: string | null; byte_size: number | null;
}
