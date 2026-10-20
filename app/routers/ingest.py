"""
Ingestion endpoints for the Multi Modal RAG API.

Two POST routes accept **multipart file uploads** and run them as
background tasks (FastAPI BackgroundTasks — no external queue):

- /ingest/pdf   — one or more PDFs.  The backend extracts BOTH modalities:
                  text is chunked (scripts/text-extraction) and embedded
                  with BGE, figures are cropped (scripts/image-extraction)
                  into IMAGE_DIR and embedded with SigLIP.
- /ingest/audio — one or more audio files; each is transcribed with Whisper,
                  chunked, embedded, and stored (modality='audio').

Clients never provide extracted artifacts (chunks, metadata JSON, cropped
images) — raw files in, all extraction happens server-side.

Uploaded files are saved under ``settings.upload_dir/<job_id>/`` before the
background task starts, so the request can return immediately.  Extracted
images are placed in ``settings.image_dir`` so they are servable through
/static/images for citations.

Each endpoint returns HTTP 202 immediately with a job_id.  The caller can
poll GET /ingest/status/{job_id} to check progress.

Job state is tracked in a simple in-memory dict on app.state.jobs.
This is intentionally kept simple — for production, swap in Redis or a DB table.

No authentication is enforced on these endpoints (internal-service design).
"""

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.metrics import rag_ingestion_chunks_total
from app.schemas.ingest import IngestResponse, IngestStatusResponse

router = APIRouter(prefix="/ingest", tags=["ingestion"])


# ── Upload helpers ────────────────────────────────────────────────────
# Every uploaded filename is reduced to its basename so a malicious client
# can't traverse directories with names like "../../etc/passwd".


def _save_upload(upload: UploadFile, dest_dir: Path) -> Path:
    """
    Write an uploaded file into ``dest_dir`` and return the saved path.

    Raises HTTPException(400) if the upload has no filename, and
    HTTPException(500) if the file cannot be written.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "").name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Uploaded file has no filename.")

    dest = dest_dir / safe_name
    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save upload: {exc}",
        ) from exc
    return dest


def _save_pdf_uploads(files: list[UploadFile], job_dir: Path) -> list[str]:
    """Validate that every upload is a PDF, save them, and return the paths."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF is required.")

    saved: list[str] = []
    for upload in files:
        if not (upload.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"'{upload.filename}' is not a PDF — only .pdf files are accepted.",
            )
        saved.append(str(_save_upload(upload, job_dir)))
    return saved


# ── Background task helpers ───────────────────────────────────────────
# Each helper wraps the service call, catches exceptions, updates the
# shared jobs dict, and increments the per-modality ingestion counter.


def _run_pdf_ingestion(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    ingestion_service: Any,
    pdf_paths: list[str],
) -> None:
    """Background worker for PDF ingestion (text chunks + extracted figures)."""
    try:
        text_count, image_count = ingestion_service.ingest_pdfs(pdf_paths)
        rag_ingestion_chunks_total.labels(modality="text").inc(text_count)
        rag_ingestion_chunks_total.labels(modality="image").inc(image_count)
        jobs[job_id] = {
            "status": "completed",
            "chunks_processed": text_count + image_count,
            "error": None,
        }
    except Exception as exc:
        jobs[job_id] = {"status": "failed", "chunks_processed": 0, "error": str(exc)}


def _run_audio_ingestion(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    ingestion_service: Any,
    audio_dir: str,
) -> None:
    """Background worker for audio ingestion."""
    try:
        count = ingestion_service.ingest_audio(audio_dir)
        rag_ingestion_chunks_total.labels(modality="audio").inc(count)
        jobs[job_id] = {"status": "completed", "chunks_processed": count, "error": None}
    except Exception as exc:
        jobs[job_id] = {"status": "failed", "chunks_processed": 0, "error": str(exc)}


def _start_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    background_tasks: BackgroundTasks,
    worker: Any,
    *worker_args: Any,
) -> None:
    """Create a job entry for ``job_id`` and schedule the worker."""
    jobs[job_id] = {"status": "accepted", "chunks_processed": 0, "error": None}
    background_tasks.add_task(worker, jobs, job_id, *worker_args)


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/pdf", response_model=IngestResponse, status_code=202)
def ingest_pdf(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="One or more PDF manuals."),
) -> IngestResponse:
    """
    Accept one or more PDF uploads and ingest them in the background.

    The backend extracts both modalities from each PDF: text is chunked
    (scripts/text-extraction) and embedded with BGE, figures are cropped
    (scripts/image-extraction) and embedded with SigLIP.  The client does
    not choose between text and image ingestion.
    """
    job_id = str(uuid.uuid4())
    job_dir = Path(settings.upload_dir) / job_id
    pdf_paths = _save_pdf_uploads(files, job_dir)

    ingestion_service = request.app.state.ingestion_service
    _start_job(
        request.app.state.jobs, job_id, background_tasks, _run_pdf_ingestion,
        ingestion_service, pdf_paths,
    )
    return IngestResponse(job_id=job_id)


@router.post("/audio", response_model=IngestResponse, status_code=202)
def ingest_audio(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="One or more audio recordings."),
) -> IngestResponse:
    """
    Accept one or more audio uploads and process them in the background.

    Each file is transcribed with Whisper, chunked, embedded, and stored
    with modality='audio' in the same table as text chunks.
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one audio file is required.")

    job_id = str(uuid.uuid4())
    job_dir = Path(settings.upload_dir) / job_id
    for upload in files:
        _save_upload(upload, job_dir)

    ingestion_service = request.app.state.ingestion_service
    _start_job(
        request.app.state.jobs, job_id, background_tasks, _run_audio_ingestion,
        ingestion_service, str(job_dir),
    )
    return IngestResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=IngestStatusResponse)
def ingest_status(job_id: str, request: Request) -> JSONResponse:
    """
    Poll the status of an ingestion job by its job_id.

    Possible statuses: accepted, completed, failed.
    Returns 404 if the job_id is unknown.
    """
    jobs: dict = request.app.state.jobs
    job = jobs.get(job_id)

    if job is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Job {job_id} not found"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "job_id": job_id,
            "status": job["status"],
            "chunks_processed": job["chunks_processed"],
            "error": job["error"],
        },
    )
