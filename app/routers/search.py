"""
Search endpoint for the Multi Modal RAG API.

Exposes a single POST /search route that performs multi-modal vector
similarity search across text chunks, image descriptions, and audio
transcripts stored in PostgreSQL (pgvector).

The heavy lifting is delegated to RetrievalService which handles
embedding, querying, and cross-encoder reranking internally.
"""

from fastapi import APIRouter, Request

from app.core.metrics import rag_rerank_seconds, rag_retrieval_seconds
from app.schemas.search import EvidenceItem, SearchRequest, SearchResponse
from app.services.retrieval import to_static_url

router = APIRouter(prefix="", tags=["search"])


# NOTE: This is a regular `def` (not `async def`) on purpose.
# RetrievalService.search() calls synchronous ML model inference
# (sentence-transformers, cross-encoder) and synchronous psycopg2 DB queries.
# FastAPI automatically runs sync route handlers in a threadpool, which
# prevents blocking the event loop while these CPU/IO-bound calls execute.
@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, request: Request) -> SearchResponse:
    """
    Retrieve the most relevant evidence chunks for a natural-language question.

    Pipeline:
    1. Embed the question using the text and/or image embedding models.
    2. Query pgvector for the top-k nearest neighbours.
    3. Rerank with a cross-encoder and return the top-n results.
    """
    retrieval_service = request.app.state.retrieval_service

    results, timings, total_candidates = retrieval_service.search(
        question=body.question,
        k=body.k,
        top_n=body.top_n,
        modalities=body.modalities,
    )

    # Record pipeline timings for the RAG dashboard.  Retrieval = the whole
    # pipeline; rerank is reported separately because it usually dominates.
    rag_retrieval_seconds.observe(sum(timings.values()))
    rag_rerank_seconds.observe(timings.get("rerank", 0.0))

    # The retrieval layer returns dicts whose keys already match EvidenceItem,
    # so the Pydantic model can be built directly from each dict.  image_path
    # is rewritten to the /static/images URL the client can render.
    evidence_items = []
    for r in results:
        r = {**r, "image_path": to_static_url(r.get("image_path"))}
        evidence_items.append(EvidenceItem(**r))

    return SearchResponse(
        results=evidence_items,
        timings=timings,
        total_candidates=total_candidates,
    )
