"""
core/rag/__init__.py
Retrieval-Augmented Generation (RAG) system for the Avi → FortiADC migration tool.

Zero external dependencies — stdlib only (sqlite3, json, math, re, hashlib).
Works fully offline / air-gapped.

Public API:
    from core.rag import get_retriever, query, index_all

Usage:
    # Build/rebuild the index (run once, or after new discovery runs)
    from core.rag import index_all
    stats = index_all(dirs)

    # Retrieve chunks relevant to a query
    from core.rag import get_retriever
    retriever = get_retriever(dirs)
    results = retriever.search("DataScript migration FortiADC", top_k=5)

    # Full RAG query (retrieval + optional LLM augmentation)
    from core.rag import query
    answer = query("How do I handle HSM certificates?", dirs=dirs)
"""
from __future__ import annotations

from core.rag.document import Document, SearchResult
from core.rag.vectorstore import VectorStore
from core.rag.retriever import Retriever
from core.rag.indexer import index_all, INDEX_SOURCES
from core.rag.augmentor import build_augmented_prompt, build_no_llm_summary


def get_retriever(dirs: dict) -> Retriever:
    """Return a Retriever bound to the project's RAG index."""
    from core.rag.vectorstore import VectorStore
    from pathlib import Path
    db_path = Path(dirs.get("state_dir", "state")) / "rag_index.db"
    store = VectorStore(str(db_path))
    return Retriever(store)


def query(
    question: str,
    dirs: dict,
    env: str = "",
    top_k: int = 6,
    use_llm: bool = True,
) -> dict:
    """
    Full RAG pipeline: retrieve relevant chunks → augment → call LLM.

    Returns:
        {
          answer: str,
          sources: list[dict],
          retrieved: int,
          mode: str,          # "rag+llm" | "rag-only" | "no-index" | "verified-experience"
          prompt_hash: str,
          latency_ms: int,
          verified: bool,
        }
    """
    import time
    t0 = time.monotonic()

    retriever = get_retriever(dirs)
    stats = retriever.store.get_stats()
    if stats["total_chunks"] == 0:
        return {
            "answer": "RAG index is empty. Run `migrate.py rag index` to build it.",
            "sources": [],
            "retrieved": 0,
            "mode": "no-index",
            "prompt_hash": "",
            "latency_ms": 0,
        }

    # Scoped filter: prefer env-specific chunks if env specified
    source_filter = {"env": env} if env else {}
    results = retriever.search(question, top_k=top_k, filter=source_filter)

    sources = [
        {
            "title":       r.chunk.title,
            "source_type": r.chunk.source_type,
            "score":       round(r.score, 4),
            "highlights":  r.highlights[:2],
        }
        for r in results
    ]

    # ── Verified Experience Bypass ────────────────────────────────────────────
    # If we have a very strong match from the 'experience' source, 
    # we return it directly, skipping the LLM call entirely.
    best_exp = next((r for r in results if r.chunk.source_type == "experience" and r.score > 0.9), None)
    if best_exp:
        return {
            "answer":      best_exp.chunk.content,
            "sources":     sources,
            "retrieved":   len(results),
            "mode":        "verified-experience",
            "prompt_hash": "",
            "latency_ms":  int((time.monotonic() - t0) * 1000),
            "verified":    True,
        }

    if not use_llm:
        summary = build_no_llm_summary(results)
        return {
            "answer":      summary,
            "sources":     sources,
            "retrieved":   len(results),
            "mode":        "rag-only",
            "prompt_hash": "",
            "latency_ms":  int((time.monotonic() - t0) * 1000),
        }

    # LLM augmentation
    try:
        from core.llm_gateway import get_gateway
        gw = get_gateway()
        if not gw.enabled:
            summary = build_no_llm_summary(results)
            return {
                "answer":      summary,
                "sources":     sources,
                "retrieved":   len(results),
                "mode":        "rag-only",
                "prompt_hash": "",
                "latency_ms":  int((time.monotonic() - t0) * 1000),
            }

        prompt = build_augmented_prompt(question, results)
        resp = gw.call(prompt, cache_key=f"rag:{hash(question) & 0xFFFF}")
        return {
            "answer":      resp.content if resp.ok else build_no_llm_summary(results),
            "sources":     sources,
            "retrieved":   len(results),
            "mode":        "rag+llm" if resp.ok else "rag-only",
            "prompt_hash": resp.prompt_hash,
            "latency_ms":  int((time.monotonic() - t0) * 1000),
        }
    except Exception as e:
        summary = build_no_llm_summary(results)
        return {
            "answer":      summary,
            "sources":     sources,
            "retrieved":   len(results),
            "mode":        "rag-only",
            "prompt_hash": "",
            "error":       str(e),
            "latency_ms":  int((time.monotonic() - t0) * 1000),
            "verified":    False,
        }


def chat(
    question: str,
    history_data: list[dict],
    dirs: dict,
    env: str = "",
    top_k: int = 6,
) -> dict:
    """
    Multi-turn RAG chat. 
    Maintains memory and context across a conversation.
    
    Args:
        question      — user's new message
        history_data  — list of serialized ChatMessage dicts
        dirs          — standard dirs dict
        
    Returns:
        {
          answer: str,
          history: list[dict],  # Updated history
          sources: list[dict],
          mode: str,
          latency_ms: int,
        }
    """
    import time
    t0 = time.monotonic()
    from core.rag.memory import ChatMemory

    # 1. Restore memory
    memory = ChatMemory.deserialize(history_data)
    memory.add_user_message(question)

    # 2. Retrieve context (always use the latest question for search)
    retriever = get_retriever(dirs)
    source_filter = {"env": env} if env else {}
    results = retriever.search(question, top_k=top_k, filter=source_filter)

    sources = [
        {
            "title":       r.chunk.title,
            "source_type": r.chunk.source_type,
            "score":       round(r.score, 4),
        }
        for r in results
    ]

    # ── Verified Experience Bypass (Chat) ─────────────────────────────────────
    best_exp = next((r for r in results if r.chunk.source_type == "experience" and r.score > 0.9), None)
    if best_exp:
        answer = best_exp.chunk.content
        memory.add_assistant_message(answer)
        return {
            "answer":      answer,
            "history":     memory.serialize(),
            "sources":     sources,
            "mode":        "verified-experience",
            "latency_ms":  int((time.monotonic() - t0) * 1000),
            "verified":    True,
        }

    # 3. Build Augmented Prompt with History
    from core.llm_gateway import get_gateway
    gw = get_gateway()
    
    if not gw.enabled:
        answer = build_no_llm_summary(results)
        memory.add_assistant_message(answer)
        return {
            "answer":      answer,
            "history":     memory.serialize(),
            "sources":     sources,
            "mode":        "rag-only",
            "latency_ms":  int((time.monotonic() - t0) * 1000),
        }

    # Assembling prompt: System Rules + Context + History + New Question
    history_str = memory.format_for_prompt()
    base_prompt = build_augmented_prompt(question, results)
    
    # Inject history into the prompt
    final_prompt = f"{base_prompt}\n\n{history_str}\n\nAssistant:"
    
    resp = gw.call(final_prompt, cache_key=f"chat:{hash(question + str(len(history_data))) & 0xFFFF}")
    
    answer = resp.content if resp.ok else build_no_llm_summary(results)
    memory.add_assistant_message(answer)

    return {
        "answer":      answer,
        "history":     memory.serialize(),
        "sources":     sources,
        "mode":        "rag+llm" if resp.ok else "rag-only",
        "prompt_hash": resp.prompt_hash,
        "latency_ms":  int((time.monotonic() - t0) * 1000),
    }


__all__ = [
    "Document",
    "SearchResult",
    "VectorStore",
    "Retriever",
    "index_all",
    "INDEX_SOURCES",
    "get_retriever",
    "query",
    "chat",
    "build_augmented_prompt",
    "build_no_llm_summary",
]
