"""CLI pipeline scripts — ingestion, transcription, and fusion retrieval.

Each module here is also importable as a library (e.g. the FastAPI service
imports ``embed_images``/``save_chunks`` from these modules), so the exact
same models and helpers used at ingestion time are used at query time.
"""
