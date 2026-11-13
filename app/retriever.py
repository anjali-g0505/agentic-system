"""Hybrid retrieval: pgvector cosine + in-process BM25, fused with RRF (k=60). Values fixed by the benchmark."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import numpy as np
from fastembed import TextEmbedding
from pgvector.psycopg import register_vector_async
from rank_bm25 import BM25Okapi

from app.config import settings
from app.db import pool

CANDIDATES = 20
TOP_K = 5
RRF_K = 60
TOKEN = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*")
CLAUSE = re.compile(r"(?m)^\s*(\d{1,2}(?:\.\d{1,2}){1,3})\.?\s")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


@dataclass
class Chunk:
    id: int
    source_filename: str
    page_number: int
    product: str
    circular_no: str | None
    content_type: str
    content: str

    def citation(self) -> dict:
        m = CLAUSE.search(self.content)
        return {"source_filename": self.source_filename, "page_number": self.page_number,
                "circular_no": self.circular_no, "clause": m.group(1) if m else None,
                "content_type": self.content_type, "product": self.product}


class HybridRetriever:
    def __init__(self) -> None:
        self.model: TextEmbedding | None = None
        self.chunks: list[Chunk] = []
        self.bm25: BM25Okapi | None = None

    async def load(self) -> None:
        async with pool.connection() as conn:
            cur = await conn.execute("""SELECT id, source_filename, page_number, product, circular_no, content_type,
                                        content FROM chunks ORDER BY id""")
            self.chunks = [Chunk(*r) for r in await cur.fetchall()]
        if self.chunks:
            self.bm25 = BM25Okapi([tokenize(c.content) for c in self.chunks])
        self.model = await asyncio.to_thread(TextEmbedding, settings.EMBED_MODEL)

    def _embed(self, text: str) -> np.ndarray:
        v = np.asarray(next(iter(self.model.embed([text]))), dtype=np.float32)
        return v / max(float(np.linalg.norm(v)), 1e-12)

    async def search(self, query: str, products: list[str]) -> list[Chunk]:
        if not self.chunks or self.bm25 is None or self.model is None:
            return []
        qvec = await asyncio.to_thread(self._embed, query)
        async with pool.connection() as conn:
            await register_vector_async(conn)
            # pgvector 0.8: keep scanning the HNSW graph until the product filter yields enough rows.
            await conn.execute("SET hnsw.iterative_scan = relaxed_order")
            cur = await conn.execute("""SELECT id FROM chunks WHERE product = ANY(%s)
                                        ORDER BY embedding <=> %s LIMIT %s""", (products, qvec, CANDIDATES))
            dense_ids = [r[0] for r in await cur.fetchall()]

        allowed = {i for i, c in enumerate(self.chunks) if c.product in products}
        scores = self.bm25.get_scores(tokenize(query))
        bm25_idx = sorted(allowed, key=lambda i: scores[i], reverse=True)[:CANDIDATES]
        bm25_ids = [self.chunks[i].id for i in bm25_idx if scores[i] > 0]

        fused: dict[int, float] = {}
        for ranking in (dense_ids, bm25_ids):
            for rank, cid in enumerate(ranking, start=1):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        by_id = {c.id: c for c in self.chunks}
        return [by_id[cid] for cid, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:TOP_K]]


retriever = HybridRetriever()
