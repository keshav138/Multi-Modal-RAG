"""
Tests for the /ingest endpoints (multipart uploads + background tasks).

The client sends raw files only: PDFs for /ingest/pdf (the backend extracts
text chunks AND figures from each PDF), audio files for /ingest/audio.
Uploaded files are saved into ``settings.upload_dir`` (monkeypatched to a
tmp dir) and handed to the fake IngestionService by a BackgroundTask.
Starlette's TestClient runs background tasks synchronously before returning,
so the job is already completed when the response arrives.
"""

from app.config import settings


def test_pdf_upload_triggers_background_ingestion(client, fake_services, tmp_path, monkeypatch):
    """Uploading multiple PDFs saves them all and schedules full ingestion."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))

    response = client.post(
        "/ingest/pdf",
        files=[
            ("files", ("trane_manual.pdf", b"%PDF-1.4 fake pdf bytes", "application/pdf")),
            ("files", ("goodman_manual.pdf", b"%PDF-1.4 another fake pdf", "application/pdf")),
        ],
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    # Both PDFs were saved under upload_dir/<job_id>/ with their original names.
    job_dir = tmp_path / "uploads" / job_id
    assert (job_dir / "trane_manual.pdf").exists()
    assert (job_dir / "goodman_manual.pdf").exists()

    # The service receives the list of saved paths — one call, both modalities.
    modality, paths = fake_services["ingestion"].calls[0]
    assert modality == "pdf"
    assert set(paths) == {
        str(job_dir / "trane_manual.pdf"),
        str(job_dir / "goodman_manual.pdf"),
    }

    # The background task has already run, so the job shows completed with
    # the combined count (text chunks + image records).
    status = client.get(f"/ingest/status/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "completed"
    assert body["chunks_processed"] == 10  # 7 text + 3 images
    assert body["error"] is None


def test_pdf_upload_rejects_non_pdf(client, tmp_path, monkeypatch):
    """Only .pdf files are accepted by /ingest/pdf."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    response = client.post(
        "/ingest/pdf",
        files={"files": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400


def test_audio_upload_saves_all_files(client, fake_services, tmp_path, monkeypatch):
    """Multiple audio files are saved into one job dir and ingested together."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))

    response = client.post(
        "/ingest/audio",
        files=[
            ("files", ("call_1.wav", b"RIFF fake wav", "audio/wav")),
            ("files", ("call_2.mp3", b"ID3 fake mp3", "audio/mpeg")),
        ],
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    job_dir = tmp_path / "uploads" / job_id
    assert (job_dir / "call_1.wav").exists()
    assert (job_dir / "call_2.mp3").exists()

    # The service receives the job directory, not a single file.
    assert fake_services["ingestion"].calls[0] == ("audio", [str(job_dir)])

    status = client.get(f"/ingest/status/{job_id}")
    assert status.json()["status"] == "completed"
    assert status.json()["chunks_processed"] == 5


def test_status_unknown_job_returns_404(client):
    """Polling a job_id that was never created returns 404."""
    response = client.get("/ingest/status/does-not-exist")
    assert response.status_code == 404
