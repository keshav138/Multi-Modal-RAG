"""
Service layer for the Multi Modal RAG API.

Each service encapsulates one domain concern — retrieval, LLM synthesis, or
ingestion — and is initialised once at application startup.  Services hold
loaded ML models and a reference to the connection pool so they can be reused
across requests without reloading weights.
"""
