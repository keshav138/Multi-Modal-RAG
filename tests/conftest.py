"""
Shared pytest fixtures for the Multi Modal RAG API tests.

The FastAPI app is imported only after placeholder env vars are set, because
``app.config.Settings`` requires DATABASE_URL and GROQ_API_KEY.  The
TestClient is used *without* its context manager so the lifespan never runs
— loading real ML models and opening a real DB pool would make tests slow
and environment-dependent.  Every fixture installs lightweight fakes onto
``app.state`` instead, which is exactly the injection point the routers use.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pytest
from fastapi.testclient import TestClient

from app.main import app

# ── Fakes ─────────────────────────────────────────────────────────────


class FakePool:
    """Stand-in for psycopg_pool.ConnectionPool with working getconn/putconn."""

    def getconn(self):
        return object()

    def putconn(self, conn):
        pass


class BrokenPool:
    """Pool that fails to hand out connections (readiness-failure tests)."""

    def getconn(self):
        raise RuntimeError("connection refused")

    def putconn(self, conn):
        pass


class FakeRetrievalService:
    """Records search calls and returns one canned evidence item per call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search(
        self,
        question: str,
        k: int = 10,
        top_n: int = 5,
        modalities: list[str] | None = None,
        guarantee_image: bool = True,
    ):
        self.calls.append(
            {"question": question, "k": k, "top_n": top_n, "modalities": modalities},
        )
        results = [
            {
                "chunk_id": "trane_p3_c0",
                "modality": "text",
                "content_type": "text",
                "source": "trane_manual.pdf",
                "page": 3,
                "location": {"x0": 0, "y0": 0, "x1": 612, "y1": 200},
                "text": "Install the filter drier in the liquid line.",
                "image_path": None,
                "retrieval_score": 0.82,
                "rerank_score": 0.95,
            },
        ]
        timings = {"embed": 0.01, "search": 0.02, "rerank": 0.05}
        return results, timings, len(results)


class FakeLLMService:
    """Returns a canned answer that cites [Evidence 1]."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def synthesize(self, question: str, evidence: list[dict], model: str | None = None):
        self.calls.append({"question": question, "model": model, "evidence": evidence})
        return {
            "answer": "Install the filter drier in the liquid line. [Evidence 1]",
            "model_used": model or "fake-model",
            "usage": {"prompt_tokens": 42, "completion_tokens": 9},
        }


class FakeIngestionService:
    """Records ingestion calls and reports fixed chunk counts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def ingest_pdfs(self, pdf_paths: list[str]) -> tuple[int, int]:
        self.calls.append(("pdf", list(pdf_paths)))
        return 7, 3  # text chunks, image records

    def ingest_audio(self, audio_path: str) -> int:
        self.calls.append(("audio", [audio_path]))
        return 5


# ── Fixture ───────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    """
    TestClient with fake services installed on app.state.

    Not used as a context manager on purpose — see the module docstring.
    """
    app.state.pool = FakePool()
    app.state.retrieval_service = FakeRetrievalService()
    app.state.llm_service = FakeLLMService()
    app.state.ingestion_service = FakeIngestionService()
    app.state.jobs = {}

    test_client = TestClient(app)
    yield test_client
    test_client.close()


@pytest.fixture()
def fake_services():
    """Direct handles to the fakes installed by the `client` fixture."""
    return {
        "pool": app.state.pool,
        "retrieval": app.state.retrieval_service,
        "llm": app.state.llm_service,
        "ingestion": app.state.ingestion_service,
    }
