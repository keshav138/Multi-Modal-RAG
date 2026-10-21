"""
Synchronous PostgreSQL connection pool with pgvector support.

We deliberately use a *sync* pool (psycopg_pool.ConnectionPool) instead of
an async one because all ML inference code (embedding, reranking, whisper)
is synchronous.  FastAPI automatically runs sync route handlers in a
threadpool, so we still get concurrency without mixing sync/async DB calls.
"""

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool


def ensure_vector_extension(dsn: str) -> None:
    """
    Enable the pgvector extension on a fresh database.

    register_vector() fails with "vector type not found" if the extension
    doesn't exist yet, so this must run on a plain connection *before* the
    pool is created.  It is idempotent and needs a role allowed to create
    extensions (the POSTGRES_USER of the pgvector image is a superuser).
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


def create_pool(dsn: str) -> ConnectionPool:
    """
    Create and return a connection pool that is ready for pgvector operations.

    The pool's ``configure`` callback registers the pgvector extension on
    every new connection so that numpy arrays can be used directly as
    query parameters for vector columns.

    Args:
        dsn: PostgreSQL connection string
             (e.g. ``postgresql://user:pass@host:5432/dbname``).

    Returns:
        An open ``ConnectionPool`` with min 2 / max 10 connections.
    """

    ensure_vector_extension(dsn)

    def _configure_connection(conn):  # noqa: ANN001
        """Register pgvector type handlers on each new connection."""
        register_vector(conn)

    pool = ConnectionPool(
        conninfo=dsn,
        min_size=2,
        max_size=10,
        configure=_configure_connection,
    )
    return pool


def close_pool(pool: ConnectionPool) -> None:
    """
    Gracefully shut down the connection pool.

    Should be called during application shutdown (e.g. in a FastAPI
    lifespan handler) to release all database connections.
    """
    pool.close()
