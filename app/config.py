"""
Centralised application configuration loaded from environment variables.

Uses pydantic-settings v2 so every knob is validated at startup and
available as a typed attribute on the singleton `settings` object.
A .env file is read automatically if present, but real env vars always
take precedence — which keeps containerised deployments simple.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, validated configuration for the Multi Modal RAG service."""

    # ── Required secrets / connection strings ───────────────────────
    database_url: str
    groq_api_key: str

    # ── Model identifiers ───────────────────────────────────────────
    groq_model: str = "llama-3.3-70b-versatile"
    text_embed_model: str = "BAAI/bge-small-en-v1.5"
    image_embed_model: str = "google/siglip-base-patch16-224"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    whisper_model: str = "small.en"

    # ── Server settings ─────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Paths ───────────────────────────────────────────────────────
    image_dir: str = "./data/out_images"  # extracted PDF figures, served via /static/images
    upload_dir: str = "./data/uploads"    # multipart uploads saved here before ingestion

    # ── Retrieval tuning ────────────────────────────────────────────
    default_k: int = 10      # candidates fetched per vector search
    default_top_n: int = 5   # results kept after cross-encoder reranking

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


# Module-level singleton — import this, not the class.
settings = Settings()
