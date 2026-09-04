"""
services/llm_advisory_service.py
Thin wrapper around core/llm_gateway.py for UI consumption.

Design rules:
- Always returns a dict (never raises)
- LLM disabled → available: False, meaningful fallback message
- Never sends raw environment data — always sanitized via gateway
- Advisory only — no tool execution, no config changes
- Logs prompt hash to audit trail (not raw prompt)
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _load_discovery(dirs: dict, env: str) -> dict:
    disc = Path(dirs.get("discovery_dir", "discovery")) / f"{env}.json"
    if not disc.exists():
        return {}
    try:
        return json.loads(disc.read_text())
    except Exception:
        return {}


def _load_ledger(dirs: dict, env: str) -> dict:
    path = Path(dirs.get("state_dir", "state")) / f"{env}-ledger.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _gateway():
    """Lazy import — gateway only initialised if LLM features are actually called."""
    try:
        from core.llm_gateway import get_gateway
        return get_gateway()
    except Exception:
        return None


def get_advisory(dirs: dict, query: str, env: str = "") -> dict:
    """
    Answer a free-form question about the migration using the LLM gateway.
    Sanitizes all environment context before sending.
Returns: { available, content, prompt_hash, mode, error }
    """
    gw = _gateway()

    if gw is None or not gw.enabled:
        return {
            "available":    False,
            "content":      "",
            "prompt_hash":  "",
            "mode":         "disabled",
            "error":        "LLM advisory is disabled",
        }

    # Build sanitized context from discovery + ledger
    context_parts = []
    if env:
        disc   = _load_discovery(dirs, env)
        ledger = _load_ledger(dirs, env)

        vs_count    = len(disc.get("virtual_services",  []))
        pool_count  = len(disc.get("pools",             []))
        ds_count    = len(disc.get("datascripts",       []))
        cert_count  = len(disc.get("ssl_certificates",  []))
        open_items  = [i for i in ledger.get("unsupported_items", [])
                       if not i.get("resolved", False)]

        context_parts.append(
            f"Environment stats: {vs_count} VS, {pool_count} pools, "
            f"{ds_count} DataScripts, {cert_count} certificates."
        )
        if open_items:
            context_parts.append(
                f"Open manual items: {len(open_items)} — types: "
                f"{', '.join(set(i.get('object_type','?') for i in open_items[:5]))}."
            )

    context = " ".join(context_parts)

    # Augment context with RAG-retrieved knowledge chunks
    try:
        from services.rag_service import get_index_stats, search as rag_search
        rag_stats = get_index_stats(dirs)
        if rag_stats.get("ready"):
            rag_result = rag_search(dirs, query=query, top_k=4, rerank=True)
            for r in rag_result.get("results", []):
                excerpt = r.get("snippet", "")[:300]
                if excerpt:
                    context_parts.append(f"[KB: {r['title']}] {excerpt}")
            context = " ".join(context_parts)
    except Exception:
        pass  # RAG unavailable — proceed with env-stats context only

    try:
        from core.llm_gateway import enrich_unsupported_item
        resp = gw.call(
            prompt=query,
            context=context,
            cache_key=f"advisory:{hash(query) & 0xFFFF}:{env}",
        )
        return {
            "available":   resp.ok,
            "content":     resp.content if resp.ok else "",
            "prompt_hash": resp.prompt_hash,
            "mode":        resp.gateway_mode,
            "latency_ms":  resp.latency_ms,
            "error":       resp.error,
        }
    except Exception as e:
        return {
            "available":  False,
            "content":    "",
            "prompt_hash": "",
            "mode":       "error",
            "error":      str(e),
        }


def get_datascript_advice(dirs: dict, env: str, script_name: str) -> dict:
    """
    Get LLM advice for a specific DataScript migration.
    Returns deterministic classification + optional LLM enrichment.
    """
    disc = _load_discovery(dirs, env)
    datascripts = disc.get("datascripts", [])
    target = next((d for d in datascripts if d.get("name") == script_name), None)

    if not target:
        return {"error": f"DataScript '{script_name}' not found in {env} discovery"}

    scripts = target.get("datascript", [])
    code = "\n".join(s.get("script", "") for s in scripts)
    events = target.get("_events", [])
    line_count = target.get("_total_lines", 0)

    # Deterministic classification (always available, no LLM)
    from core.intelligence import build_llm_pack
    det_class = _classify_datascript(code, line_count)

    result = {
        "name":                script_name,
        "events":              events,
        "line_count":          line_count,
        "deterministic_class": det_class,
        "patterns":            extract_datascript_patterns(code),
        "llm_enrichment":      None,
    }

    gw = _gateway()
    if gw and gw.enabled:
        try:
            from core.llm_gateway import enrich_datascript_analysis
            enrichment = enrich_datascript_analysis(
                script_name=script_name,
                script_code=code,
                events=events,
                line_count=line_count,
                deterministic_classification=det_class,
                gateway=gw,
            )
            result["llm_enrichment"] = enrichment
        except Exception as e:
            result["llm_error"] = str(e)

    return result


def _classify_datascript(code: str, line_count: int) -> str:
    """Deterministic DataScript classification — no LLM needed."""
    code_lower = code.lower()

    complex_indicators = [
        "http.get_cookie", "http.set_cookie", "http.redirect",
        "avi.vs.", "avi.pool.", "math.", "string.find",
        "crypto", "auth", "rate_limit", "throttle",
    ]
    medium_indicators = [
        "http.add_header", "http.remove_header",
        "http.get_header", "http.replace",
        "avi.http.get_path", "avi.http.get_query",
    ]

    if line_count > 50 or sum(1 for i in complex_indicators if i in code_lower) >= 2:
        return "COMPLEX"
    if line_count > 20 or any(i in code_lower for i in medium_indicators):
        return "MEDIUM"
    return "SIMPLE"


def extract_datascript_patterns(code: str) -> dict:
    """Extract complex logic patterns from DataScript code (regex, loops, external calls)."""
    patterns = {
        "regex": [],
        "external_calls": [],
        "loops": [],
        "conditional_depth": 0
    }
    
    # Regex detection (approximate for Lua)
    # Looking for strings that look like regex patterns or contain regex operators
    regex_matches = re.findall(r'["\']([^"\']*[\*\+\?\|\(\)\[\]][^"\']*)["\']', code)
    if regex_matches:
        # Filter for things that are likely regex, not just file paths with dots
        likely_regex = [m for m in regex_matches if any(c in m for c in "*+?|()[]")]
        patterns["regex"] = list(set(likely_regex))
        
    # External calls / Specialized Avi API
    ext_indicators = ["avi.vs.", "avi.pool.", "avi.http.redirect", "avi.crypto", "avi.auth"]
    for ind in ext_indicators:
        if ind in code.lower():
            patterns["external_calls"].append(ind)
            
    # Loop detection
    if "for " in code or "while " in code:
        patterns["loops"].append("Iteration detected")
        
    # Complexity depth
    patterns["conditional_depth"] = code.count("if ") + code.count("elseif ")
    
    return patterns
