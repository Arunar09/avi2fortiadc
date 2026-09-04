"""
core/rag/augmentor.py
Prompt augmentation — assembles retrieved context into a structured LLM prompt.

Two modes:
  1. build_augmented_prompt() — for LLM-augmented RAG (passes context + query to LLM)
  2. build_no_llm_summary()   — for retrieval-only mode (formats results as structured answer)
"""
from __future__ import annotations

from core.rag.document import SearchResult


# ── System context (injected into every RAG prompt) ────────────────────────────

_SYSTEM_CONTEXT = """\
You are an expert assistant for Avi Networks (NSX Advanced Load Balancer) to \
FortiADC migration projects. You help platform engineers understand migration \
findings, resolve blockers, and plan migration steps.

STRICT RULES:
1. Base ALL answers ONLY on the retrieved knowledge sections below.
2. If the answer is not in the retrieved knowledge, say explicitly: \
   "I don't have specific information about this in the knowledge base."
3. Never invent FortiADC CLI commands you are not certain about.
4. Always note when a recommendation requires human validation.
5. Keep answers concise and actionable. Use bullet points for steps.
6. All values in [BRACKETS] are sanitized placeholders — treat them as real values.

The following sections contain retrieved knowledge relevant to the question.\
"""


# ── Augmented prompt builder ───────────────────────────────────────────────────

def build_augmented_prompt(
    query:       str,
    results:     list[SearchResult],
    system_role: str = "migration",
    max_context_chars: int = 4000,
) -> str:
    """
    Build a structured RAG prompt for the LLM gateway.

    Format:
        [SYSTEM] role and rules
        [RETRIEVED KNOWLEDGE] top N chunks with source labels
        [QUESTION] the user's query

    Args:
        query              — the user's question
        results            — retrieved SearchResult list (pre-ranked)
        system_role        — future: allow "security" / "ops" variants
        max_context_chars  — cap total context length (compliance with max_prompt_chars)

    Returns a single string ready to pass to llm_gateway.call(prompt=...).
    """
    sections: list[str] = []

    # Retrieved knowledge sections
    chars_used = 0
    for i, result in enumerate(results, 1):
        source_label = _format_source_label(result)
        excerpt = result.chunk.content.strip()

        # Truncate individual chunks if needed
        available = max_context_chars - chars_used
        if available <= 100:
            break
        if len(excerpt) > available:
            excerpt = excerpt[:available] + "…"

        section = (
            f"[Source {i}: {source_label}]\n"
            f"{excerpt}"
        )
        sections.append(section)
        chars_used += len(section)

    knowledge_block = "\n\n".join(sections)

    prompt = (
        f"{_SYSTEM_CONTEXT}\n\n"
        f"{'─' * 60}\n"
        f"RETRIEVED KNOWLEDGE ({len(results)} source(s)):\n"
        f"{'─' * 60}\n\n"
        f"{knowledge_block}\n\n"
        f"{'─' * 60}\n"
        f"QUESTION:\n"
        f"{'─' * 60}\n"
        f"{query}\n\n"
        f"Answer based only on the retrieved knowledge above. "
        f"Cite which sources support your answer."
    )

    return prompt


def _format_source_label(result: SearchResult) -> str:
    """Short readable label for a search result."""
    src  = result.chunk.source_type
    path = result.chunk.source_path
    title = result.chunk.title
    score = round(result.score, 3)

    if src == "builtin":
        return f"Built-in Knowledge — {title} (relevance: {score})"
    if src == "docs":
        return f"Documentation: {path} — {title} (relevance: {score})"
    if src == "ledger":
        return f"Migration State — {title} (relevance: {score})"
    if src == "discovery":
        return f"Discovery Data — {title} (relevance: {score})"
    if src == "mappings":
        return f"Mapping Table — {title} (relevance: {score})"
    return f"{src}: {title} (relevance: {score})"


# ── No-LLM summary builder ────────────────────────────────────────────────────

def build_no_llm_summary(results: list[SearchResult]) -> str:
    """
    Format retrieved chunks as a structured Markdown-ready answer.
    Used when LLM is disabled or unavailable.
    """
    if not results:
        return (
            "### 🔍 Knowledge Base Search\n\n"
            "**No relevant information found** for this specific query.\n\n"
            "**Suggestions:**\n"
            "- Run `migrate.py rag index` to ensure all documentation is ingested.\n"
            "- Try more specific technical terms (e.g., 'GSLB' or 'Persistence').\n"
            "- Check the [Master Knowledge Index](/kb) for a broad overview."
        )

    lines: list[str] = [
        f"### 📚 Expert Knowledge Matches ({len(results)})\n",
        "*Retrieval-only mode active. Displaying the most relevant verified knowledge snippets.*\n",
        "---",
    ]

    for i, result in enumerate(results, 1):
        src_type = result.chunk.source_type
        title = result.chunk.title
        path = result.chunk.source_path
        
        # Premium labeling for Verified Experiences
        if src_type == "experience":
            label = f"✨ **Verified Expert Insight**: {title}"
        elif src_type == "ledger":
            label = f"📝 **Past Resolution**: {title}"
        elif src_type == "docs":
            label = f"📄 **Documentation**: {title} (`{path}`)"
        else:
            label = f"📦 **{src_type.title()}**: {title}"

        lines.append(f"\n{i}. {label}")
        
        # Content snippet
        if result.highlights:
            for h in result.highlights[:2]:
                lines.append(f"   - ...{h.strip()}...")
        else:
            content = result.chunk.content.strip()
            # If it's a long chunk, show first 400 chars
            if len(content) > 400:
                content = content[:400] + "..."
            lines.append(f"\n   > {content}\n")

    lines.append("\n---\n")
    lines.append(
        "> [!NOTE]\n"
        "> AI-Synthesis is currently disabled. These results are direct matches from your local knowledge base. "
        "To enable AI analysis, set `llm_gateway.enabled: true` in `config.yaml`."
    )

    return "\n".join(lines)
