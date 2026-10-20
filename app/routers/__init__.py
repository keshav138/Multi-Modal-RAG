"""
Router package for the Multi Modal RAG API.

Each sub-module defines an APIRouter for a specific domain:
- health  — liveness and readiness probes
- search  — vector-similarity retrieval endpoint
- query   — full RAG pipeline (retrieve + synthesize)
- ingest  — background ingestion jobs for text, images, and audio
"""
