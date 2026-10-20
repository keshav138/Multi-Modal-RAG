"""
Ingest schemas — response models for the /ingest endpoints.

The endpoints accept **multipart file uploads** (not JSON bodies), per the
project decision that the API owns the files it ingests.  The client sends
raw files only:

- /ingest/pdf   — one or more PDFs (text chunks AND figures are extracted
                  server-side from each PDF)
- /ingest/audio — one or more audio recordings

Uploaded files are saved under ``settings.upload_dir`` (extracted images go
to ``settings.image_dir`` so they are servable via /static/images) and then
processed by a FastAPI BackgroundTask.

Ingestion is long-running (PDF chunking, Whisper transcription, embedding),
so the API accepts a job and returns 202 immediately with a job_id.  The
status endpoint lets clients poll for completion.
"""

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    """Immediate acknowledgement that an ingestion job has been accepted."""

    job_id: str
    status: str = "accepted"
    message: str = Field(
        default="Job accepted — processing runs in the background. "
        "Poll GET /ingest/status/{job_id} for progress.",
        description="Human-readable acknowledgement message.",
    )


class IngestStatusResponse(BaseModel):
    """Current state of a running or completed ingestion job."""

    job_id: str
    status: str = Field(
        description="One of: 'accepted', 'completed', 'failed'.",
    )
    chunks_processed: int | None = Field(
        default=None,
        description="Number of chunks/records stored, set once the job completes.",
    )
    error: str | None = Field(
        default=None,
        description="Exception message if the job failed, otherwise None.",
    )
