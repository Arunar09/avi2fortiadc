"""
core/intelligence.py
Built-in heuristic intelligence layer — no LLM required.

The tool is intelligent WITHOUT an LLM by applying:
  1. Pattern library   — known Avi config patterns and their implications
  2. Complexity scoring — rates each VS migration 1-10 automatically
  3. Ordered next steps — deterministic "what to do now" list from findings
  4. Contextual advice  — recommendations based on what was actually found
  5. LLM pack builder  — prepares optimised prompts for external LLM use

The sanitized output feeds into the LLM pack so engineers can paste
directly into any external LLM (ChatGPT, Claude, internal OpsAI)
without exposing environment details.

The OpsAI connection: this module's output can be fed directly into
the OpsAI knowledge base (docs/platform/) to make OpsAI aware of the
migration state and able to answer questions about it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.events import EventBus, Level, sanitize
from analyzers.dependency_graph import VSProfile
from analyzers.compatibility import CompatibilityResult, Compat


# ── Known patterns ────────────────────────────────────────────────────────────
# Each pattern: condition function + explanation + recommendation
# These are domain knowledge encoded as rules — the "intelligence" without LLM.

@dataclass
class Pattern:
    id:             str
    name:           str
    detected:       bool = False
    severity:       str  = "INFO"   # INFO | WARN | CRITICAL
    explanation:    str  = ""
    recommendation: str  = ""
    affected:       list[str] = field(default_factory=list)


def detect_patterns(discovery: dict,
                    vs_profiles: dict[str, VSProfile],
                    bus: EventBus) -> list[Pattern]:
    """
    Apply all known pattern detectors. Returns list of detected patterns.
    These run WITHOUT any LLM — purely from API data + mapping rules.
    """
    patterns: list[Pattern] = []

    # ── P01: DataScript concentration risk ────────────────────────────────
    ds_vses = [n for n, p in vs_profiles.items() if p.datascripts]
    patterns.append(Pattern(
        id="P01", name="DataScript Concentration",
        detected=bool(ds_vses),
        severity="CRITICAL" if len(ds_vses) > 3 else "WARN",
        explanation=(
            f"{len(ds_vses)} Virtual Service(s) use DataScripts which cannot "
            f"be auto-migrated. These block automated migration completely. "
            f"Each DataScript requires analysis and manual reimplementation."
        ),
        recommendation=(
            "1. Prioritise DataScript analysis before any other migration work.\n"
            "2. For each DataScript: identify the business function it performs.\n"
            "3. Determine if FortiADC content routing can replace it.\n"
            "4. If not, move logic to the application or use WAF rules.\n"
            "5. Use the 'migrate.py llm-pack datascript --name X' command "
            "to generate a sanitized version safe for external LLM assistance."
        ),
        affected=ds_vses,
    ))

    # ── P02: HSM certificate cascade risk ────────────────────────────────
    hsm_certs = [c["name"] for c in discovery.get("ssl_certificates", [])
                 if not c.get("_exportable", True)]
    hsm_vs    = [n for n, p in vs_profiles.items()
                 if any(c in p.ssl_certs for c in hsm_certs)]
    patterns.append(Pattern(
        id="P02", name="HSM Certificate Cascade",
        detected=bool(hsm_certs),
        severity="CRITICAL" if hsm_certs else "INFO",
        explanation=(
            f"{len(hsm_certs)} HSM-backed certificate(s) found. "
            f"These certificates have non-exportable private keys — "
            f"they cannot be moved to FortiADC. "
            f"{len(hsm_vs)} Virtual Service(s) depend on them and will be blocked."
        ),
        recommendation=(
            "1. Engage Wintel team immediately to request replacement certificates.\n"
            "2. New certs must be issued from internal CA.\n"
            "3. Import new certs into FortiADC BEFORE migration window.\n"
            "4. Update DNS/application config if certificate CN/SANs change.\n"
            "5. Keep Avi running until new certs are validated in FortiADC."
        ),
        affected=hsm_certs + [f"VS: {v}" for v in hsm_vs],
    ))

    # ── P03: No health monitors on pools ─────────────────────────────────
    unhealthy_pools = []
    for pool in discovery.get("pools", []):
        hm_refs = pool.get("health_monitor_refs", [])
        if not hm_refs and pool.get("_member_count", 0) > 0:
            unhealthy_pools.append(pool.get("name", "?"))
    patterns.append(Pattern(
        id="P03", name="Pools Without Health Monitors",
        detected=bool(unhealthy_pools),
        severity="WARN",
        explanation=(
            f"{len(unhealthy_pools)} pool(s) have no health monitor configured. "
            f"FortiADC will send traffic to all members regardless of health. "
            f"This is usually a misconfiguration in Avi that will carry over."
        ),
        recommendation=(
            "Before migrating these pools, add HTTP or TCP health monitors. "
            "Migrate the health monitor to FortiADC first, then associate it "
            "with the pool during migration."
        ),
        affected=unhealthy_pools,
    ))

    # ── P04: Expired or near-expiry certificates ──────────────────────────
    expiring = []
    for cert in discovery.get("ssl_certificates", []):
        days = cert.get("_days_until_expiry")
        if days is not None and days < 60:
            expiring.append(f"{cert['name']} || {days}d remaining")
    patterns.append(Pattern(
        id="P04", name="Certificate Expiry Risk",
        detected=bool(expiring),
        severity="CRITICAL" if any("EXPIRED" in e or "(-" in e for e in expiring) else "WARN",
        explanation=(
            f"{len(expiring)} certificate(s) expire within 60 days. "
            f"Migrating to FortiADC with near-expiry certs risks service outage "
            f"shortly after migration — doubly disruptive."
        ),
        recommendation=(
            "Renew all certificates expiring within 90 days BEFORE migration. "
            "Treat cert renewal as a prerequisite, not a parallel activity."
        ),
        affected=expiring,
    ))

    # ── P05: Mixed HTTP/HTTPS on same VIP ────────────────────────────────
    vip_map: dict[str, list[str]] = {}
    for vs in discovery.get("virtual_services", []):
        for vip in vs.get("_vips", []):
            vip_map.setdefault(vip, []).append(vs.get("name", "?"))
    shared_vips = {vip: vses for vip, vses in vip_map.items() if len(vses) > 1}
    patterns.append(Pattern(
        id="P05", name="Shared VIP Addresses",
        detected=bool(shared_vips),
        severity="WARN",
        explanation=(
            f"{len(shared_vips)} VIP address(es) shared across multiple "
            f"Virtual Services. These must be migrated together or traffic "
            f"routing will break for the remaining VS on the old platform."
        ),
        recommendation=(
            "Migrate all Virtual Services sharing a VIP in the same maintenance window. "
            "Do not split-migrate VSes that share an IP address."
        ),
        affected=[f"{vip} || {', '.join(vses)}" for vip, vses in shared_vips.items()],
    ))

    # ── P06: Members currently down ───────────────────────────────────────
    degraded_pools = []
    for pool in discovery.get("pools", []):
        up   = pool.get("_members_up", 0)
        total = pool.get("_member_count", 0)
        if total > 0 and up < total:
            degraded_pools.append(
                f"{pool['name']} || {up}/{total} members up"
            )
    patterns.append(Pattern(
        id="P06", name="Degraded Pool Members",
        detected=bool(degraded_pools),
        severity="WARN",
        explanation=(
            f"{len(degraded_pools)} pool(s) have members currently down. "
            f"Migrating a degraded pool to FortiADC copies the problem. "
            f"FortiADC will correctly detect and route around down members "
            f"if health monitors are configured."
        ),
        recommendation=(
            "Investigate and restore down members before migration if possible. "
            "If members are intentionally disabled, note this for FortiADC config. "
            "Ensure health monitors are configured so FortiADC tracks member state."
        ),
        affected=degraded_pools,
    ))

    # ── P07: GSLB in use ─────────────────────────────────────────────────
    gslb_services = discovery.get("gslb_services", [])
    patterns.append(Pattern(
        id="P07", name="GSLB Configuration",
        detected=bool(gslb_services),
        severity="CRITICAL" if gslb_services else "INFO",
        explanation=(
            f"{len(gslb_services)} GSLB service(s) in use. "
            f"FortiADC GSLB uses a fundamentally different model than Avi. "
            f"This requires a separate design exercise, not just migration."
        ),
        recommendation=(
            "Engage network team and FortiADC SE for GSLB design session. "
            "GSLB migration is out of scope for this tool and must be planned separately. "
            "Avi GSLB must remain live until FortiGSLB or DNS-based GSLB is validated."
        ),
        affected=[s.get("name", "?") for s in gslb_services],
    ))

    # ── P08: OpenStack cloud integration ─────────────────────────────────
    openstack_conns = [c for c in discovery.get("connections", [])
                       if c.get("type") == "cloud"]
    patterns.append(Pattern(
        id="P08", name="OpenStack VIP Management",
        detected=bool(openstack_conns),
        severity="WARN",
        explanation=(
            f"Avi is integrated with OpenStack for automatic VIP allocation (IPAM). "
            f"FortiADC does not integrate with OpenStack. All VIPs currently "
            f"managed by Avi IPAM must be pre-allocated as fixed IPs in OpenStack "
            f"Neutron and configured statically in FortiADC."
        ),
        recommendation=(
            "1. List all VIPs from Avi using: migrate.py discover --output-vips.\n"
            "2. Reserve each VIP as a fixed IP in the correct Neutron network.\n"
            "3. Coordinate with IPNS team to prevent IP conflicts.\n"
            "4. Configure FortiADC virtual servers with static VIP assignments.\n"
            "5. Remove VIPs from Avi IPAM ONLY after FortiADC is validated."
        ),
        affected=[c.get("name", "?") for c in openstack_conns],
    ))

    # ── P09: Identical DataScript bodies (shared-profile quick win) ───────
    # Hash each script body; if multiple DS sets share the same body across
    # tenants, one FortiADC HTTP Profile covers all of them.
    import hashlib
    body_registry: dict[str, list[str]] = {}
    for ds_set in discovery.get("datascripts", []):
        for script in ds_set.get("datascript", []) if isinstance(ds_set, dict) else []:
            body = str(script.get("script", "")).strip()
            if body:
                h = hashlib.md5(body.encode()).hexdigest()
                body_registry.setdefault(h, []).append(ds_set.get("name", "?"))
    duplicated = {h: names for h, names in body_registry.items() if len(names) > 1}
    duplicate_groups = [
        f"{len(names)} sets share identical body || {', '.join(names)}"
        for names in duplicated.values()
    ]
    patterns.append(Pattern(
        id="P09", name="Identical DataScript Bodies (Quick Win)",
        detected=bool(duplicated),
        severity="WARN",
        explanation=(
            f"{len(duplicated)} group(s) of DataScripts with identical Lua bodies "
            f"were found across multiple VS/tenants. "
            f"These can be replaced by a single shared FortiADC HTTP Profile or "
            f"Content Rewriting rule — configure once, reference everywhere."
        ),
        recommendation=(
            "1. Identify the shared logic (header injection, URL rewriting, etc.).\n"
            "2. Create one FortiADC HTTP Profile or Content Rewriting Rule for it.\n"
            "3. Attach that profile to all Virtual Servers in the group.\n"
            "4. This covers all VS in the group with a single config change — "
            "do this before tackling unique DataScripts."
        ),
        affected=duplicate_groups,
    ))

    # ── P10: External / SCTP health monitors ─────────────────────────────
    external_hms = [
        hm.get("name", "?")
        for hm in discovery.get("health_monitors", [])
        if hm.get("type", "") in ("HEALTH_MONITOR_EXTERNAL", "HEALTH_MONITOR_SCTP")
    ]
    sctp_hms = [
        hm.get("name", "?")
        for hm in discovery.get("health_monitors", [])
        if hm.get("type", "") == "HEALTH_MONITOR_SCTP"
    ]
    patterns.append(Pattern(
        id="P10", name="External / SCTP Health Monitors",
        detected=bool(external_hms),
        severity="CRITICAL" if sctp_hms else "WARN",
        explanation=(
            f"{len(external_hms)} health monitor(s) use types FortiADC cannot replicate directly "
            f"(External script-based or SCTP). "
            + (f"{len(sctp_hms)} are SCTP monitors — FortiADC has no SCTP support at all. " if sctp_hms else "")
            + "These pools will have no health checking after migration unless replacements are designed."
        ),
        recommendation=(
            "For each External monitor:\n"
            "  - HTTP status check → replace with FortiADC HTTP health monitor.\n"
            "  - Response body match → use FortiADC HTTP monitor with 'match-type content'.\n"
            "  - Custom script logic → redesign as closest HTTP/TCP check + alert.\n"
            "For SCTP monitors: FortiADC does not support SCTP. "
            "If the pool serves SCTP traffic it cannot be migrated to FortiADC as-is. "
            "Confirm with the application team whether a TCP health check is acceptable."
        ),
        affected=external_hms,
    ))

    return [p for p in patterns if p.detected]


# ── Complexity scoring ────────────────────────────────────────────────────────

def score_migration_complexity(
    vs_profiles: dict[str, VSProfile],
    compat_results: list[CompatibilityResult],
    patterns: list[Pattern],
) -> dict:
    """
    Score the overall migration complexity 1-10.
    Deterministic — no LLM needed.
    Returns score dict with breakdown and verbal assessment.
    """
    score = 0
    breakdown = []

    # Base: number of VSes
    vs_count = len(vs_profiles)
    if vs_count > 50:
        score += 3; breakdown.append(f"+3: Large environment ({vs_count} VSes)")
    elif vs_count > 20:
        score += 2; breakdown.append(f"+2: Medium environment ({vs_count} VSes)")
    else:
        score += 1; breakdown.append(f"+1: Small environment ({vs_count} VSes)")

    # DataScripts
    ds_count = sum(1 for p in vs_profiles.values() if p.datascripts)
    if ds_count:
        pts = min(3, ds_count)
        score += pts; breakdown.append(f"+{pts}: {ds_count} DataScript(s) (manual rewrite)")

    # Blocked items
    blocked = sum(1 for r in compat_results if r.status == Compat.BLOCKED)
    if blocked:
        pts = min(2, blocked // 2 + 1)
        score += pts; breakdown.append(f"+{pts}: {blocked} blocked item(s)")

    # Critical patterns
    crit_patterns = [p for p in patterns if p.severity == "CRITICAL"]
    if crit_patterns:
        pts = min(2, len(crit_patterns))
        score += pts; breakdown.append(f"+{pts}: {len(crit_patterns)} critical pattern(s)")

    score = min(score, 10)

    verbal = (
        "LOW — straightforward migration, proceed with standard process"
        if score <= 3 else
        "MEDIUM — several manual items, allow extra planning time"
        if score <= 6 else
        "HIGH — significant manual work, dedicated project team recommended"
        if score <= 8 else
        "VERY HIGH — major complexity, consider phased approach over multiple windows"
    )

    return {
        "score":      score,
        "max":        10,
        "verbal":     verbal,
        "breakdown":  breakdown,
        "estimated_days": max(2, score * 3),
    }


# ── Ordered next steps ────────────────────────────────────────────────────────

def generate_next_steps(
    patterns: list[Pattern],
    compat_results: list[CompatibilityResult],
    complexity: dict,
) -> list[str]:
    """
    Generate an ordered, actionable "what to do now" list.
    Deterministic — priority based on severity and blocking nature.
    """
    steps: list[tuple[int, str]] = []   # (priority, step)

    blocked = [r for r in compat_results if r.status == Compat.BLOCKED]
    manual  = [r for r in compat_results if r.status == Compat.MANUAL]

    # Always first: resolve blockers
    if blocked:
        steps.append((1, f"Resolve {len(blocked)} BLOCKED item(s) — "
                         f"migration cannot proceed until these are fixed. "
                         f"Run: migrate.py report --filter BLOCKED"))

    # HSM certs — always early
    hsm = next((p for p in patterns if p.id == "P02" and p.detected), None)
    if hsm:
        steps.append((2, "Engage Wintel team to issue replacement certificates "
                         "for HSM-backed certs (cannot be exported from Avi). "
                         "This has a lead time — start immediately."))

    # DataScripts
    ds = next((p for p in patterns if p.id == "P01" and p.detected), None)
    if ds:
        steps.append((3, f"Analyse {len(ds.affected)} DataScript(s). "
                         f"For each: determine equivalent FortiADC config. "
                         f"Run: migrate.py llm-pack datascript --all "
                         f"to generate sanitized versions for LLM assistance."))

    # Expiring certs
    exp = next((p for p in patterns if p.id == "P04" and p.detected), None)
    if exp:
        steps.append((4, f"Renew {len(exp.affected)} near-expiry certificate(s) "
                         f"before migration window."))

    # GSLB
    gslb = next((p for p in patterns if p.id == "P07" and p.detected), None)
    if gslb:
        steps.append((5, "Schedule GSLB design session with network team and "
                         "FortiADC SE. GSLB migration is out of tool scope."))

    # OpenStack VIPs
    osp = next((p for p in patterns if p.id == "P08" and p.detected), None)
    if osp:
        steps.append((6, "Pre-allocate all Avi VIPs as fixed IPs in OpenStack Neutron. "
                         "Coordinate with IPNS team. "
                         "Run: migrate.py discover --output-vips to list all VIPs."))

    # Degraded pools
    deg = next((p for p in patterns if p.id == "P06" and p.detected), None)
    if deg:
        steps.append((7, f"Investigate {len(deg.affected)} degraded pool(s). "
                         f"Restore down members or document intentional state."))

    # Shared VIPs
    vip = next((p for p in patterns if p.id == "P05" and p.detected), None)
    if vip:
        steps.append((8, f"Plan {len(vip.affected)} shared VIP migration(s) — "
                         f"all VSes on a VIP must migrate in the same window."))

    # Standard steps always present
    steps.append((9, "Run in Dev-B first: migrate.py discover|analyse|transform|deploy "
                      "--env dev-b --dry-run"))
    steps.append((10, "Validate 7 days in Dev-B before proceeding to next environment."))
    steps.append((11, "Raise Change Request with migration report as evidence."))
    steps.append((12, "Schedule production maintenance window (recommend off-peak)."))

    return [step for _, step in sorted(steps, key=lambda x: x[0])]


# ── LLM pack builder ─────────────────────────────────────────────────────────

def build_llm_pack(
    pack_type:   str,
    discovery:   dict,
    bus:         EventBus,
    target_name: str = "",
    extra:       dict | None = None,
) -> str:
    """
    Build an optimised, sanitized prompt pack for external LLM use.
    The engineer copies the output and pastes it into any LLM — no env data exposed.

    pack_type:
      "full"           — complete migration analysis
      "datascript"     — DataScript translation request (requires target_name)
      "manual_item"    — specific manual item help (requires target_name)
      "http_policy"    — HTTP policy set translation
      "certificate"    — certificate strategy advice
      "next_steps"     — what to do next advice
    """
    header = (
        f"=== LLM ASSISTANCE PACK — {pack_type.upper()} ===\n"
        f"All environment-specific values sanitized.\n"
        f"Safe to paste into any external LLM.\n"
        f"{'='*60}\n\n"
    )

    if pack_type == "datascript":
        ds_list = discovery.get("datascripts", [])
        target  = next((d for d in ds_list if d.get("name") == target_name), None)
        if not target:
            return f"DataScript '{target_name}' not found in discovery data."

        scripts = target.get("datascript", [])
        code_blocks = []
        for s in scripts:
            code = s.get("script", "")
            event = s.get("evt", "unknown-event")
            sanitized_code = sanitize(code)
            code_blocks.append(f"Event: {event}\n```lua\n{sanitized_code}\n```")

        prompt = (
            f"I am migrating from Avi Networks LB to FortiADC.\n\n"
            f"The following is an Avi DataScript (Lua) that cannot be automatically "
            f"migrated because FortiADC does not have an equivalent scripting engine.\n\n"
            f"DataScript Name: {sanitize(target_name)}\n"
            f"Events: {', '.join(target.get('_events', []))}\n"
            f"Total Lines: {target.get('_total_lines', 0)}\n\n"
            + "\n\n".join(code_blocks) +
            f"\n\nPlease:\n"
            f"1. Explain what this DataScript does in plain English\n"
            f"2. Identify the business function it serves\n"
            f"3. Suggest the best FortiADC equivalent implementation:\n"
            f"   - FortiADC content routing rules (if applicable)\n"
            f"   - FortiADC WAF custom rules (if security-related)\n"
            f"   - Application-layer changes (if business logic)\n"
            f"4. Provide example FortiADC config or CLI commands where possible\n"
            f"5. Flag any functionality that has no FortiADC equivalent\n"
        )
        return header + prompt

    elif pack_type == "full":
        sanitized = bus.sanitized_block()
        prompt = (
            f"I am migrating from Avi Networks LB to FortiADC.\n"
            f"Below is a sanitized analysis of my Avi configuration.\n"
            f"All IPs, hostnames, and UUIDs have been replaced with placeholders.\n\n"
            f"{sanitized}\n\n"
            f"Based on this analysis:\n"
            f"1. What are the highest-risk items I should address first?\n"
            f"2. For each MANUAL item, what is the recommended FortiADC approach?\n"
            f"3. Are there any patterns here that suggest architectural concerns?\n"
            f"4. What is a realistic migration timeline given this complexity?\n"
        )
        return header + prompt

    elif pack_type == "next_steps":
        manual_events = [e for e in bus.all_events if e.level == Level.MANUAL]
        critical_events = [e for e in bus.all_events if e.level == Level.CRITICAL]
        summary = (
            f"Migration situation:\n"
            f"- {len(manual_events)} manual items requiring human action\n"
            f"- {len(critical_events)} critical blockers\n"
            f"- Items:\n"
            + "\n".join(f"  • {sanitize(e.message)}" for e in (critical_events + manual_events)[:10])
        )
        prompt = (
            f"I am migrating from Avi Networks to FortiADC.\n\n"
            f"{summary}\n\n"
            f"What should I tackle first? Provide a prioritised action plan "
            f"with concrete steps for each item."
        )
        return header + prompt

    else:
        return header + bus.sanitized_block()
