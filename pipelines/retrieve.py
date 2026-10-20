"""
retrieve.py — the fusion layer: one question in, best evidence out.

Steps for every question:
  1. EMBED   the question twice: with the text model (for text + audio chunks)
             and with SigLIP's text encoder (for images).
  2. SEARCH  three searches in parallel: text chunks, audio chunks, images.
  3. MERGE   all candidates into one list.
  4. RERANK  with a cross-encoder, which reads (question, candidate) together
             and gives a much more accurate relevance score than vector distance.
  5. PICK    the top N, each tagged with its modality and source reference.

Images can't be read by a text cross-encoder, so each image candidate is
scored on a text stand-in: the manual's own caption + the text printed on
the same page. (Not a vision-model caption: that would be the "text pipeline
in a costume" trap. Retrieval itself still used the image vector.)

Usage:
    export DATABASE_URL=postgresql://postgres:pw@localhost:5432/fieldnote
    python pipelines/retrieve.py "why does unit 7 keep tripping on high pressure"

Other scripts can import it:
    retriever = Retriever(dsn)            # loads models once
    results, timings = retriever.search("question")
"""

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import torch
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from sentence_transformers import CrossEncoder, SentenceTransformer
from transformers import AutoModel, AutoProcessor

# Reuse the exact models and helpers used at ingestion time, so they always
# match.  The try/except keeps both invocation styles working: as a package
# (from the repo root) and as a script (from inside the pipelines/ folder).
try:
    from pipelines.ingest_images import DEFAULT_MODEL as IMAGE_MODEL
    from pipelines.ingest_images import embed_text as embed_text_siglip
    from pipelines.ingest_pgvector import DEFAULT_MODEL as TEXT_MODEL
    from pipelines.ingest_pgvector import QUERY_PREFIX
except ImportError:  # ran directly, e.g. `python pipelines/retrieve.py`
    from ingest_images import DEFAULT_MODEL as IMAGE_MODEL
    from ingest_images import embed_text as embed_text_siglip
    from ingest_pgvector import DEFAULT_MODEL as TEXT_MODEL
    from ingest_pgvector import QUERY_PREFIX

# small and fast; for better accuracy try "BAAI/bge-reranker-base" (slower)
DEFAULT_RERANKER = "cross-encoder/ms-marco-MiniLM-L-6-v2"
PAGE_TEXT_CHARS = 800  # how much same-page text to attach to an image candidate


def pick_top(ranked, top_n, guarantee_image=True):
    """
    Take the top N of the reranked list. Because images are scored on a text
    stand-in, they can be unfairly buried; if none made the cut, swap the best
    image into the last slot so the final model always gets a chance to look.
    """
    chosen = ranked[:top_n]
    if guarantee_image and not any(c["modality"] == "image" for c in chosen):
        best_image = next((c for c in ranked if c["modality"] == "image"), None)
        if best_image:
            chosen[-1] = best_image
    return sorted(chosen, key=lambda c: c["rerank_score"], reverse=True)


class Retriever:
    def __init__(self, dsn, reranker_model=DEFAULT_RERANKER):
        self.dsn = dsn
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.text_model = SentenceTransformer(TEXT_MODEL)
        self.siglip = AutoModel.from_pretrained(IMAGE_MODEL).to(self.device).eval()
        self.siglip_processor = AutoProcessor.from_pretrained(IMAGE_MODEL)
        self.reranker = CrossEncoder(reranker_model, max_length=512)

    # ---- the three searches ------------------------------------------------
    def _connect(self):
        # one connection per search thread (a connection can't be shared safely).
        # When this goes behind FastAPI, swap this for a connection pool.
        conn = psycopg.connect(self.dsn, row_factory=dict_row)
        register_vector(conn)
        return conn

    def _search_text_table(self, vector, modality, k):
        """Nearest chunks in text_chunks for one modality ('text' or 'audio')."""
        sql = """SELECT chunk_id, modality, content_type, source, page,
                        bbox AS location, text, 1 - (embedding <=> %s) AS retrieval_score
                 FROM text_chunks WHERE modality = %s
                 ORDER BY embedding <=> %s LIMIT %s"""
        with self._connect() as conn:
            candidates = conn.execute(sql, [vector, modality, vector, k]).fetchall()
        for c in candidates:
            c["image_path"] = None
            c["rerank_text"] = c["text"]  # the cross-encoder reads the chunk itself
        return candidates

    def _search_images(self, vector, k):
        """Nearest figures in image_chunks, each given a text stand-in for reranking."""
        sql = """SELECT chunk_id, modality, source, page, bbox AS location,
                        caption, image_path, 1 - (embedding <=> %s) AS retrieval_score
                 FROM image_chunks ORDER BY embedding <=> %s LIMIT %s"""
        page_sql = """SELECT string_agg(text, ' ') AS page_text FROM text_chunks
                      WHERE source = %s AND page = %s AND modality = 'text'"""
        with self._connect() as conn:
            candidates = conn.execute(sql, [vector, vector, k]).fetchall()
            for c in candidates:
                page_text = conn.execute(page_sql, [c["source"], c["page"]]).fetchone()["page_text"]
                c["content_type"] = "figure"
                c["text"] = c["caption"] or ""
                c["rerank_text"] = (
                    f"{c['caption'] or ''} {(page_text or '')[:PAGE_TEXT_CHARS]}".strip()
                )
        return candidates

    # ---- the full pipeline ---------------------------------------------------
    def search(self, question, k=10, top_n=5, guarantee_image=True):
        """Returns (results, timings). `k` = candidates fetched per search."""
        timings = {}

        # 1. embed the question for both vector spaces
        start = time.perf_counter()
        text_vector = self.text_model.encode(QUERY_PREFIX + question, normalize_embeddings=True)
        image_vector = embed_text_siglip(self.siglip, self.siglip_processor, question, self.device)
        timings["embed"] = time.perf_counter() - start

        # 2 + 3. three searches at the same time, then merge into one list
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [
                pool.submit(self._search_text_table, text_vector, "text", k),
                pool.submit(self._search_text_table, text_vector, "audio", k),
                pool.submit(self._search_images, image_vector, k),
            ]
            candidates = [c for job in jobs for c in job.result()]
        timings["search"] = time.perf_counter() - start
        if not candidates:
            return [], timings

        # 4. rerank: the cross-encoder scores every (question, candidate) pair
        start = time.perf_counter()
        scores = self.reranker.predict([(question, c["rerank_text"]) for c in candidates])
        for c, score in zip(candidates, scores):
            c["rerank_score"] = float(score)
            del c["rerank_text"]
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        timings["rerank"] = time.perf_counter() - start

        # 5. pick the final evidence
        return pick_top(ranked, top_n, guarantee_image), timings


def describe(result, chars=None):
    """One-line summary of a result for the command line (chars=None shows all text)."""
    if result["modality"] == "image":
        return f"{result['caption']!r}  {result['image_path']}"
    if result["modality"] == "audio":
        loc = result["location"]
        return f"{loc['start']}s-{loc['end']}s  {result['text'][:chars]!r}"
    return result["text"][:chars].replace("\n", " ")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--k", type=int, default=10, help="candidates fetched per search")
    parser.add_argument("--top-n", type=int, default=5, help="results returned after reranking")
    parser.add_argument("--reranker", default=DEFAULT_RERANKER)
    parser.add_argument("--chars", type=int, default=300,
                        help="characters of text to show per result (0 = all)")
    args = parser.parse_args()
    if not args.dsn:
        parser.error("Set DATABASE_URL or pass --dsn")

    retriever = Retriever(args.dsn, args.reranker)
    results, timings = retriever.search(args.question, args.k, args.top_n)

    for r in results:
        page = f"p{r['page']}" if r["page"] else "audio"
        summary = describe(r, args.chars or None)
        print(f"{r['rerank_score']:7.2f}  [{r['modality']:5}] "
              f"{r['source']} {page}  {summary}")
    print("\n" + "  ".join(f"{name}: {seconds:.2f}s" for name, seconds in timings.items())
          + f"  total: {sum(timings.values()):.2f}s")


if __name__ == "__main__":
    main()
