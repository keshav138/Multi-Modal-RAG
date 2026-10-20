"""
Search schemas — request and response models for the /search endpoint.

SearchRequest captures what the user wants to find.  SearchResponse wraps the
ranked evidence list returned by the retrieval service, including per-stage
timing so the frontend (or developer) can see where latency goes.
"""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Parameters for a multi-modal vector search."""

    question: str = Field(
        ...,
        description="The natural-language question to search for.",
    )
    k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of candidates to fetch per vector search (text, audio, image).",
    )
    top_n: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of results to return after cross-encoder reranking.",
    )
    modalities: list[str] = Field(
        default=["text", "image", "audio"],
        description="Which modalities to include in the search.",
    )


class EvidenceItem(BaseModel):
    """A single piece of retrieved evidence, tagged with its origin and scores."""

    chunk_id: str
    modality: str
    content_type: str | None = None
    source: str
    page: int | None = None
    location: dict | None = None
    text: str
    image_path: str | None = None
    retrieval_score: float
    rerank_score: float


class SearchResponse(BaseModel):
    """Wraps the full search result set with timings for observability."""

    results: list[EvidenceItem]
    timings: dict[str, float]
    total_candidates: int = Field(
        description="Number of candidates before reranking (useful for debugging recall).",
    )
