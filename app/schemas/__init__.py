"""
Pydantic schemas for the Multi Modal RAG API.

Organises request/response models by domain: search, query (LLM-augmented
search), and ingestion.  Every endpoint imports its models from here so the
router modules stay thin and the data contracts live in one place.
"""
