"""
Full RAG query endpoint for the Multi Modal RAG API.

POST /query orchestrates the complete Retrieval-Augmented Generation pipeline:
1. Retrieve relevant evidence via RetrievalService (vector search + reranking).
2. Send the evidence + question to an LLM via LLMService for answer synthesis.
3. Extract citations from the generated answer and return a structured response.

This is the primary endpoint that end-users interact with.
"""

import time

from fastapi import APIRouter, Request

from app.core.metrics import rag_llm_seconds, rag_rerank_seconds, rag_retrieval_seconds
from app.schemas.query import Citation, QueryRequest, QueryResponse
from app.schemas.search import EvidenceItem
from app.services.retrieval import to_static_url

router = APIRouter(prefix="", tags=["query"])


# NOTE: Sync `def` — same reasoning as the search router.
# Both the retrieval (ML models, DB) and the LLM call (HTTP to Groq) are
# blocking operations.  FastAPI runs this in a threadpool automatically.
@router.post("/query", response_model=QueryResponse)
def query(body: QueryRequest, request: Request) -> QueryResponse:
    """
    End-to-end RAG: retrieve evidence, synthesize an answer, return citations.

    The answer may contain `[Evidence N]` markers.  For each marker found,
    a Citation object is built from the corresponding evidence metadata so
    the frontend can link back to the original source document/page.
    """
    retrieval_service = request.app.state.retrieval_service
    llm_service = request.app.state.llm_service

    # ── Step 1: Retrieve & rerank evidence ────────────────────────────
    results, timings, _total = retrieval_service.search(
        question=body.question,
        k=body.k,
        top_n=body.top_n,
    )
    rag_retrieval_seconds.observe(sum(timings.values()))
    rag_rerank_seconds.observe(timings.get("rerank", 0.0))

    # ── Step 2: Synthesize an answer with the LLM ────────────────────
    llm_start = time.perf_counter()
    llm_output = llm_service.synthesize(
        question=body.question,
        evidence=results,
        model=body.model,
    )
    timings["llm_seconds"] = round(time.perf_counter() - llm_start, 3)
    rag_llm_seconds.observe(timings["llm_seconds"])

    answer_text: str = llm_output["answer"]

    # ── Step 3: Extract citations ─────────────────────────────────────
    # The LLM is prompted to include [Evidence 1], [Evidence 2], … markers
    # when it uses a piece of evidence.  We scan the answer for these markers
    # and create Citation objects for every evidence chunk that was actually
    # referenced, so the frontend can render source links.
    citations: list[Citation] = []
    for i, evidence in enumerate(results):
        marker = f"[Evidence {i + 1}]"
        if marker in answer_text:
            citations.append(
                Citation(
                    source=evidence.get("source", "unknown"),
                    page=evidence.get("page"),
                    modality=evidence.get("modality", "text"),
                    evidence_index=i,
                )
            )

    return QueryResponse(
        answer=answer_text,
        citations=citations,
        evidence=[
            EvidenceItem(**{**r, "image_path": to_static_url(r.get("image_path"))})
            for r in results
        ],
        timings=timings,
        model_used=llm_output["model_used"],
        usage=llm_output.get("usage", {}),
    )
