"""
reporters/sanitized.py
Produces an LLM-safe block with ALL environment data sanitized.
Engineers paste this directly into an LLM to get migration assistance
without exposing IPs, hostnames, credentials, or internal names.
"""
from __future__ import annotations
from core.events import EventBus, Level, Phase, sanitize
from analyzers.dependency_graph import VSProfile
from analyzers.compatibility import CompatibilityResult, Compat


def generate_sanitized_block(
    bus: EventBus,
    vs_profiles: dict[str, VSProfile],
    compat_results: list[CompatibilityResult],
    phase_filter: Phase | None = None,
) -> str:
    """
    Build the full sanitized output block.
    Replace: IPs → [IP_N], hostnames → [HOSTNAME_N], UUIDs → [UUID_N],
    credentials → [REDACTED], cert names → [CERT_N].
    The result is safe to paste into any LLM.
    """
    lines = [
        "=" * 70,
        "AVI → FORTIADC MIGRATION ANALYSIS",
        "All environment-specific values replaced with generic placeholders.",
        "Safe to paste into an LLM without exposing infrastructure details.",
        "=" * 70,
        "",
    ]

    # Summary counts
    counts = bus.summary_counts()
    lines.append("SUMMARY")
    lines.append("-" * 40)
    for level, count in counts.items():
        if count:
            lines.append(f"  {level}: {count}")
    lines.append("")

    # VS migration readiness
    auto   = sum(1 for p in vs_profiles.values() if p.can_auto_migrate)
    manual = len(vs_profiles) - auto
    lines += [
        "VIRTUAL SERVICE READINESS",
        "-" * 40,
        f"  Total VS:          {len(vs_profiles)}",
        f"  Auto-migratable:   {auto}",
        f"  Require manual:    {manual}",
        "",
    ]

    # Compatibility breakdown
    lines += ["COMPATIBILITY BREAKDOWN", "-" * 40]
    status_counts = {s.value: 0 for s in Compat}
    for r in compat_results:
        status_counts[r.status.value] += 1
    for status, count in status_counts.items():
        lines.append(f"  {status}: {count}")
    lines.append("")
    
    # VS Detailed Breakdown (Sanitized)
    lines += ["DETAILED VS STATUS (SAMPLE)", "-" * 40]
    for r in compat_results[:30]:
        if r.object_type == "virtualservice":
            links = f" (Links: {', '.join(r.associations)})" if r.associations else ""
            lines.append(f"  {r.status.value:7} {sanitize(r.object_name)}{links}")
    if len(compat_results) > 30:
        lines.append("  ...")
    lines.append("")

    # All MANUAL items — sanitized
    manual_items = [e for e in bus.all_events if e.level == Level.MANUAL]
    if manual_items:
        lines += [
            f"MANUAL MIGRATION ITEMS ({len(manual_items)})",
            "-" * 40,
            "These items cannot be automatically migrated and require human action.",
            "",
        ]
        for i, event in enumerate(manual_items, 1):
            lines.append(f"[MANUAL-{i:03d}] {sanitize(event.object_type)}/{sanitize(event.object_name)}")
            lines.append(f"  Issue:  {sanitize(event.message)}")
            detail = event.detail
            if "options" in detail:
                lines.append("  Options:")
                for opt in detail["options"]:
                    lines.append(f"    {opt}")
            if "action" in detail:
                lines.append(f"  Action: {sanitize(detail['action'])}")
            lines.append("")

    # All CRITICAL items — sanitized
    critical_items = [e for e in bus.all_events if e.level == Level.CRITICAL]
    if critical_items:
        lines += [
            f"CRITICAL BLOCKERS ({len(critical_items)})",
            "-" * 40,
        ]
        for i, event in enumerate(critical_items, 1):
            lines.append(f"[CRITICAL-{i:03d}] {sanitize(event.message)}")
            if event.detail.get("action"):
                lines.append(f"  Action: {sanitize(event.detail['action'])}")
            lines.append("")

    # WARN items — sanitized
    warn_items = [e for e in bus.all_events if e.level == Level.WARN]
    if warn_items:
        lines += [
            f"WARNINGS ({len(warn_items)}) — Review before migration",
            "-" * 40,
        ]
        for event in warn_items[:20]:   # cap at 20 in sanitized block
            lines.append(
                f"  [WARN] {sanitize(event.object_type)}: {sanitize(event.message)}"
            )
        if len(warn_items) > 20:
            lines.append(f"  ... and {len(warn_items)-20} more (see HTML report)")
        lines.append("")

    lines += [
        "=" * 70,
        "HOW TO USE THIS WITH AN LLM",
        "-" * 40,
        "1. Copy everything above this line",
        "2. Paste into your LLM with this prompt:",
        '   "I am migrating from Avi Networks LB to FortiADC. This is a',
        "    sanitized analysis of my Avi configuration. For each MANUAL item,",
        "    suggest the best approach to implement equivalent functionality in",
        '    FortiADC. Focus on [specific item] first."',
        "3. For DataScripts specifically, also paste the sanitized script code",
        "   using: python3 migrate.py sanitize-datascript --name [name]",
        "=" * 70,
    ]

    return "\n".join(lines)
