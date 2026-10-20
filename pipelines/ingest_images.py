"""
ingest_images.py — embed the cropped figures with SigLIP and store them in Postgres.

Reads all_figures_metadata.json from extract_figures.py, turns each image into
a vector with SigLIP's image encoder, and saves it in the `image_chunks` table.
This is a separate table from `text_chunks` because SigLIP vectors live in a
different "space" than the text-embedding vectors and can't be compared to them.

To search images with a text question, SigLIP's *text* encoder turns the
question into a vector in the same space as the image vectors (see --test-query).

Setup:
    pip install torch transformers pillow "psycopg[binary]" pgvector
    export DATABASE_URL=postgresql://postgres:pw@localhost:5432/fieldnote

Usage:
    python ingest_images.py out/all_figures_metadata.json
    python ingest_images.py out/all_figures_metadata.json \
        --test-query "tie down knockout dimensions"
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import psycopg
import torch
from pgvector.psycopg import register_vector
from PIL import Image
from psycopg.types.json import Jsonb
from transformers import AutoModel, AutoProcessor

DEFAULT_MODEL = "google/siglip-base-patch16-224"  # 768-dimensional vectors
TEXT_MAX_LEN = 64  # SigLIP's text encoder only reads the first 64 tokens


def to_vectors(output):
    """
    Turn the model output into a plain tensor of unit-length vectors.
    (Some transformers versions return a tensor, others wrap it in an object.)
    """
    if not torch.is_tensor(output):
        output = output.pooler_output
    return torch.nn.functional.normalize(output, dim=-1)  # unit length -> cosine search


def embed_images(model, processor, paths, device):
    images = [Image.open(p).convert("RGB") for p in paths]
    inputs = processor(images=images, return_tensors="pt").to(device)
    with torch.no_grad():
        return to_vectors(model.get_image_features(**inputs)).cpu().numpy()


def embed_text(model, processor, text, device):
    # SigLIP was trained with padding to exactly 64 tokens, so we must do the same
    inputs = processor(text=[text], padding="max_length", max_length=TEXT_MAX_LEN,
                       truncation=True, return_tensors="pt").to(device)
    with torch.no_grad():
        return to_vectors(model.get_text_features(**inputs)).cpu().numpy()[0]


def create_table(conn, dim):
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS image_chunks (
            chunk_id   TEXT PRIMARY KEY,
            modality   TEXT NOT NULL DEFAULT 'image',
            source     TEXT NOT NULL,   -- which PDF the figure came from
            page       INT,
            bbox       JSONB,           -- where on the page, for citations
            caption    TEXT,            -- the manual's own "Fig. N - ..." line, if found
            image_path TEXT NOT NULL,   -- the cropped PNG
            embedding  vector({dim}) NOT NULL
        )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS image_chunks_embedding_idx
                    ON image_chunks USING hnsw (embedding vector_cosine_ops)""")


def save_images(conn, records, vectors):
    """Insert rows; if a chunk_id already exists, overwrite it (safe to re-run)."""
    sql = """
        INSERT INTO image_chunks (chunk_id, source, page, bbox, caption, image_path, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chunk_id) DO UPDATE SET
            source = EXCLUDED.source,
            page = EXCLUDED.page,
            bbox = EXCLUDED.bbox,
            caption = EXCLUDED.caption,
            image_path = EXCLUDED.image_path,
            embedding = EXCLUDED.embedding"""
    rows = [
        (Path(r["image_path"]).stem, r["source_pdf"], r["page"], Jsonb(r["bbox"]),
         r.get("caption"), r["image_path"], np.asarray(v))
        for r, v in zip(records, vectors)
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def run_test_query(conn, model, processor, device, question, top_k=5):
    """Search the images with a text question and print the closest ones."""
    query_vector = embed_text(model, processor, question, device)
    sql = """SELECT source, page, caption, image_path, 1 - (embedding <=> %s) AS score
             FROM image_chunks ORDER BY embedding <=> %s LIMIT %s"""
    with conn.cursor() as cur:
        cur.execute(sql, [query_vector, query_vector, top_k])
        for source, page, caption, image_path, score in cur.fetchall():
            print(f"{score:.3f}  {source} p{page}  {caption!r}  {image_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata_json", help="all_figures_metadata.json from extract_figures.py")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--test-query", help="after ingesting, search images with this question")
    args = parser.parse_args()
    if not args.dsn:
        parser.error("Set DATABASE_URL or pass --dsn")

    with open(args.metadata_json) as f:
        records = json.load(f)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModel.from_pretrained(args.model).to(device).eval()
    processor = AutoProcessor.from_pretrained(args.model)

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        table_ready = False
        for start in range(0, len(records), args.batch_size):
            batch = records[start:start + args.batch_size]
            vectors = embed_images(model, processor, [r["image_path"] for r in batch], device)

            if not table_ready:  # we learn the vector size from the first batch
                create_table(conn, vectors.shape[1])
                register_vector(conn)  # lets psycopg send numpy arrays as pgvector values
                table_ready = True

            save_images(conn, batch, vectors)
            print(f"ingested {start + len(batch)}/{len(records)}")

        if args.test_query:
            print(f"\nTop images for: {args.test_query!r}")
            run_test_query(conn, model, processor, device, args.test_query)


if __name__ == "__main__":
    main()
