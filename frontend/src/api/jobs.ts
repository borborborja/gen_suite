import { api, authFetch } from "./client";
import type { Job } from "./types";

export type { Job } from "./types";
// Back-compat alias — new code should import Job from api/types.
export type JobItem = Job;

export const listJobs = () => api<Job[]>("/jobs");
export const getJob = (id: string) => api<Job>(`/jobs/${id}`);
export const cancelJob = (id: string) => api<Job>(`/jobs/${id}/cancel`, { method: "POST" });

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

const TERMINAL_KINDS = new Set(["all_done", "book_fail", "cancelled", "error"]);

// The stream can end without a terminal event (network drop, proxy timeout, worker crash).
// Never report success from a mid-stream event: ask the API for the job's real status and
// synthesize the final event from it, so "completado" is only shown for a completed job.
async function finalEventFromStatus(id: string): Promise<JobEvent | undefined> {
  try {
    const job = await getJob(id);
    const kind =
      job.status === "completed" ? "all_done" :
      job.status === "cancelled" ? "cancelled" :
      job.status === "error" ? "book_fail" : "stream_lost"; // still queued/running
    return { kind, done: job.progress?.done, total: job.progress?.total,
             error: job.error ?? undefined, status: job.status };
  } catch {
    return undefined;
  }
}

// How a finished stream should be reported to the user. Success ONLY on a real all_done —
// a dropped stream or unknown state must never show as "completado".
export function jobOutcome(last?: JobEvent): "ok" | "cancelled" | "error" | "unknown" {
  if (!last) return "unknown";
  if (last.kind === "all_done") return "ok";
  if (last.kind === "cancelled") return "cancelled";
  if (last.kind === "book_fail" || last.kind === "error") return "error";
  return "unknown"; // stream_lost / mid-stream event
}

export function streamJob(
  id: string,
  onEvent: (e: JobEvent) => void,
  onDone?: (last?: JobEvent) => void,
): () => void {
  const ctrl = new AbortController();
  let aborted = false;
  (async () => {
    let res: Response;
    try {
      res = await authFetch(`/jobs/${id}/events`, { signal: ctrl.signal });
    } catch { if (!aborted) onDone?.(await finalEventFromStatus(id)); return; }
    if (!res.ok || !res.body) { onDone?.(await finalEventFromStatus(id)); return; }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
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
            onEvent(evt);
            if (TERMINAL_KINDS.has(evt.kind)) {
              onDone?.(evt);
              return;
            }
          } catch { /* keepalive / non-json */ }
        }
      }
    } catch { if (aborted) return; }
    // Stream ended with no terminal event — resolve against the job's actual status.
    onDone?.(await finalEventFromStatus(id));
  })();
  return () => { aborted = true; ctrl.abort(); };
}
