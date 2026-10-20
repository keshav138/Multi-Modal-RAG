"""
Custom Prometheus metrics for the Multi Modal RAG service.

The generic HTTP metrics (request count, latency, in-progress) are provided
automatically by ``prometheus-fastapi-instrumentator`` in ``app/main.py``.
This module adds domain-specific metrics that the RAG dashboard plots:

- ``rag_retrieval_seconds``    — full retrieval pipeline (embed + search + rerank).
- ``rag_rerank_seconds``       — cross-encoder reranking stage only.
- ``rag_llm_seconds``          — Groq LLM synthesis call.
- ``rag_ingestion_chunks_total`` — chunks stored per modality ('text', 'image', 'audio').

All of them live in the default Prometheus registry, so they are exposed
automatically by the instrumentator at GET /metrics.
"""

from prometheus_client import Counter, Histogram

# Histograms bucket latencies from 50ms to 30s — retrieval involves ML models
# and DB round-trips, so it spans a wide range.
rag_retrieval_seconds = Histogram(
    "rag_retrieval_seconds",
    "Time for the full retrieval pipeline (embed + search + rerank).",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

rag_rerank_seconds = Histogram(
    "rag_rerank_seconds",
    "Time for the cross-encoder reranking stage only.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

rag_llm_seconds = Histogram(
    "rag_llm_seconds",
    "Time for the Groq LLM synthesis call.",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

rag_ingestion_chunks_total = Counter(
    "rag_ingestion_chunks_total",
    "Chunks/records successfully ingested, labelled by modality.",
    labelnames=("modality",),
)
