"""
ingestion.py — service for ingesting text, images, and audio into pgvector.

The user-facing contract is raw files, not extracted artifacts:

- PDFs  → text chunks   via scripts/text-extraction/chunk-text.py
- PDFs  → figures       via scripts/image-extraction (crops figures + captions)
- audio → transcript    via pipelines/transcribe_audio.py (Whisper)

Extraction runs server-side inside this service, then the results are
embedded and stored exactly as the standalone CLI pipelines do (the same
``save_chunks`` / ``save_images`` helpers from pipelines/ are reused), so
the tables stay byte-compatible with CLI runs.

Models (SentenceTransformer, SigLIP, Whisper) are loaded lazily on first use
to avoid paying startup cost for models that may never be needed.
"""

import importlib.util
import logging
import sys
from pathlib import Path

import torch
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool
from sentence_transformers import SentenceTransformer
from transformers import AutoModel, AutoProcessor

from app.config import settings
from pipelines.ingest_images import create_table as create_image_table
from pipelines.ingest_images import embed_images, save_images
from pipelines.ingest_pgvector import create_table as create_text_table
from pipelines.ingest_pgvector import save_chunks

logger = logging.getLogger(__name__)


def _load_script_module(script_path: Path, module_name: str):
    """
    Import a single-file script from ``scripts/`` by path.

    importlib is used because the script directories contain hyphens
    ("text-extraction", "image-extraction"), which makes them impossible to
    import with a normal `import` statement — Python identifiers can't
    contain hyphens.
    """
    spec = importlib.util.spec_from_file_location(module_name, str(script_path))
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules so any internal imports in the script resolve
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class IngestionService:
    """
    Coordinates ingestion of text, images, and audio into the vector database.

    Models are loaded lazily — only when a particular ingest method is called
    for the first time — to avoid loading Whisper or SigLIP weights when
    the server only needs to serve queries.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # Lazily initialised models (set to None until first use)
        self._text_model: SentenceTransformer | None = None
        self._siglip_model = None
        self._siglip_processor = None
        self._whisper_model = None
        self._chunk_text_module = None
        self._figure_extraction_module = None

    # ------------------------------------------------------------------
    # Lazy model loaders
    # ------------------------------------------------------------------

    @property
    def text_model(self) -> SentenceTransformer:
        """Text embedding model, loaded on first access."""
        if self._text_model is None:
            logger.info("Loading text embedding model: %s", settings.text_embed_model)
            self._text_model = SentenceTransformer(settings.text_embed_model)
        return self._text_model

    @property
    def siglip_model(self):
        """SigLIP image encoder, loaded on first access."""
        if self._siglip_model is None:
            logger.info("Loading SigLIP model: %s", settings.image_embed_model)
            self._siglip_model = AutoModel.from_pretrained(
                settings.image_embed_model,
            ).to(self.device).eval()
            self._siglip_processor = AutoProcessor.from_pretrained(
                settings.image_embed_model,
            )
        return self._siglip_model

    @property
    def siglip_processor(self):
        """SigLIP processor — triggers model load if not already done."""
        if self._siglip_processor is None:
            # Access siglip_model to trigger the joint load
            _ = self.siglip_model
        return self._siglip_processor

    @property
    def whisper_model(self):
        """Whisper model for audio transcription, loaded on first access."""
        if self._whisper_model is None:
            # Import here so the server can start without faster-whisper
            # installed when audio ingestion is not needed
            from faster_whisper import WhisperModel

            logger.info("Loading Whisper model: %s", settings.whisper_model)
            self._whisper_model = WhisperModel(
                settings.whisper_model, device="cpu", compute_type="auto",
            )
        return self._whisper_model

    @property
    def chunk_text_module(self):
        """The chunk-text extraction module, loaded via importlib on first access."""
        if self._chunk_text_module is None:
            script_path = (
                Path(__file__).resolve().parents[1]
                / "scripts" / "text-extraction" / "chunk-text.py"
            )
            self._chunk_text_module = _load_script_module(script_path, "chunk_text")
        return self._chunk_text_module

    @property
    def figure_extraction_module(self):
        """The figure-extraction module, loaded via importlib on first access."""
        if self._figure_extraction_module is None:
            script_path = (
                Path(__file__).resolve().parents[1]
                / "scripts" / "image-extraction" / "image-extraction-claude-new.py"
            )
            self._figure_extraction_module = _load_script_module(
                script_path, "figure_extraction",
            )
        return self._figure_extraction_module

    # ------------------------------------------------------------------
    # Connection helper
    # ------------------------------------------------------------------

    def _get_conn(self):
        """Borrow a connection from the pool with pgvector registered."""
        conn = self.pool.getconn()
        register_vector(conn)
        return conn

    # ------------------------------------------------------------------
    # Ingestion methods
    # ------------------------------------------------------------------

    def ingest_pdfs(self, pdf_paths: list[str]) -> tuple[int, int]:
        """
        Ingest every uploaded PDF twice: text chunks AND extracted figures.

        This is the only PDF path — the API never exposes the internal
        text/image split.  For each PDF:

        1. scripts/text-extraction chunks the pages → BGE → text_chunks.
        2. scripts/image-extraction crops figures + captions into
           settings.image_dir → SigLIP → image_chunks.

        Both reuse the exact extraction code and table helpers of the CLI
        pipelines, so results are byte-compatible with standalone runs.

        Args:
            pdf_paths: Paths to PDF files already saved on disk.

        Returns:
            (text_chunks, image_records) counts ingested.
        """
        chunk_pdf = self.chunk_text_module.process_pdf
        extract_figures = self.figure_extraction_module.process_pdf

        image_dir = Path(settings.image_dir).resolve()
        image_dir.mkdir(parents=True, exist_ok=True)

        chunks: list[dict] = []
        records: list[dict] = []
        for path in pdf_paths:
            pdf_path = Path(path)
            pdf_chunks = chunk_pdf(pdf_path)
            pdf_records, num_pages = extract_figures(pdf_path, image_dir)
            chunks.extend(pdf_chunks)
            records.extend(pdf_records)
            logger.info(
                "%s -> %d text chunks, %d figures (%d pages)",
                pdf_path.name, len(pdf_chunks), len(pdf_records), num_pages,
            )

        text_count = self._store_text_chunks(chunks)
        image_count = self._store_image_records(records)
        return text_count, image_count

    def _store_text_chunks(self, chunks: list[dict], batch_size: int = 64) -> int:
        """Embed text chunks with BGE and upsert them into text_chunks."""
        if not chunks:
            return 0

        dim = self.text_model.get_sentence_embedding_dimension()

        conn = self._get_conn()
        try:
            conn.autocommit = True
            create_text_table(conn, dim)

            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                vectors = self.text_model.encode(
                    [c["text"] for c in batch],
                    normalize_embeddings=True,
                    batch_size=batch_size,
                )
                save_chunks(conn, batch, vectors)
                logger.info("Text ingestion: %d / %d chunks", start + len(batch), len(chunks))
        finally:
            self.pool.putconn(conn)

        return len(chunks)

    def _store_image_records(self, records: list[dict], batch_size: int = 16) -> int:
        """Embed extracted figure images with SigLIP and upsert into image_chunks."""
        if not records:
            return 0

        conn = self._get_conn()
        try:
            conn.autocommit = True
            table_ready = False

            for start in range(0, len(records), batch_size):
                batch = records[start : start + batch_size]
                vectors = embed_images(
                    self.siglip_model,
                    self.siglip_processor,
                    [r["image_path"] for r in batch],
                    self.device,
                )

                if not table_ready:
                    # Learn vector dimensionality from the first batch
                    create_image_table(conn, vectors.shape[1])
                    # Re-register after table creation so the vector type is known
                    register_vector(conn)
                    table_ready = True

                save_images(conn, batch, vectors)
                logger.info("Image ingestion: %d / %d records", start + len(batch), len(records))
        finally:
            self.pool.putconn(conn)

        return len(records)

    def ingest_audio(self, audio_path: str, batch_size: int = 64) -> int:
        """
        Transcribe audio with Whisper, embed the transcript chunks, and store.

        The transcription runs on CPU (Whisper), then the text chunks are
        embedded with the same SentenceTransformer used for PDF text, so
        audio and text chunks live in the same vector space and can be
        searched together.

        Args:
            audio_path: Path to an audio file or directory of audio files.
            batch_size: Number of transcript chunks to embed per batch.

        Returns:
            Total number of transcript chunks ingested.
        """
        from pipelines.transcribe_audio import build_records, collect_audio_files, transcribe

        path = Path(audio_path)
        # collect_audio_files expects a list of paths
        audio_files = collect_audio_files([path])
        if not audio_files:
            return 0

        # Transcribe all audio files and build chunk records
        all_chunks: list[dict] = []
        for af in audio_files:
            segments = transcribe(self.whisper_model, af)
            all_chunks.extend(build_records(af, segments))
            logger.info("Transcribed %s: %d segments", af.name, len(segments))

        if not all_chunks:
            return 0

        dim = self.text_model.get_sentence_embedding_dimension()

        conn = self._get_conn()
        try:
            conn.autocommit = True
            create_text_table(conn, dim)

            for start in range(0, len(all_chunks), batch_size):
                batch = all_chunks[start : start + batch_size]
                vectors = self.text_model.encode(
                    [c["text"] for c in batch],
                    normalize_embeddings=True,
                    batch_size=batch_size,
                )
                save_chunks(conn, batch, vectors)
                logger.info("Audio ingestion: %d / %d chunks", start + len(batch), len(all_chunks))
        finally:
            self.pool.putconn(conn)

        return len(all_chunks)
