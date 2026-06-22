import { api } from "./client";

export interface JobItem {
  id: string;
  type: string;
  status: string;
  progress: { done?: number; total?: number } | null;
  result: Record<string, unknown> | null;
  error: string | null;
  document_id: string | null;
  created_at: string;
  finished_at: string | null;
}
export const listJobs = () => api<JobItem[]>("/jobs");
export const cancelJob = (id: string) => api<JobItem>(`/jobs/${id}/cancel`, { method: "POST" });

// Live job progress via the SSE endpoint /jobs/{id}/events. EventSource can't send the auth
// header, so we read the text/event-stream with fetch + ReadableStream.
export interface JobEvent {
  kind: string; // book_start | page_ok | page_fail | all_done | book_fail | cancelled
  done?: number;
  total?: number;
  records?: number;
  error?: string;
  [k: string]: unknown;
}

export function streamJob(
  id: string,
  onEvent: (e: JobEvent) => void,
  onDone?: (last?: JobEvent) => void,
): () => void {
  const ctrl = new AbortController();
  (async () => {
    const token = localStorage.getItem("gs_access");
    let res: Response;
    try {
      res = await fetch(`/api/jobs/${id}/events`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: ctrl.signal,
      });
    } catch { onDone?.(); return; }
    if (!res.ok || !res.body) { onDone?.(); return; }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let last: JobEvent | undefined;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop() || "";
        for (const f of frames) {
          const data = f.split("\n").find((l) => l.startsWith("data:"));
          if (!data) continue;
          try {
            const evt = JSON.parse(data.slice(5).trim()) as JobEvent;
            last = evt;
            onEvent(evt);
            if (evt.kind === "all_done" || evt.kind === "book_fail" || evt.kind === "cancelled") {
              onDone?.(evt);
              return;
            }
          } catch { /* keepalive / non-json */ }
        }
      }
    } catch { /* aborted */ }
    onDone?.(last);
  })();
  return () => ctrl.abort();
}
