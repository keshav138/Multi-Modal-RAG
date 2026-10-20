"""
Health-check endpoints for the Multi Modal RAG API.

Two probes are exposed:
- /health    — lightweight liveness check (always returns 200).
- /readiness — deeper check that verifies the database connection pool
               and ML model availability before reporting "ready".

These follow the Kubernetes probe conventions so the service can be
deployed behind a load balancer or orchestrator that polls them.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="", tags=["health"])


@router.get("/health")
async def liveness() -> dict:
    """Liveness probe — always returns 200 to signal the process is alive."""
    return {"status": "ok"}


@router.get("/readiness")
async def readiness(request: Request) -> JSONResponse:
    """
    Readiness probe — verifies that critical subsystems are operational.

    Checks performed:
    1. Database pool can acquire and release a connection.
    2. RetrievalService (which wraps the ML models) is present on app.state.

    Returns 200 with component statuses on success, or 503 with failure details.
    """
    details: dict[str, str] = {}
    all_ok = True

    # --- Check 1: Database connectivity ---
    try:
        pool = request.app.state.pool
        # Borrow a connection and immediately return it to prove the pool works
        conn = pool.getconn()
        pool.putconn(conn)
        details["database"] = "ok"
    except Exception as exc:
        details["database"] = f"error: {exc}"
        all_ok = False

    # --- Check 2: ML models loaded via RetrievalService ---
    try:
        retrieval_svc = request.app.state.retrieval_service
        if retrieval_svc is None:
            raise RuntimeError("retrieval_service is None")
        details["models"] = "ok"
    except Exception as exc:
        details["models"] = f"error: {exc}"
        all_ok = False

    if all_ok:
        return JSONResponse(
            status_code=200,
            content={"status": "ready", **details},
        )

    return JSONResponse(
        status_code=503,
        content={"status": "not ready", **details},
    )
