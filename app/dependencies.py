"""
Dependency-injection module for the Multi Modal RAG API.

In this project, service instances (RetrievalService, LLMService,
IngestionService) are stored directly on ``app.state`` during the
lifespan startup phase (see ``app/main.py``).

WHY NOT USE FastAPI's ``Depends()``?
------------------------------------
The core services wrap heavy ML models (sentence-transformers, SigLIP,
cross-encoder, Whisper).  These models take 30-60 seconds to load into
GPU/CPU memory and should be instantiated **exactly once** during
application startup — not on every request.

Storing them on ``app.state`` gives us:
- A single load at startup via the lifespan context manager.
- Zero per-request overhead (no factory functions, no caching logic).
- Deterministic cleanup on shutdown.

Route handlers access the services via ``request.app.state.<service>``.

If you later need request-scoped dependencies (e.g. per-request DB
transactions or auth tokens), add standard ``Depends()`` callables here.
"""
