"""Stage 2: chunks.jsonl -> pgvector. Fast and re-runnable (replaces the table contents)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding
from pgvector.psycopg import register_vector
from psycopg import Connection

from app.config import settings

log = logging.getLogger("ingest.embed")

INSERT = """INSERT INTO chunks (chunk_id, source_filename, page_number, product, circular_no, fiscal_year,
            content_type, chunk_index, content, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""


def embed_texts(model: TextEmbedding, texts: list[str]) -> np.ndarray:
    vecs = np.asarray(list(model.embed(texts, batch_size=64)), dtype=np.float32)
    if vecs.shape[1] != settings.EMBED_DIM:
        raise SystemExit(f"INGEST ERROR: embedding dim {vecs.shape[1]} != EMBED_DIM {settings.EMBED_DIM}")
    # Explicit unit normalisation: cosine == dot product only for unit vectors; do not trust the library.
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-12, None)


def run(chunks_path: Path) -> int:
    rows = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    model = TextEmbedding(settings.EMBED_MODEL)
    vecs = embed_texts(model, [r["content"] for r in rows])
    with Connection.connect(settings.DATABASE_URL) as conn:
        register_vector(conn)
        with conn.transaction():
            conn.execute("DROP INDEX IF EXISTS chunks_embedding_hnsw")
            conn.execute("TRUNCATE chunks RESTART IDENTITY")
            with conn.cursor() as cur:
                cur.executemany(INSERT, [
                    (r["chunk_id"], r["source_filename"], r["page_number"], r["product"], r["circular_no"],
                     r["fiscal_year"], r["content_type"], r["chunk_index"], r["content"], v)
                    for r, v in zip(rows, vecs)
                ])
            # Built after the bulk insert: building first makes every insert pay for graph maintenance.
            conn.execute("CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
    log.info("inserted %d chunks and built HNSW index", len(rows))
    return len(rows)
