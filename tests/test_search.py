"""
Tests for the retrieval-only /search endpoint.
"""


def test_search_returns_evidence_and_timings(client, fake_services):
    """A valid search returns ranked evidence with per-stage timings."""
    response = client.post(
        "/search",
        json={
            "question": "Where should the filter drier be installed?",
            "k": 10,
            "top_n": 5,
            "modalities": ["text", "audio"],
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["total_candidates"] == 1
    assert body["timings"]["rerank"] == 0.05

    evidence = body["results"]
    assert len(evidence) == 1
    item = evidence[0]
    assert item["chunk_id"] == "trane_p3_c0"
    assert item["modality"] == "text"
    assert item["source"] == "trane_manual.pdf"
    assert item["page"] == 3
    assert item["text"] == "Install the filter drier in the liquid line."
    assert item["retrieval_score"] == 0.82
    assert item["rerank_score"] == 0.95


def test_search_forwards_requested_modalities(client, fake_services):
    """The modalities field from the request reaches the retrieval service."""
    response = client.post(
        "/search",
        json={"question": "wiring diagram", "modalities": ["image"]},
    )
    assert response.status_code == 200
    assert fake_services["retrieval"].calls[0]["modalities"] == ["image"]


def test_search_uses_defaults(client, fake_services):
    """k, top_n and modalities fall back to schema defaults."""
    response = client.post("/search", json={"question": "pressure chart"})
    assert response.status_code == 200
    call = fake_services["retrieval"].calls[0]
    assert call["k"] == 10
    assert call["top_n"] == 5
    assert call["modalities"] == ["text", "image", "audio"]


def test_search_rewrites_image_path_to_static_url(client, fake_services):
    """Stored image paths are exposed as /static/images/<name> URLs."""
    image_item = {
        "chunk_id": "trane_p12_fig1",
        "modality": "image",
        "content_type": "figure",
        "source": "trane_manual.pdf",
        "page": 12,
        "location": {"x0": 10, "y0": 20, "x1": 300, "y1": 200},
        "text": "Fig. 4 — Wiring diagram",
        "image_path": "C:/server/data/out_images/trane_manual_p12_fig1.png",
        "retrieval_score": 0.75,
        "rerank_score": 0.88,
    }

    def _image_search(question, k=10, top_n=5, modalities=None, guarantee_image=True):
        return [image_item], {"embed": 0.01, "search": 0.02, "rerank": 0.03}, 1

    fake_services["retrieval"].search = _image_search

    response = client.post("/search", json={"question": "wiring diagram"})
    assert response.status_code == 200
    assert response.json()["results"][0]["image_path"] == (
        "/static/images/trane_manual_p12_fig1.png"
    )
