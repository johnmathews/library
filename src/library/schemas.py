"""Pydantic response schemas for the HTTP API."""

from datetime import datetime

from pydantic import BaseModel

from library.models import DocumentStatus


class DocumentUploadResponse(BaseModel):
    """Body returned by POST /api/documents (201 created, 200 duplicate)."""

    id: int
    sha256: str
    status: DocumentStatus
    duplicate: bool


class JobInfo(BaseModel):
    """One row from the procrastinate_jobs table, as exposed by GET /api/jobs."""

    id: int
    status: str
    task_name: str
    attempts: int
    scheduled_at: datetime | None
    document_id: int | None
