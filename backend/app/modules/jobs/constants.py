"""Shared job-lifecycle vocabulary.

Single source of truth for job status strings, the statuses that mean "over", and the
status → SSE terminal-event mapping (previously duplicated across the jobs router, the SSE
relay and every task module).
"""
from __future__ import annotations


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"
    CANCELLED = "cancelled"
    PAUSED = "paused"


# Statuses a job can still leave.
ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)
# An execution attempt ends here. Only the ResearchRun controller may explicitly rearm a
# paused/failed stage, retaining its job identity and incrementing the dispatch generation.
TERMINAL_STATUSES = (JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.ERROR, JobStatus.CANCELLED, JobStatus.PAUSED)

# SSE event kinds that mean the job is over — the relay closes the stream on one of these.
TERMINAL_EVENT_KINDS = {"all_done", "book_fail", "cancelled", "error"}

_STATUS_TO_KIND = {
    JobStatus.COMPLETED: "all_done",
    JobStatus.CANCELLED: "cancelled",
    JobStatus.PAUSED: "cancelled",
}


def terminal_event(status: str, progress: dict | None, error: str | None) -> dict:
    """The synthetic final SSE event for a job already in a terminal state (pub/sub has no
    history, so late subscribers get this instead of hanging on keepalives)."""
    prog = progress or {}
    return {
        "kind": _STATUS_TO_KIND.get(status, "book_fail"),
        "done": prog.get("done"), "total": prog.get("total"),
        "error": error, "status": status,
    }
