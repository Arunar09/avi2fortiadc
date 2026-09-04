"""
services/rag_service.py
UI integration layer for the RAG system.

Keeps the same contractual style as kb_service.py:
  - Always returns a dict (never raises to caller)
  - Includes graceful fallback if index not built
  - No UI framework dependencies — plain Python dicts

Public API:
    search(dirs, query, top_k, source_filter)  → search results list
    ask(dirs, query, env, use_llm)             → full RAG Q&A
    rebuild_index(dirs, sources, rebuild)       → index build result
    get_index_stats(dirs)                       → index health
"""
from __future__ import annotations

import json
import time
from pathlib import Path


# ── Internal helpers ──────────────────────────────────────────────────────────

def _store(dirs: dict):
    """Return a VectorStore for the project's RAG index."""
    from core.rag.vectorstore import VectorStore
    db_path = Path(dirs.get("state_dir", "state")) / "rag_index.db"
    return VectorStore(str(db_path))


def _retriever(dirs: dict):
    from core.rag.retriever import Retriever
    return Retriever(_store(dirs))


def _get_env_snapshot(dirs: dict, env: str) -> str:
    """Return a high-density, sanitized summary of the environment state."""
    if not env:
        return ""
    
    try:
        from services.pipeline_service import get_pipeline_state
        state = get_pipeline_state(dirs, env)
        discovery = state.get("discovery", {})
        
        objects = discovery.get("object_counts", {})
        counts_str = ", ".join([f"{k}: {v}" for k, v in objects.items() if v > 0])
        
        # Get incident summaries
        events = state.get("events", [])
        criticals = [e["message"] for e in events if e["level"] == "CRITICAL"][:5]
        
        snapshot = [
            f"--- LIVE DATA CONTEXT: {env.upper()} ---",
            f"Topology: {counts_str or 'No assets found yet.'}",
            f"Recent Issues: {'; '.join(criticals) or 'Healthy'}",
            f"Phase: {state.get('current_phase', 'unknown')}",
            "---------------------------------------"
        ]
        return "\n".join(snapshot)
    except Exception:
        return ""


# ── Public service functions ──────────────────────────────────────────────────

def search(
    dirs:          dict,
    query:         str,
    top_k:         int = 8,
    source_filter: str = "",
    rerank:        bool = True,
    diversify:     bool = True,
) -> dict:
    """
    Search the RAG knowledge base without LLM augmentation.

    Args:
        dirs          — standard dirs dict
        query         — search query string
        top_k         — max results to return
        source_filter — limit to a specific source type
                        ("docs" | "ledger" | "discovery" | "mappings" | "builtin")
        rerank        — apply BM25 reranking
        diversify     — apply MMR deduplication

    Returns:
        {
          results:     list[dict],    # SearchResult.to_dict() for each hit
          query:       str,
          total:       int,
          index_stats: dict,
          error:       str | None,
        }
    """
    try:
        retriever = _retriever(dirs)
        stats = retriever.store.get_stats()

        if stats["total_chunks"] == 0:
            return {
                "results":     [],
                "query":       query,
                "total":       0,
                "index_stats": stats,
                "error":       "Index empty — run `migrate.py rag index` first.",
            }

        sf = {"source_type": source_filter} if source_filter else None
        results = retriever.search(
            query, top_k=top_k, filter=sf,
            rerank=rerank, diversify=diversify,
        )
        return {
            "results":     [r.to_dict() for r in results],
            "query":       query,
            "total":       len(results),
            "index_stats": stats,
            "error":       None,
        }
    except Exception as e:
        return {
            "results": [],
            "query":   query,
            "total":   0,
            "index_stats": {},
            "error":   str(e),
        }


def ask(
    dirs:    dict,
    query:   str,
    env:     str  = "",
    top_k:   int  = 6,
    use_llm: bool = True,
) -> dict:
    """
    Full RAG pipeline: retrieve → augment → LLM (optional).

    Args:
        dirs    — standard dirs dict
        query   — the user's question
        env     — if specified, prioritize env-scoped chunks
        top_k   — max chunks to retrieve
        use_llm — whether to call LLM gateway

    Returns:
        {
          answer:      str,
          sources:     list[dict],
          retrieved:   int,
          mode:        str,          # "rag+llm" | "rag-only" | "no-index"
          prompt_hash: str,
          latency_ms:  int,
          error:       str | None,
        }
    """
    try:
        from core.rag import query as rag_query
        # Inject live context into the user query
        snapshot = _get_env_snapshot(dirs, env)
        full_query = f"{snapshot}\n\nQuestion: {query}" if snapshot else query
        
        return rag_query(full_query, dirs=dirs, env=env, top_k=top_k, use_llm=use_llm)
    except Exception as e:
        return {
            "answer":      f"RAG system error: {e}",
            "sources":     [],
            "retrieved":   0,
            "mode":        "error",
            "prompt_hash": "",
            "latency_ms":  0,
            "error":       str(e),
        }


def chat(
    dirs:    dict,
    question: str,
    history: list[dict],
    env:     str = "",
) -> dict:
    """
    Multi-turn RAG chat wrapper for UI use.
    
    Returns:
        {
          answer: str,
          history: list[dict],
          sources: list[dict],
          mode: str,
          error: str | None
        }
    """
    try:
        from core.rag import chat as rag_chat
        # For chat, we only inject the snapshot if it's the start of the session
        # or if specifically requested. For now, we inject it for freshness.
        snapshot = _get_env_snapshot(dirs, env)
        full_question = f"Context: {snapshot}\n\n{question}" if snapshot else question
        
        return rag_chat(full_question, history_data=history, dirs=dirs, env=env)
    except Exception as e:
        return {
            "answer":      f"Chat service error: {e}",
            "history":     history,
            "sources":     [],
            "mode":        "error",
            "error":       str(e),
        }


def rebuild_index(
    dirs:    dict,
    sources: list[str] | None = None,
    rebuild: bool = True,
) -> dict:
    """
    Build or rebuild the RAG index.

    Args:
        dirs    — standard dirs dict
        sources — list of source types to index; None = all
                  valid: "docs", "ledger", "discovery", "mappings", "builtin", "code"
        rebuild — if True, delete existing chunks before re-indexing

    Returns:
        {
          indexed:     int,
          by_source:   dict,
          duration_ms: int,
          errors:      list[str],
          db_path:     str,
        }
    """
    try:
        from core.rag.indexer import index_all
        return index_all(dirs, sources=sources, rebuild=rebuild)
    except Exception as e:
        return {
            "indexed":     0,
            "by_source":   {},
            "duration_ms": 0,
            "errors":      [str(e)],
            "db_path":     "",
        }


def get_index_stats(dirs: dict) -> dict:
    """
    Return health and size statistics for the RAG index.

    Returns:
        {
          total_chunks: int,
          by_source:    dict,
          idf_terms:    int,       # vocabulary size
          last_indexed: str,       # ISO timestamp
          age_hours:    float,
          db_path:      str,
          ready:        bool,      # True if index has content
        }
    """
    try:
        stats = _store(dirs).get_stats()
        stats["ready"] = stats.get("total_chunks", 0) > 0
        return stats
    except Exception as e:
        return {
            "total_chunks": 0,
            "by_source":    {},
            "idf_terms":    0,
            "last_indexed": None,
            "age_hours":    None,
            "db_path":      "",
            "ready":        False,
            "error":        str(e),
        }


def get_kb_entries_rag(
    dirs:  dict,
    query: str = "",
    top_k: int = 20,
) -> list[dict]:
    """
    Drop-in replacement for kb_service.get_kb_entries() using RAG search.
    Returns results in the same dict format as kb_service for UI compatibility.
    """
    try:
        retriever = _retriever(dirs)
        stats = retriever.store.get_stats()
        if stats["total_chunks"] == 0:
            return []

        results = retriever.search(query or "migration FortiADC overview", top_k=top_k)
        return [
            {
                "source":   r.chunk.source_type,
                "title":    r.chunk.title,
                "filename": r.chunk.source_path,
                "excerpt":  r.chunk.snippet(200),
                "tag":      r.chunk.metadata.get("tag", r.chunk.source_type),
                "score":    round(r.score, 4),
            }
            for r in results if r.chunk.source_type != "code"
        ]
    except Exception:
        return []


def save_experience(
    dirs:     dict,
    title:    str,
    content:  str,
    tags:     list[str] | None = None,
) -> dict:
    """
    Save a verified AI insight to the local RAG knowledge base.
    This creates a new markdown file in state/experiences/.
    """
    try:
        from core.events import sanitize
        state_dir = Path(dirs.get("state_dir", "state"))
        exp_dir = state_dir / "experiences"
        exp_dir.mkdir(parents=True, exist_ok=True)
        
        # Safe filename
        safe_title = "".join([c if c.isalnum() else "_" for c in title]).strip("_")[:50]
        filename = f"{int(time.time())}_{safe_title}.md"
        filepath = exp_dir / filename
        
        tags_str = ", ".join(tags) if tags else "insight, verified"
        
        md_content = [
            f"# {title}",
            f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Tags: {tags_str}",
            "",
            "## Verified Insight",
            content,
            "",
            "---",
            "*This experience was verified by an engineer and is reused as a primary source for future similar queries.*"
        ]
        
        with open(filepath, "w") as f:
            f.write("\n".join(md_content))
            
        # Trigger incremental re-index for the experiences source
        try:
            rebuild_index(dirs, sources=["experience"], rebuild=False)
        except Exception:
            # Non-blocking error for indexing; the file is still saved.
            pass
            
        return {"success": True, "path": str(filepath)}
    except Exception as e:
        return {"success": False, "error": str(e)}

