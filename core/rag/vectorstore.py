"""
core/rag/vectorstore.py
SQLite-backed TF-IDF vector store.

No external dependencies — pure stdlib (sqlite3, json, math, re, hashlib).

Design:
  - Each document chunk stored as a row in SQLite
  - TF-IDF weight vector stored as a sparse JSON dict {term: weight}
  - Cosine similarity computed at query time (dot product / norms)
  - IDF computed globally and cached in index_meta table
  - Supports selective re-index by source_type (incremental updates)

Query performance:
  - For corpora < 10,000 chunks: linear scan is fast enough (~10-50ms)
  - IDF table cached in memory after first load
  - Query TF-IDF computed once per query, inner-looped over rows
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from core.rag.document import Document, SearchResult


# ── Text processing ────────────────────────────────────────────────────────────

# Common English stop words — filtered from TF-IDF index
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "it",
    "its", "this", "that", "these", "those", "i", "we", "you", "he", "she",
    "they", "them", "their", "our", "your", "my", "his", "her", "not",
    "no", "if", "then", "so", "than", "too", "very", "just", "also",
    "each", "which", "who", "all", "any", "both", "few", "more", "most",
    "such", "only", "same", "other", "into", "up", "out", "about",
})


def _tokenize(text: str) -> list[str]:
    """
    Tokenize text into lowercase alpha-numeric terms >= 2 chars,
    excluding stop words. Handles hyphenated terms (split on hyphen too).
    """
    raw = re.findall(r'\b[a-z][a-z0-9]{1,}\b', text.lower())
    # Also split hyphenated compound terms: "dry-run" -> "dry", "run"
    expanded: list[str] = []
    for token in raw:
        parts = token.split("-")
        expanded.extend(p for p in parts if len(p) >= 2)
    return [t for t in expanded if t not in _STOP_WORDS]


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Raw term frequency (count / total tokens)."""
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    n = len(tokens)
    return {t: c / n for t, c in counts.items()}


def _cosine_sim(v1: dict[str, float], v2: dict[str, float],
                norm1: float, norm2: float) -> float:
    """Dot product of two sparse TF-IDF vectors divided by their L2 norms."""
    if norm1 == 0 or norm2 == 0:
        return 0.0
    dot = sum(v1.get(t, 0.0) * w for t, w in v2.items())
    return dot / (norm1 * norm2)


def _l2_norm(v: dict[str, float]) -> float:
    return math.sqrt(sum(w * w for w in v.values()))


# ── Schema ─────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    tf_idf      TEXT NOT NULL DEFAULT '{}',
    norm        REAL NOT NULL DEFAULT 0.0,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_type ON chunks(source_type);

CREATE TABLE IF NOT EXISTS idf_cache (
    term TEXT PRIMARY KEY,
    idf  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ── VectorStore ────────────────────────────────────────────────────────────────

class VectorStore:
    """
    TF-IDF vector store backed by SQLite.

    Lifecycle:
        store = VectorStore("state/rag_index.db")
        store.upsert(docs)            # index documents
        results = store.search(query) # retrieve
        store.get_stats()             # introspect
    """

    def __init__(self, db_path: str):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._idf: dict[str, float] = {}
        self._idf_loaded = False
        # Ensure schema exists
        with self._conn() as conn:
            conn.executescript(_DDL)
            conn.commit()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ── IDF management ──────────────────────────────────────────────────────

    def _load_idf(self) -> None:
        """Load IDF table into memory cache."""
        with self._conn() as conn:
            rows = conn.execute("SELECT term, idf FROM idf_cache").fetchall()
        self._idf = {row["term"]: row["idf"] for row in rows}
        self._idf_loaded = True

    def _rebuild_idf(self, conn: sqlite3.Connection) -> None:
        """
        Recompute IDF from all indexed chunks and persist to idf_cache.
        Called after any upsert operation.
        """
        rows = conn.execute("SELECT tf_idf FROM chunks").fetchall()
        N = len(rows)
        if N == 0:
            return

        # Build document frequency counts
        df: dict[str, int] = {}
        for row in rows:
            terms = set(json.loads(row["tf_idf"]).keys())
            for term in terms:
                df[term] = df.get(term, 0) + 1

        # Smooth IDF: log((N + 1) / (df + 1)) + 1
        idf: dict[str, float] = {
            term: math.log((N + 1) / (count + 1)) + 1.0
            for term, count in df.items()
        }

        # Persist
        conn.execute("DELETE FROM idf_cache")
        conn.executemany(
            "INSERT INTO idf_cache (term, idf) VALUES (?, ?)",
            idf.items(),
        )

        # Recompute TF-IDF weighted vectors and norms for all chunks
        updates = []
        for row in conn.execute("SELECT id, tf_idf FROM chunks").fetchall():
            tf = json.loads(row["tf_idf"])
            tfidf = {t: tf_val * idf.get(t, 1.0) for t, tf_val in tf.items()}
            norm = _l2_norm(tfidf)
            updates.append((json.dumps(tfidf), norm, row["id"]))

        conn.executemany(
            "UPDATE chunks SET tf_idf = ?, norm = ? WHERE id = ?",
            updates,
        )

        # Invalidate in-memory cache
        self._idf = idf
        self._idf_loaded = True

    # ── Indexing ─────────────────────────────────────────────────────────────

    def upsert(self, docs: list[Document]) -> int:
        """
        Insert or replace documents. Rebuilds IDF after each batch.
        Returns number of documents written.
        """
        if not docs:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for doc in docs:
            tokens = _tokenize(doc.content + " " + doc.title)
            tf = _compute_tf(tokens)
            rows.append((
                doc.id,
                doc.source_type,
                doc.source_path,
                doc.title,
                doc.content,
                json.dumps(doc.metadata),
                json.dumps(tf),    # store raw TF first; TF-IDF computed after IDF rebuild
                0.0,               # norm placeholder
                now,
            ))

        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO chunks
                   (id, source_type, source_path, title, content,
                    metadata, tf_idf, norm, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            # Update index timestamp
            conn.execute(
                "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
                ("last_indexed", now),
            )
            self._rebuild_idf(conn)
            conn.commit()

        return len(docs)

    def delete_by_source(self, source_type: str) -> int:
        """Remove all chunks from a specific source type (for re-indexing)."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM chunks WHERE source_type = ?", (source_type,)
            )
            conn.commit()
            self._rebuild_idf(conn)
        self._idf_loaded = False
        return cur.rowcount

    # ── Search ───────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 8,
        source_filter: dict | None = None,
    ) -> list[SearchResult]:
        """
        Cosine-similarity search over the TF-IDF index.

        Args:
            query          — natural-language question
            top_k          — maximum results to return
            source_filter  — dict of metadata fields to filter on
                             e.g. {"source_type": "docs"} or {"env": "prod-a"}

        Returns list of SearchResult sorted by descending score.
        """
        if not self._idf_loaded:
            self._load_idf()

        # Build query TF-IDF vector
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        q_tf = _compute_tf(q_tokens)
        q_tfidf = {t: tf_val * self._idf.get(t, 1.0) for t, tf_val in q_tf.items()}
        q_norm = _l2_norm(q_tfidf)

        # Load all chunks (with optional SQL filter on source_type)
        sql = "SELECT id, source_type, source_path, title, content, metadata, tf_idf, norm FROM chunks"
        params: list = []
        if source_filter and "source_type" in source_filter:
            sql += " WHERE source_type = ?"
            params.append(source_filter["source_type"])

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        results: list[SearchResult] = []
        for row in rows:
            # Apply metadata filters (env, severity, etc.)
            if source_filter:
                meta = json.loads(row["metadata"])
                skip = False
                for k, v in source_filter.items():
                    if k == "source_type":
                        continue
                    if meta.get(k) != v:
                        skip = True
                        break
                if skip:
                    continue

            doc_tfidf = json.loads(row["tf_idf"])
            doc_norm = float(row["norm"])
            score = _cosine_sim(q_tfidf, doc_tfidf, q_norm, doc_norm)

            if score <= 0:
                continue

            doc = Document(
                id=row["id"],
                source_type=row["source_type"],
                source_path=row["source_path"],
                title=row["title"],
                content=row["content"],
                metadata=json.loads(row["metadata"]),
            )
            highlights = _extract_highlights(row["content"], q_tokens)
            results.append(SearchResult(
                chunk=doc,
                score=score,
                highlights=highlights,
                retrieval_method="tfidf",
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return index statistics for display/debugging."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            by_source = {}
            for row in conn.execute(
                "SELECT source_type, COUNT(*) as n FROM chunks GROUP BY source_type"
            ).fetchall():
                by_source[row["source_type"]] = row["n"]

            last_row = conn.execute(
                "SELECT value FROM index_meta WHERE key = 'last_indexed'"
            ).fetchone()
            last_indexed = last_row["value"] if last_row else None

            idf_terms = conn.execute("SELECT COUNT(*) FROM idf_cache").fetchone()[0]

        age_hours = None
        if last_indexed:
            try:
                from datetime import datetime, timezone
                ts = datetime.fromisoformat(last_indexed)
                delta = datetime.now(timezone.utc) - ts
                age_hours = round(delta.total_seconds() / 3600, 1)
            except Exception:
                pass

        return {
            "total_chunks":  total,
            "by_source":     by_source,
            "idf_terms":     idf_terms,
            "last_indexed":  last_indexed,
            "age_hours":     age_hours,
            "db_path":       self._path,
        }


# ── Highlight extraction ───────────────────────────────────────────────────────

def _extract_highlights(content: str, query_tokens: list[str],
                         max_highlights: int = 3) -> list[str]:
    """
    Extract sentences containing query terms as highlights.
    Prefers sentences with multiple query term hits.
    """
    sentences = re.split(r'(?<=[.!?\n])\s+', content)
    scored = []
    q_set = set(query_tokens)
    for sent in sentences:
        sent = sent.strip()
        if not sent or len(sent) < 20:
            continue
        hit_count = sum(1 for t in _tokenize(sent) if t in q_set)
        if hit_count > 0:
            scored.append((hit_count, sent[:300]))

    scored.sort(reverse=True)
    return [s for _, s in scored[:max_highlights]]
