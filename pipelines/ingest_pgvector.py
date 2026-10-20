"""
ingest_pgvector.py — turn chunk text into vectors and store them in Postgres.

Reads the JSONL from chunk_text.py, embeds each chunk's text with a
sentence-transformers model, and saves chunk + vector in the `text_chunks` table.
Later, audio transcripts go into the same table (modality = 'audio') because
they use the same text embedder. Images get their own table (different model).

Setup:
    pip install sentence-transformers "psycopg[binary]" pgvector
    export DATABASE_URL=postgresql://postgres:pw@localhost:5432/fieldnote

Usage:
    python ingest_pgvector.py out/text_chunks.jsonl
    python ingest_pgvector.py out/text_chunks.jsonl --test-query "filter drier install"
"""

import argparse
import json
import os

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dimensional vectors; change with --model
# bge models want this prefix on search *queries* (never on the stored chunks)
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def create_table(conn, dim):
    """Create the extension, table and indexes if they don't exist yet."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS text_chunks (
            chunk_id     TEXT PRIMARY KEY,
            modality     TEXT NOT NULL,   -- 'text' or 'audio'
            content_type TEXT,            -- 'text', 'table' or 'transcript'
            source       TEXT NOT NULL,   -- which PDF / audio file
            page         INT,
            bbox         JSONB,           -- where on the page, for citations
            text         TEXT NOT NULL,
            embedding    vector({dim}) NOT NULL
        )""")
    # fast filter by modality, and fast nearest-neighbour search by cosine distance
    conn.execute("CREATE INDEX IF NOT EXISTS text_chunks_modality_idx ON text_chunks (modality)")
    conn.execute("""CREATE INDEX IF NOT EXISTS text_chunks_embedding_idx
                    ON text_chunks USING hnsw (embedding vector_cosine_ops)""")


def save_chunks(conn, chunks, vectors):
    """Insert chunks; if a chunk_id already exists, overwrite it (safe to re-run)."""
    sql = """
        INSERT INTO text_chunks
            (chunk_id, modality, content_type, source, page, bbox, text, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chunk_id) DO UPDATE SET
            modality = EXCLUDED.modality,
            content_type = EXCLUDED.content_type,
            source = EXCLUDED.source,
            page = EXCLUDED.page,
            bbox = EXCLUDED.bbox,
            text = EXCLUDED.text,
            embedding = EXCLUDED.embedding"""
    rows = [
        (c["chunk_id"], c["modality"], c.get("content_type"), c["source"],
         c.get("page"), Jsonb(c.get("bbox")), c["text"], np.asarray(v))
        for c, v in zip(chunks, vectors)
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def run_test_query(conn, model, question, modality=None, top_k=5):
    """Embed a question and print the closest chunks, as a retrieval sanity check."""
    query_vector = np.asarray(model.encode(QUERY_PREFIX + question, normalize_embeddings=True))

    sql = (
        "SELECT source, page, content_type, 1 - (embedding <=> %s) AS score, "
        "left(text, 140) FROM text_chunks"
    )
    params = [query_vector]
    if modality:
        sql += " WHERE modality = %s"
        params.append(modality)
    # `<=>` is cosine distance: smaller = more similar
    sql += " ORDER BY embedding <=> %s LIMIT %s"
    params += [query_vector, top_k]

    with conn.cursor() as cur:
        cur.execute(sql, params)
        for source, page, content_type, score, snippet in cur.fetchall():
            print(f"{score:.3f}  {source} p{page} [{content_type}]  {snippet!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", help="chunk file produced by chunk_text.py")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--test-query", help="after ingesting, search for this question")
    parser.add_argument("--modality", help="limit --test-query to 'text' or 'audio'")
    args = parser.parse_args()
    if not args.dsn:
        parser.error("Set DATABASE_URL or pass --dsn")

    with open(args.jsonl) as f:
        chunks = [json.loads(line) for line in f if line.strip()]

    model = SentenceTransformer(args.model)
    dim = model.get_sentence_embedding_dimension()  # the table's vector size must match

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        create_table(conn, dim)
        register_vector(conn)  # lets psycopg send numpy arrays as pgvector values

        for start in range(0, len(chunks), args.batch_size):
            batch = chunks[start:start + args.batch_size]
            # normalize_embeddings=True makes cosine search consistent
            vectors = model.encode([c["text"] for c in batch],
                                   normalize_embeddings=True, batch_size=args.batch_size)
            save_chunks(conn, batch, vectors)
            print(f"ingested {start + len(batch)}/{len(chunks)}")

        if args.test_query:
            print(f"\nTop results for: {args.test_query!r}")
            run_test_query(conn, model, args.test_query, args.modality)


if __name__ == "__main__":
    main()
