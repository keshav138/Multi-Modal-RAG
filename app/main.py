"""
Application entry-point for the Multi Modal RAG API.

This module wires together every component of the system:

1. **Lifespan** — an async context manager that initialises heavy resources
   (DB connection pool, ML-backed services) once at startup and tears them
   down cleanly on shutdown.
2. **FastAPI app** — created with the lifespan, Prometheus instrumentation,
   static-file serving for extracted images, and all route modules.
3. **Uvicorn runner** — for convenient ``python -m app.main`` execution
   during development.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.database import close_pool, create_pool

# Import routers
from app.routers.health import router as health_router
from app.routers.ingest import router as ingest_router
from app.routers.query import router as query_router
from app.routers.search import router as search_router
from app.services.ingestion import IngestionService
from app.services.llm import LLMService
from app.services.retrieval import RetrievalService

# ── Lifespan ──────────────────────────────────────────────────────────
# The lifespan context manager replaces the deprecated @app.on_event
# startup/shutdown hooks.  Everything before `yield` runs at startup;
# everything after runs at shutdown.


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application-wide resources across the server's lifetime.

    Startup:
        - Create a PostgreSQL connection pool.
        - Instantiate RetrievalService (loads text embedder, SigLIP,
          cross-encoder into memory — this is the slow part).
        - Instantiate LLMService (lightweight HTTP client wrapper).
        - Instantiate IngestionService (reuses the DB pool).
        - Initialise the in-memory ingestion job tracker.

    Shutdown:
        - Close the connection pool and release all connections.
    """
    # ── Startup ───────────────────────────────────────────────────────
    print("=" * 60)
    print("  Multi Modal RAG API — Starting up")
    print("=" * 60)

    # 1. Database connection pool
    print("Creating database connection pool...")
    pool = create_pool(settings.database_url)
    app.state.pool = pool
    print("Connection pool ready ✓")

    # 2. Retrieval service (this triggers ML model downloads on first run)
    print("Loading ML models... (this takes 30-60s on first run)")
    retrieval_service = RetrievalService(pool=pool)
    app.state.retrieval_service = retrieval_service
    print("Text embedder loaded ✓")
    print("SigLIP loaded ✓")
    print("Cross-encoder loaded ✓")

    # 3. LLM service — lightweight; just stores the API key and model name
    llm_service = LLMService(
        api_key=settings.groq_api_key,
        default_model=settings.groq_model,
    )
    app.state.llm_service = llm_service
    print("LLM service configured ✓")

    # 4. Ingestion service
    ingestion_service = IngestionService(pool=pool)
    app.state.ingestion_service = ingestion_service
    print("Ingestion service ready ✓")

    # 5. In-memory job tracker for background ingestion tasks
    #    Keys are UUID strings; values are dicts with status/chunks_processed/error.
    app.state.jobs: dict[str, dict] = {}

    print("=" * 60)
    print("  Multi Modal RAG API is ready 🚀")
    print(f"  Docs → http://{settings.host}:{settings.port}/docs")
    print("=" * 60)

    yield  # ← Server is running; control returns here on shutdown

    # ── Shutdown ──────────────────────────────────────────────────────
    print("Shutting down...")
    close_pool(pool)
    print("Connection pool closed ✓")
    print("Goodbye.")


# ── App factory ───────────────────────────────────────────────────────

app = FastAPI(
    title="Multi Modal RAG API",
    description=(
        "Retrieval-Augmented Generation across text, images, and audio. "
        "Upload documents, ask questions, and get grounded answers with "
        "source citations."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Prometheus metrics ────────────────────────────────────────────────
# Automatically tracks request count, latency, and in-progress requests.
# Metrics are exposed at GET /metrics in Prometheus exposition format.
Instrumentator().instrument(app).expose(app)

# ── Static files ──────────────────────────────────────────────────────
# Serve extracted document images so the frontend can display them.
# Images are stored at the path configured in settings.image_dir (created
# here if missing, e.g. a fresh clone before any ingestion has run).
Path(settings.image_dir).mkdir(parents=True, exist_ok=True)
app.mount(
    "/static/images",
    StaticFiles(directory=settings.image_dir),
    name="images",
)

# ── Routers ───────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(search_router)
app.include_router(query_router)
app.include_router(ingest_router)


# ── Root endpoint ─────────────────────────────────────────────────────


@app.get("/", tags=["root"])
async def root() -> dict:
    """Welcome endpoint with navigation links."""
    return {
        "message": "Welcome to the Multi Modal RAG API",
        "docs": "/docs",
        "health": "/health",
        "openapi": "/openapi.json",
    }


# ── Development runner ────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
