"""In-process identity only; the database remains authoritative for status and budget."""
from contextvars import ContextVar

current_job: ContextVar[tuple | None] = ContextVar("current_research_job", default=None)
