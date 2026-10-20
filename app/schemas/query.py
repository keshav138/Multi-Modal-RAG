"""
Query schemas — request and response models for the /query endpoint.

The query endpoint first retrieves evidence (same as /search), then sends it
to an LLM to synthesize a grounded answer with citations.  QueryResponse
carries the answer, the evidence it was based on, and token usage from the LLM.
"""

from pydantic import BaseModel, Field

from app.schemas.search import EvidenceItem


class QueryRequest(BaseModel):
    """Parameters for an LLM-augmented retrieval query."""

    question: str = Field(
        ...,
        description="The natural-language question to answer.",
    )
    k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Candidates fetched per vector search.",
    )
    top_n: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Evidence items passed to the LLM after reranking.",
    )
    model: str | None = Field(
        default=None,
        description=(
            "Groq model override. When None the server default from "
            "settings.groq_model is used at runtime."
        ),
    )


class Citation(BaseModel):
    """A structured citation extracted from the LLM answer."""

    source: str
    page: int | None = None
    modality: str
    evidence_index: int = Field(
        description="0-based index into the evidence list so the frontend can highlight it.",
    )


class QueryResponse(BaseModel):
    """Full answer with provenance, timing, and LLM usage stats."""

    # `model_used` collides with pydantic's reserved `model_` namespace.
    model_config = {"protected_namespaces": ()}

    answer: str
    citations: list[Citation]
    evidence: list[EvidenceItem]
    timings: dict[str, float]
    model_used: str
    usage: dict[str, int] = Field(
        description="Token counts: prompt_tokens and completion_tokens.",
    )
