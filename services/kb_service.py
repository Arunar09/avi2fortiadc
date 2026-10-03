"""
services/kb_service.py
Knowledge base — wraps the RAG system for UI consumption.

Search is now RAG-powered (TF-IDF + BM25 + MMR) via services/rag_service.py.
Falls back to keyword scan if the RAG index has not been built yet.

Read-only. No LLM at service level — LLM enrichment is in llm_advisory_service.
"""
from __future__ import annotations

import json
from pathlib import Path

from services.common import canonical_env_name


# Docs that are useful operator references
_FEATURED_DOCS = {
    "00-PRODUCT-OVERVIEW.md":         "Product Overview & Scope",
    "USER-GUIDE.md":                  "End-to-End User Guide",
    "ARCHITECTURE.md":                "Architecture Overview",
    "TECHNICAL-ARCHITECTURE.md":      "Technical Architecture & Pipeline",
    "OBJECT-MAPPING-MATRIX.md":       "Avi to FortiADC Object Mapping Matrix",
    "UNSUPPORTED-FEATURES.md":        "Unsupported Features & Manual Actions",
    "DATASCRIPT-MIGRATION.md":        "DataScript Translation Guide",
    "GSLB-MIGRATION.md":              "GSLB Migration Guide",
    "SECURITY.md":                    "Security & Compliance Guide",
    "RUNBOOK.md":                     "Operations Runbook",
    "TROUBLESHOOTING.md":             "Troubleshooting Guide",
    "CLI-REFERENCE.md":               "CLI Reference",
    "WEB-CONSOLE.md":                 "Web Console Operator Guide",
    "OPERATING-MODEL.md":             "Operating Model & Roles",
    "OPERATIONAL-GLOSSARY.md":        "Operational Glossary",
    "QUALIFICATION.md":               "Evidence Qualification Matrix",
    "MASTER-KNOWLEDGE-INDEX.md":      "Master Knowledge Index",
}


def get_kb_summary(dirs: dict) -> dict:
    docs_dir  = Path(dirs.get("tool_root", ".")) / "docs"
    state_dir = Path(dirs.get("state_dir", "state"))
    # 1. Base counts from local files
    doc_count = len(list(docs_dir.glob("*.md"))) if docs_dir.exists() else 0
    featured_present = sum(1 for filename in _FEATURED_DOCS if (docs_dir / filename).exists())

    # Count resolved unsupported items from ledgers
    ledger_resolved = 0
    for ledger_file in (state_dir.glob("*-ledger.json") if state_dir.exists() else []):
        try:
            data = json.loads(ledger_file.read_text())
            ledger_resolved += sum(1 for i in data.get("unsupported_items", []) if i.get("resolved", False))
        except Exception: pass

    # 2. Augment with RAG stats if ready
    experience_count = 0
    total_chunks = 0
    try:
        from services.rag_service import get_index_stats
        rag_stats = get_index_stats(dirs)
        if rag_stats.get("ready"):
            by_src = rag_stats.get("by_source", {})
            experience_count = by_src.get("experience", 0)
            total_chunks = rag_stats.get("total_chunks", 0)
    except Exception: pass

    return {
        "doc_count":       doc_count,
        "resolved_items":  ledger_resolved + experience_count,
        "featured_count":  featured_present,
        "total_chunks":    total_chunks,
        "experience_only": experience_count
    }


def get_kb_entries(dirs: dict, query: str = "") -> list[dict]:
    """
    Return knowledge base entries matching the query (or all if query is empty).
    Primary: RAG-powered search (TF-IDF + BM25 + MMR).
    Fallback: legacy keyword scan if RAG index is not built.
    """
    # Try RAG first
    try:
        from services.rag_service import get_index_stats, get_kb_entries_rag
        stats = get_index_stats(dirs)
        if stats.get("ready"):
            return get_kb_entries_rag(dirs, query=query, top_k=20)
    except Exception:
        pass

    # Fallback: keyword scan over docs + ledger
    entries = []
    entries.extend(_get_doc_entries(query))
    entries.extend(_get_resolved_entries(dirs, query))
    return entries


def _get_doc_entries(query: str) -> list[dict]:
    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    if not docs_dir.exists():
        return []

    entries = []
    q = query.lower()

    for filename, label in _FEATURED_DOCS.items():
        path = docs_dir / filename
        if not path.exists():
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            content = ""

        if q and q not in content.lower() and q not in label.lower():
            continue

        # Extract first non-empty paragraph as excerpt
        excerpt = ""
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                excerpt = line[:200]
                break

        entries.append({
            "source":   "docs",
            "title":    label,
            "filename": filename,
            "excerpt":  excerpt,
            "tag":      _tag_for(filename),
        })

    return entries


def _tag_for(filename: str) -> str:
    lower = filename.lower()
    if "runbook" in lower or "operations" in lower:
        return "operations"
    if "security" in lower or "access" in lower or "governance" in lower:
        return "security"
    if "migration" in lower or "object" in lower:
        return "migration"
    if "troubleshoot" in lower:
        return "support"
    if "llm" in lower or "opsai" in lower:
        return "ai"
    return "reference"


def _get_resolved_entries(dirs: dict, query: str) -> list[dict]:
    state_dir = Path(dirs.get("state_dir", "state"))
    if not state_dir.exists():
        return []

    entries = []
    q = query.lower()

    for ledger_file in sorted(state_dir.glob("*-ledger.json")):
        env = canonical_env_name(ledger_file.name)
        try:
            data = json.loads(ledger_file.read_text())
        except Exception:
            continue

        for item in data.get("unsupported_items", []):
            if not item.get("resolved", False):
                continue
            title  = f"[{env}] {item.get('object_type','?')}: {item.get('object_name','?')}"
            reason = item.get("reason", "")
            note   = item.get("resolved_note", "")
            text   = f"{reason} {note}".lower()

            if q and q not in text and q not in title.lower():
                continue

            entries.append({
                "source":      "migration_learning",
                "title":       title,
                "filename":    "",
                "excerpt":     (note or reason)[:200],
                "tag":         "learned",
                "resolved_by": item.get("resolved_by", ""),
                "resolved_at": item.get("resolved_at", ""),
                "env":         env,
            })

    return entries


def get_doc_detail(dirs: dict, filename: str) -> dict | None:
    """Return document details and rendered HTML content safely."""
    safe_name = Path(filename).name
    if not safe_name.endswith(".md"):
        return None

    docs_dir = Path(dirs.get("tool_root", ".")) / "docs"
    path = docs_dir / safe_name
    if not path.is_file():
        path = Path(dirs.get("tool_root", ".")) / safe_name
        if not path.is_file():
            return None

    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    title = _FEATURED_DOCS.get(safe_name, safe_name.replace(".md", "").replace("-", " "))
    tag = _tag_for(safe_name)

    html_content = ""
    try:
        import markdown
        html_content = markdown.markdown(
            raw_text,
            extensions=["tables", "fenced_code", "nl2br", "sane_lists"]
        )
    except Exception:
        import html
        html_content = f"<pre style='white-space:pre-wrap;font-family:inherit;'>{html.escape(raw_text)}</pre>"

    return {
        "filename": safe_name,
        "title": title,
        "tag": tag,
        "html_content": html_content,
        "raw_text": raw_text,
        "line_count": len(raw_text.splitlines()),
        "size_kb": max(1, round(len(raw_text.encode("utf-8")) / 1024, 1)),
    }
