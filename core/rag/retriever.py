"""
core/rag/retriever.py
Multi-stage retrieval pipeline: TF-IDF → BM25 rerank → MMR dedup.

Stages:
  1. TFIDFRetriever  — cosine similarity, returns top-K candidates
  2. BM25Reranker    — BM25 score on candidate set (improves precision)
  3. MMRFilter       — Maximal Marginal Relevance deduplication (improves diversity)

All stages are stdlib-only (no numpy, no scipy, no faiss).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable

from core.rag.document import Document, SearchResult
from core.rag.vectorstore import VectorStore, _tokenize


# ── BM25 Reranker ─────────────────────────────────────────────────────────────

# BM25 tuning constants (standard values)
_BM25_K1 = 1.5   # term frequency saturation
_BM25_B  = 0.75  # length normalization


def _bm25_score(
    query_tokens: list[str],
    doc_content:  str,
    avg_doc_len:  float,
    N:            int,
    df:           dict[str, int],
) -> float:
    """
    Compute BM25 score for a single document against a query.
    Uses IDF from the collection-level document frequency dict.
    """
    terms = _tokenize(doc_content)
    doc_len = len(terms)
    if doc_len == 0:
        return 0.0

    # Term frequency map for this doc
    tf_map: dict[str, int] = {}
    for t in terms:
        tf_map[t] = tf_map.get(t, 0) + 1

    score = 0.0
    for qt in query_tokens:
        if qt not in tf_map:
            continue
        tf  = tf_map[qt]
        idf = math.log((N - df.get(qt, 0) + 0.5) / (df.get(qt, 0) + 0.5) + 1.0)
        numerator   = tf * (_BM25_K1 + 1)
        denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / max(avg_doc_len, 1))
        score += idf * numerator / denominator

    return score


# ── MMR filter ────────────────────────────────────────────────────────────────

def _term_set(text: str) -> frozenset[str]:
    return frozenset(_tokenize(text))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _mmr_filter(
    results: list[SearchResult],
    top_k: int,
    diversity: float = 0.5,
) -> list[SearchResult]:
    """
    Maximal Marginal Relevance: selects results that are both
    relevant (high score) and diverse (low overlap with already-selected).

    diversity ∈ [0, 1]:
      0.0 = pure relevance (same as no MMR)
      1.0 = pure diversity
      0.5 = balanced (recommended default)
    """
    if len(results) <= 1:
        return results[:top_k]

    selected: list[SearchResult] = []
    remaining = list(results)
    term_sets = {r.chunk.id: _term_set(r.chunk.content) for r in results}

    while remaining and len(selected) < top_k:
        if not selected:
            # First pick: highest cosine score
            best = max(remaining, key=lambda r: r.score)
        else:
            # MMR pick: maximize (1-λ)·relevance - λ·max_similarity_to_selected
            selected_sets = [term_sets[s.chunk.id] for s in selected]

            def mmr_score(r: SearchResult) -> float:
                rel = r.score
                max_sim = max(
                    _jaccard(term_sets[r.chunk.id], ss)
                    for ss in selected_sets
                )
                return (1 - diversity) * rel - diversity * max_sim

            best = max(remaining, key=mmr_score)

        selected.append(best)
        remaining.remove(best)

    return selected


# ── Retriever ─────────────────────────────────────────────────────────────────

class Retriever:
    """
    Multi-stage retriever: TF-IDF → BM25 rerank → MMR dedup.

    Usage:
        retriever = Retriever(store)
        results = retriever.search("DataScript migration FortiADC", top_k=5)
    """

    def __init__(
        self,
        store: VectorStore,
        bm25_candidate_multiplier: int = 3,
        mmr_diversity: float = 0.5,
    ):
        """
        Args:
            store                      — the TF-IDF vector store
            bm25_candidate_multiplier  — fetch top_k * N candidates from TF-IDF
                                          before BM25 reranking
            mmr_diversity              — MMR diversity weight [0=relevance, 1=diversity]
        """
        self.store = store
        self._bm25_mult = bm25_candidate_multiplier
        self._mmr_div   = mmr_diversity

    def search(
        self,
        query:    str,
        top_k:    int = 6,
        filter:   dict | None = None,
        rerank:   bool = True,
        diversify: bool = True,
    ) -> list[SearchResult]:
        """
        Full retrieval pipeline.

        Args:
            query     — natural language question
            top_k     — final number of results
            filter    — metadata filter dict e.g. {"source_type": "docs"}
            rerank    — apply BM25 reranking
            diversify — apply MMR deduplication

        Returns list of SearchResult sorted by final score (descending).
        """
        if not query.strip():
            return []

        # Stage 1: TF-IDF candidate retrieval
        candidates_k = top_k * self._bm25_mult if rerank else top_k
        candidates = self.store.search(query, top_k=candidates_k, source_filter=filter)

        if not candidates:
            return []

        # Stage 2: BM25 rerank
        if rerank and len(candidates) > 1:
            candidates = self._bm25_rerank(query, candidates, top_k=top_k)

        # Stage 3: MMR deduplication
        if diversify and len(candidates) > 1:
            candidates = _mmr_filter(candidates, top_k=top_k, diversity=self._mmr_div)

        return candidates[:top_k]

    def _bm25_rerank(
        self,
        query:      str,
        candidates: list[SearchResult],
        top_k:      int,
    ) -> list[SearchResult]:
        """Rerank candidates using BM25 and blend with TF-IDF score."""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return candidates

        # Compute collection-level stats from candidates only
        N = len(candidates)
        df: dict[str, int] = {}
        for r in candidates:
            for term in set(_tokenize(r.chunk.content)):
                df[term] = df.get(term, 0) + 1

        avg_doc_len = (
            sum(len(_tokenize(r.chunk.content)) for r in candidates) / N
        )

        # Score each candidate
        reranked: list[SearchResult] = []
        for r in candidates:
            bm25 = _bm25_score(q_tokens, r.chunk.content, avg_doc_len, N, df)
            # Normalise BM25 to [0,1] range (approximate)
            norm_bm25 = bm25 / (bm25 + 10.0)  # smooth normalizer
            # Blend: 40% TF-IDF cosine + 60% BM25
            blended = 0.4 * r.score + 0.6 * norm_bm25
            reranked.append(SearchResult(
                chunk=r.chunk,
                score=blended,
                highlights=r.highlights,
                retrieval_method="bm25",
            ))

        reranked.sort(key=lambda r: r.score, reverse=True)
        return reranked[:top_k * 2]  # keep generous candidate pool for MMR

    def explain(self, query: str, result: SearchResult) -> dict:
        """
        Explain why a result was retrieved (debug / transparency).
        Returns scoring breakdown and matching terms.
        """
        q_tokens = _tokenize(query)
        doc_tokens = set(_tokenize(result.chunk.content))
        matching = [t for t in q_tokens if t in doc_tokens]
        return {
            "id":              result.chunk.id,
            "title":           result.chunk.title,
            "score":           result.score,
            "method":          result.retrieval_method,
            "matching_terms":  matching,
            "match_count":     len(matching),
            "query_terms":     q_tokens,
        }
