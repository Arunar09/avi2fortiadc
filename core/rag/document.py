"""
core/rag/document.py
Data classes for RAG chunks and search results.
Stdlib only — no external dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    """
    A single indexed chunk from any source.

    Attributes:
        id            — unique stable ID (sha256 of source_path + chunk_index)
        source_type   — "docs" | "ledger" | "discovery" | "mappings"
        source_path   — file path the chunk came from (relative)
        title         — human-readable title for display in UI
        content       — full text content of this chunk
        metadata      — arbitrary extra fields (env, object_type, severity, etc.)
        chunk_index   — position within the source document (0-based)
    """
    id:           str
    source_type:  str
    source_path:  str
    title:        str
    content:      str
    metadata:     dict = field(default_factory=dict)
    chunk_index:  int  = 0

    def word_count(self) -> int:
        return len(self.content.split())

    def snippet(self, max_chars: int = 200) -> str:
        """Return a short excerpt of the content."""
        text = " ".join(self.content.split())
        return text[:max_chars] + ("…" if len(text) > max_chars else "")


@dataclass
class SearchResult:
    """
    A ranked retrieval result.

    Attributes:
        chunk            — the retrieved Document
        score            — cosine similarity in [0, 1] (higher = more relevant)
        highlights       — list of matching sentences extracted from chunk
        retrieval_method — "tfidf" | "bm25" | "exact"
    """
    chunk:            Document
    score:            float
    highlights:       list[str] = field(default_factory=list)
    retrieval_method: str = "tfidf"

    def to_dict(self) -> dict:
        return {
            "id":              self.chunk.id,
            "title":           self.chunk.title,
            "source_type":     self.chunk.source_type,
            "source_path":     self.chunk.source_path,
            "snippet":         self.chunk.snippet(),
            "score":           round(self.score, 4),
            "highlights":      self.highlights,
            "retrieval_method":self.retrieval_method,
            "metadata":        self.chunk.metadata,
        }
