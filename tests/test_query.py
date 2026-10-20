"""
Tests for the full /query endpoint (retrieval + LLM synthesis + citations).
"""


def test_query_returns_answer_with_citations(client, fake_services):
    """The full pipeline returns an answer, citations, evidence and usage."""
    response = client.post(
        "/query",
        json={"question": "Where should the filter drier be installed?"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["answer"] == "Install the filter drier in the liquid line. [Evidence 1]"
    assert body["model_used"] == "fake-model"
    assert body["usage"]["prompt_tokens"] == 42
    assert body["timings"]["llm_seconds"] >= 0

    # The answer cites [Evidence 1], so exactly one citation is extracted.
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["source"] == "trane_manual.pdf"
    assert citation["page"] == 3
    assert citation["modality"] == "text"
    assert citation["evidence_index"] == 0

    # Evidence is echoed back so the frontend can render source links.
    assert len(body["evidence"]) == 1
    assert body["evidence"][0]["chunk_id"] == "trane_p3_c0"


def test_query_passes_model_override(client, fake_services):
    """An explicit model in the request is forwarded to the LLM service."""
    response = client.post(
        "/query",
        json={"question": "anything", "model": "llama-3.1-8b-instant"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model_used"] == "llama-3.1-8b-instant"
    assert fake_services["llm"].calls[0]["model"] == "llama-3.1-8b-instant"


def test_query_answer_without_markers_yields_no_citations(client, fake_services):
    """If the LLM answer has no [Evidence N] markers, citations is empty."""
    fake_services["llm"].calls = []
    original = fake_services["llm"].synthesize

    def _no_markers(question, evidence, model=None):
        out = original(question, evidence, model)
        out["answer"] = "No citation markers here."
        return out

    fake_services["llm"].synthesize = _no_markers

    response = client.post("/query", json={"question": "anything"})
    assert response.status_code == 200
    assert response.json()["citations"] == []
