"""
Tests for the health/readiness endpoints.
"""

from tests.conftest import BrokenPool


def test_health_returns_ok(client):
    """GET /health always returns 200 with status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ready_with_working_pool(client):
    """GET /readiness returns 200 when the pool and models are available."""
    response = client.get("/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["models"] == "ok"


def test_readiness_not_ready_with_broken_pool(client):
    """GET /readiness returns 503 when the database is unreachable."""
    client.app.state.pool = BrokenPool()
    response = client.get("/readiness")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not ready"
    assert body["database"].startswith("error:")
