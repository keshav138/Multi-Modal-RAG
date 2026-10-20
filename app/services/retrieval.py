"""
retrieval.py — pool-aware retrieval service for the FastAPI app.

This is a reimplementation of retrieve.py's Retriever class that uses a
psycopg ConnectionPool instead of opening a fresh connection per search.
The search logic (embed → 3 parallel DB searches → merge → rerank → pick)
is copied verbatim so results are identical to the standalone script.

WHY reimplement instead of wrapping?  The original Retriever.search() calls
psycopg.connect() inside each thread.  Under FastAPI that would leak
connections and bypass the pool.  By owning the connection lifecycle here we
get proper pooling, health checks, and bounded concurrency for free.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import torch
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sentence_transformers import CrossEncoder, SentenceTransformer
from transformers import AutoModel, AutoProcessor

from app.config import settings

# Reuse the exact constants and helpers from the ingestion scripts so the
# embeddings at query time always match the ones stored at ingestion time.
from pipelines.ingest_images import embed_text as embed_text_siglip
from pipelines.ingest_pgvector import QUERY_PREFIX

# How much same-page text to attach to an image candidate for reranking.
# Images can't be read by a text cross-encoder, so we build a text stand-in
# from the figure's caption + surrounding page text.
PAGE_TEXT_CHARS = 800


def to_static_url(image_path: str | None) -> str | None:
    """
    Convert a stored image path into the URL the client can render.

    Extracted figures live in ``settings.image_dir``, which FastAPI serves
    at /static/images (see app/main.py).  Only the basename is exposed —
    the client has no business knowing the server's directory layout.
    """
    if not image_path:
        return None
    return f"/static/images/{Path(image_path).name}"


def pick_top(ranked: list[dict], top_n: int, guarantee_image: bool = True) -> list[dict]:
    """
    Take the top N of the reranked list.

    Because images are scored on a text stand-in they can be unfairly buried.
    If none made the cut, swap the best image into the last slot so the LLM
    always has a chance to reference a figure.
    """
    chosen = ranked[:top_n]
    if guarantee_image and not any(c["modality"] == "image" for c in chosen):
        best_image = next((c for c in ranked if c["modality"] == "image"), None)
        if best_image:
            chosen[-1] = best_image
    return sorted(chosen, key=lambda c: c["rerank_score"], reverse=True)


class RetrievalService:
    """
    Stateful retrieval service — loads ML models once, uses pooled connections.

    Intended to be created at app startup and shared across all request handlers.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # Text embedding model (same one used by ingest_pgvector.py)
        self.text_model = SentenceTransformer(settings.text_embed_model)

        # SigLIP image/text dual encoder (same one used by ingest_images.py)
        self.siglip = AutoModel.from_pretrained(settings.image_embed_model).to(self.device).eval()
        self.siglip_processor = AutoProcessor.from_pretrained(settings.image_embed_model)

        # Cross-encoder for reranking — reads (query, passage) pairs and gives
        # a much more accurate relevance score than raw vector distance
        self.reranker = CrossEncoder(settings.reranker_model, max_length=512)

    # ------------------------------------------------------------------
    # Private: the three parallel DB searches
    # ------------------------------------------------------------------

    def _get_conn(self):
        """
        Borrow a connection from the pool and register pgvector on it.

        Returns (conn, needs_putconn).  The caller MUST call pool.putconn()
        in a finally block.
        """
        conn = self.pool.getconn()
        # dict_row so every row comes back as a dict matching our schema
        conn.row_factory = dict_row
        register_vector(conn)
        return conn

    def _search_text_table(self, vector, modality: str, k: int) -> list[dict]:
        """Nearest chunks in text_chunks for one modality ('text' or 'audio')."""
        sql = """SELECT chunk_id, modality, content_type, source, page,
                        bbox AS location, text, 1 - (embedding <=> %s) AS retrieval_score
                 FROM text_chunks WHERE modality = %s
                 ORDER BY embedding <=> %s LIMIT %s"""
        conn = self._get_conn()
        try:
            candidates = conn.execute(sql, [vector, modality, vector, k]).fetchall()
        finally:
            self.pool.putconn(conn)

        for c in candidates:
            c["image_path"] = None
            # The cross-encoder reads the chunk text itself for text/audio
            c["rerank_text"] = c["text"]
        return candidates

    def _search_images(self, vector, k: int) -> list[dict]:
        """
        Nearest figures in image_chunks.

        Each image gets a text stand-in for the cross-encoder reranker:
        its caption concatenated with surrounding text from the same page.
        This avoids scoring an image on its pixel content (which a text
        cross-encoder can't do) while still leveraging the figure's context.
        """
        sql = """SELECT chunk_id, modality, source, page, bbox AS location,
                        caption, image_path, 1 - (embedding <=> %s) AS retrieval_score
                 FROM image_chunks ORDER BY embedding <=> %s LIMIT %s"""
        page_sql = """SELECT string_agg(text, ' ') AS page_text FROM text_chunks
                      WHERE source = %s AND page = %s AND modality = 'text'"""

        conn = self._get_conn()
        try:
            candidates = conn.execute(sql, [vector, vector, k]).fetchall()
            for c in candidates:
                page_row = conn.execute(page_sql, [c["source"], c["page"]]).fetchone()
                page_text = page_row["page_text"] if page_row else None
                c["content_type"] = "figure"
                c["text"] = c["caption"] or ""
                # Build the reranking stand-in: caption + first N chars of page text
                c["rerank_text"] = (
                    f"{c['caption'] or ''} {(page_text or '')[:PAGE_TEXT_CHARS]}".strip()
                )
        finally:
            self.pool.putconn(conn)

        return candidates

    # ------------------------------------------------------------------
    # Public: the full search pipeline
    # ------------------------------------------------------------------

    def search(
        self,
        question: str,
        k: int = 10,
        top_n: int = 5,
        modalities: list[str] | None = None,
        guarantee_image: bool = True,
    ) -> tuple[list[dict], dict, int]:
        """
        Run the full retrieval pipeline: embed → search → merge → rerank → pick.

        Args:
            question: The natural-language question to search for.
            k: Candidates fetched per vector search.
            top_n: Results returned after cross-encoder reranking.
            modalities: Subset of {"text", "image", "audio"} to search, or
                None for all three.  Unknown values are ignored.
            guarantee_image: If True and no image made the cut, swap the best
                image into the last slot so the LLM can always cite a figure.

        Returns:
            (results, timings, total_candidates) where results is a list of
            evidence dicts, timings maps stage names to elapsed seconds, and
            total_candidates is the merged candidate count before reranking.
        """
        timings: dict[str, float] = {}

        # Normalise the requested modalities, defaulting to all three.
        requested = set(modalities) if modalities else {"text", "image", "audio"}
        requested &= {"text", "image", "audio"}
        if not requested:
            return [], timings, 0

        # 1. Embed the question in both vector spaces
        start = time.perf_counter()
        text_vector = self.text_model.encode(
            QUERY_PREFIX + question, normalize_embeddings=True,
        )
        image_vector = embed_text_siglip(
            self.siglip, self.siglip_processor, question, self.device,
        )
        timings["embed"] = time.perf_counter() - start

        # 2 + 3. One search per requested modality, in parallel, then merge.
        # The image search is always fed the SigLIP text vector; text/audio
        # searches share the sentence-transformer vector.
        start = time.perf_counter()
        jobs = []
        if "text" in requested:
            jobs.append(("text", lambda: self._search_text_table(text_vector, "text", k)))
        if "audio" in requested:
            jobs.append(("audio", lambda: self._search_text_table(text_vector, "audio", k)))
        if "image" in requested:
            jobs.append(("image", lambda: self._search_images(image_vector, k)))

        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = {modality: executor.submit(fn) for modality, fn in jobs}
            candidates = [c for future in futures.values() for c in future.result()]
        timings["search"] = time.perf_counter() - start
        total_candidates = len(candidates)

        if not candidates:
            return [], timings, 0

        # 4. Rerank: the cross-encoder scores every (question, candidate) pair
        start = time.perf_counter()
        scores = self.reranker.predict(
            [(question, c["rerank_text"]) for c in candidates],
        )
        for c, score in zip(candidates, scores):
            c["rerank_score"] = float(score)
            del c["rerank_text"]  # clean up the internal field before returning
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        timings["rerank"] = time.perf_counter() - start

        # 5. Pick the final evidence set
        return pick_top(ranked, top_n, guarantee_image), timings, total_candidates
