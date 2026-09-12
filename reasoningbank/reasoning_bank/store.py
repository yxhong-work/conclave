"""SQLite + NumPy vector store for reasoning memories.

A single-file SQLite database holds memory metadata and embedding vectors
(stored as JSON text). Cosine similarity is computed in-process with NumPy.
This is an O(n) scan per query — fine for a PoC bank that grows by at most
one memory per task. Append-only; no merge, no forget.
"""
import json
import os
import sqlite3
from datetime import datetime

import numpy as np

from reasoning_bank.models import MemoryItem


class Store:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "reasoningbank.db")
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id         TEXT PRIMARY KEY,
                    title      TEXT NOT NULL,
                    trigger    TEXT NOT NULL,
                    guidance   TEXT NOT NULL,
                    outcome    TEXT NOT NULL,
                    embedding  TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def insert(self, mem: MemoryItem) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    mem.id,
                    mem.title,
                    mem.trigger,
                    mem.guidance,
                    mem.outcome,
                    json.dumps(mem.embedding),
                    mem.created_at.isoformat(),
                ),
            )

    def search(
        self, query_vec: list[float], top_k: int, threshold: float = 0.0
    ) -> list[tuple[MemoryItem, float]]:
        """Return up to top_k memories with cosine similarity >= threshold,
        most similar first."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM memories").fetchall()

        if not rows:
            return []

        embeddings = np.array([json.loads(r["embedding"]) for r in rows], dtype=float)
        query = np.array(query_vec, dtype=float)
        query_norm = query / (np.linalg.norm(query) + 1e-12)
        emb_norms = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-12)
        sims = emb_norms @ query_norm

        order = np.argsort(sims)[::-1][:top_k]
        results: list[tuple[MemoryItem, float]] = []
        for i in order:
            sim = float(sims[i])
            if sim >= threshold:
                r = rows[int(i)]
                results.append(
                    (
                        MemoryItem(
                            id=r["id"],
                            title=r["title"],
                            trigger=r["trigger"],
                            guidance=r["guidance"],
                            outcome=r["outcome"],
                            embedding=None,
                            created_at=datetime.fromisoformat(r["created_at"]),
                        ),
                        sim,
                    )
                )
        return results
