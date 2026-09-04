"""
core/ops_automation.py
Intelligent Operational Automation Layer.

Mitigates the operational tradeoffs of moving from AVI (cloud-native, elastic,
script-driven) to FortiADC (traditional, stateful, policy-driven).

AVI gave you:
  - Auto-scaling Service Engines
  - Dynamic pool member discovery via OpenStack/Contrail
  - DataScript-driven business logic
  - Built-in analytics and adaptive health scoring
  - Cloud-connector VIP lifecycle automation

FortiADC gives you:
  - Stable, high-performance ADC
  - VDOM isolation
  - Strong enterprise security features
  - Predictable behaviour

This module bridges the gap by providing automation equivalents
for each lost AVI capability, using deterministic rules + scheduled checks
that can run from the same jump host, on the same schedule,
without any LLM or cloud dependency.

Components:
  1. HealthWatcher     — replaces AVI's adaptive health analytics
  2. CapacityAdvisor   — replaces AVI's elastic scaling signals
  3. PoolSyncAdvisor   — replaces AVI's dynamic pool member discovery
  4. DriftGuard        — day-2 drift detection (replaces AVI's self-healing)
  5. DataScriptAdvisor — guidance engine for DataScript → FortiADC policy translation
  6. AlertBridge       — normalises FortiADC alerts to AVI-equivalent severity model

All components are read-only — they advise, never act.
Execution path: manual trigger or cron. Never automated write operations.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 1. HealthWatcher ──────────────────────────────────────────────────────────

@dataclass
class HealthSignal:
    vs_name:      str
    pool_name:    str
    members_up:   int
    members_total: int
    health_pct:   float
    risk:         str   # GREEN | AMBER | RED
    recommendation: str
    checked_at:   str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)


class HealthWatcher:
    """
    Replaces AVI's adaptive health analytics.

    AVI continuously scored VS health and surfaced anomalies in its UI.
    FortiADC has per-VS health status but no trend analysis or anomaly detection.

    HealthWatcher polls FortiADC, computes health scores, detects trends,
    and emits recommendations — mimicking AVI's health dashboard.
    """

    AMBER_THRESHOLD = 0.75   # < 75% members up = AMBER
    RED_THRESHOLD   = 0.50   # < 50% members up = RED

    def __init__(self, client, history_file: str = "state/health-history.json"):
        self._client  = client
        self._history = Path(history_file)
        self._history.parent.mkdir(exist_ok=True)

    def check_all(self, fortiadc_config: dict) -> list[HealthSignal]:
        """Poll all VS and return health signals with trend analysis."""
        signals  = []
        history  = self._load_history()

        for vs in fortiadc_config.get("virtual_servers", []):
            vs_name   = vs.get("name", "?")
            pool_name = vs.get("payload", {}).get("pool", "")
            signal    = self._check_vs(vs_name, pool_name, history)
            if signal:
                signals.append(signal)

        self._save_history(signals, history)
        return signals

    def _check_vs(self, vs_name: str, pool_name: str,
                  history: dict) -> Optional[HealthSignal]:
        try:
            # Get pool member health from FortiADC
            pool_data = self._client.get(f"load_balance/real_server_pool/{pool_name}")
            members   = pool_data.get("results", {}).get("pool_member", [])
            if not members:
                return None

            total  = len(members)
            up     = sum(1 for m in members
                         if str(m.get("status", "")).lower() in ("up", "enable", "active"))
            pct    = up / total if total > 0 else 0.0

            # Trend: compare with previous reading
            prev_pct = history.get(vs_name, {}).get("health_pct", pct)
            trend    = pct - prev_pct  # negative = degrading

            # Risk classification
            if pct < self.RED_THRESHOLD:
                risk = "RED"
                rec  = (f"URGENT: {up}/{total} members up ({pct:.0%}). "
                        "Investigate pool member failures immediately. "
                        "Consider emergency rollback if traffic impact confirmed.")
            elif pct < self.AMBER_THRESHOLD:
                risk = "AMBER"
                rec  = (f"WARNING: {up}/{total} members up ({pct:.0%}). "
                        "Pool degraded. Monitor closely and investigate down members.")
            elif trend < -0.25:
                risk = "AMBER"
                rec  = (f"DEGRADING: Was {prev_pct:.0%}, now {pct:.0%}. "
                        "Pool health declining. Investigate before degradation continues.")
            else:
                risk = "GREEN"
                rec  = f"Healthy: {up}/{total} members up."

            return HealthSignal(
                vs_name=vs_name, pool_name=pool_name,
                members_up=up, members_total=total,
                health_pct=pct, risk=risk, recommendation=rec,
            )
        except Exception:
            return None

    def _load_history(self) -> dict:
        if self._history.exists():
            try:
                return json.loads(self._history.read_text())
            except Exception:
                pass
        return {}

    def _save_history(self, signals: list[HealthSignal], previous: dict) -> None:
        data = dict(previous)
        for s in signals:
            data[s.vs_name] = {"health_pct": s.health_pct, "checked_at": s.checked_at}
        self._history.write_text(json.dumps(data, indent=2))


# ── 2. CapacityAdvisor ────────────────────────────────────────────────────────

@dataclass
class CapacitySignal:
    vdom:           str
    vs_count:       int
    connection_pct: float   # 0.0–1.0 of estimated capacity
    ssl_pct:        float
    risk:           str
    recommendation: str

    def to_dict(self) -> dict:
        return asdict(self)


class CapacityAdvisor:
    """
    Replaces AVI's elastic scaling.

    AVI spun up new SEs when load increased. FortiADC cannot scale horizontally —
    capacity must be planned upfront. This advisor monitors utilisation metrics
    and warns before capacity limits are hit, giving the ops team time to
    provision additional FortiADC capacity or implement load shedding.

    Thresholds are estimates — calibrate against your actual FortiADC model specs.
    """

    # Conservative thresholds — adjust for your FortiADC model
    AMBER_PCT = 0.70
    RED_PCT   = 0.85

    # Baseline capacity estimates per FortiADC-VM tier
    # Override in config.yaml → fortiadc.capacity
    DEFAULT_CAPACITY = {
        "max_vs":          500,
        "max_connections": 500_000,
        "max_ssl_cps":     5_000,
    }

    def __init__(self, client, capacity_config: Optional[dict] = None):
        self._client   = client
        self._capacity = capacity_config or self.DEFAULT_CAPACITY

    def assess(self, vdom: str, fortiadc_config: dict) -> CapacitySignal:
        """Assess capacity utilisation for a VDOM."""
        vs_count = len(fortiadc_config.get("virtual_servers", []))
        vs_pct   = vs_count / self._capacity["max_vs"]

        # Attempt to read live connection count from FortiADC
        conn_pct = 0.0
        ssl_pct  = 0.0
        try:
            status = self._client.get("system/performance")
            stats  = status.get("results", {})
            conns  = int(stats.get("current_connections", 0))
            ssl    = int(stats.get("current_ssl_connections", 0))
            conn_pct = conns / self._capacity["max_connections"]
            ssl_pct  = ssl / self._capacity["max_ssl_cps"]
        except Exception:
            pass  # FortiADC may not expose this endpoint — use VS count only

        peak_pct = max(vs_pct, conn_pct, ssl_pct)

        if peak_pct >= self.RED_PCT:
            risk = "RED"
            rec  = (
                "CRITICAL: FortiADC capacity near limit. "
                "Immediate action required: provision additional FortiADC VM, "
                "redistribute load, or defer further migration until capacity added."
            )
        elif peak_pct >= self.AMBER_PCT:
            risk = "AMBER"
            rec  = (
                f"WARNING: Capacity at {peak_pct:.0%}. "
                "Plan capacity expansion before next migration phase. "
                "AVI did this automatically — FortiADC requires manual provisioning."
            )
        else:
            risk = "GREEN"
            rec  = f"Capacity healthy at {peak_pct:.0%} utilisation."

        return CapacitySignal(
            vdom=vdom, vs_count=vs_count,
            connection_pct=conn_pct, ssl_pct=ssl_pct,
            risk=risk, recommendation=rec,
        )


# ── 3. PoolSyncAdvisor ────────────────────────────────────────────────────────

@dataclass
class PoolDrift:
    pool_name:    str
    avi_members:  list[str]    # IPs AVI currently reports
    forti_members: list[str]   # IPs FortiADC currently has
    added:        list[str]    # in AVI but not FortiADC
    removed:      list[str]    # in FortiADC but not AVI
    action:       str

    def to_dict(self) -> dict:
        return asdict(self)


class PoolSyncAdvisor:
    """
    Replaces AVI's dynamic pool member discovery.

    AVI's cloud connector automatically detected new Nova instances and
    added them to pools. FortiADC has no cloud connector — pool membership
    is static and must be managed manually.

    PoolSyncAdvisor compares AVI pool members (from a fresh discovery)
    against FortiADC pool members and surfaces any membership drift.
    The engineer then decides whether to add/remove members in FortiADC.
    """

    def compare(self, discovery: dict, client,
                 fortiadc_config: dict) -> list[PoolDrift]:
        """Compare AVI pool membership against FortiADC."""
        drifts = []

        avi_pools = {p["name"]: p for p in discovery.get("pools", [])
                     if p.get("name")}

        for pool_def in fortiadc_config.get("real_server_pools", []):
            pool_name = pool_def.get("name", "?")
            avi_pool  = avi_pools.get(pool_name, {})

            # AVI members
            avi_members = [
                s.get("ip", {}).get("addr", "") if isinstance(s.get("ip"), dict)
                else str(s.get("ip", ""))
                for s in avi_pool.get("servers", [])
            ]
            avi_members = [m for m in avi_members if m]

            # FortiADC members
            forti_members = []
            try:
                data   = client.get(f"load_balance/real_server_pool/{pool_name}")
                mems   = data.get("results", {}).get("pool_member", [])
                forti_members = [m.get("real-server", m.get("ip", ""))
                                 for m in mems]
            except Exception:
                pass

            avi_set   = set(avi_members)
            forti_set = set(forti_members)
            added     = list(avi_set - forti_set)
            removed   = list(forti_set - avi_set)

            if added or removed:
                action_parts = []
                if added:
                    action_parts.append(
                        f"Add to FortiADC pool '{pool_name}': {', '.join(added)}"
                    )
                if removed:
                    action_parts.append(
                        f"Remove from FortiADC pool '{pool_name}': {', '.join(removed)} "
                        "(no longer in AVI — may be decommissioned)"
                    )
                drifts.append(PoolDrift(
                    pool_name=pool_name,
                    avi_members=avi_members,
                    forti_members=forti_members,
                    added=added, removed=removed,
                    action=" | ".join(action_parts),
                ))

        return drifts


# ── 4. DriftGuard ─────────────────────────────────────────────────────────────

class DriftGuard:
    """
    Day-2 drift detection.

    Replaces AVI's self-healing configuration enforcement.
    AVI controllers continuously reconciled SE config.
    FortiADC config is static — manual changes can create drift.

    DriftGuard runs config_diff on a schedule and reports any deviation
    from the last known good deployment state.
    """

    def __init__(self, client, state_dir: str = "state"):
        self._client    = client
        self._state_dir = Path(state_dir)

    def run(self, env: str, fortiadc_config: dict) -> dict:
        """Run drift check and return structured report."""
        from validators.config_diff import run_config_diff, format_diff_report
        from core.events import EventBus

        bus   = EventBus(log_path=self._state_dir / f"{env}-driftguard.jsonl",
                         verbose=False)
        diffs = run_config_diff(self._client, fortiadc_config, bus)
        bus.close()

        report = {
            "env":          env,
            "checked_at":   _now(),
            "total_diffs":  len(diffs),
            "drift_items":  [d.__dict__ for d in diffs if d.severity == "DRIFT"],
            "warn_items":   [d.__dict__ for d in diffs if d.severity == "WARN"],
            "clean":        len(diffs) == 0,
            "text_report":  format_diff_report(diffs),
        }

        # Persist report
        out = self._state_dir / f"{env}-driftguard-latest.json"
        out.write_text(json.dumps(report, indent=2, default=str))

        return report


# ── 5. DataScriptAdvisor ─────────────────────────────────────────────────────

@dataclass
class DataScriptAssessment:
    name:        str
    complexity:  str    # SIMPLE | MEDIUM | COMPLEX
    events:      list[str]
    line_count:  int
    patterns:    list[str]   # detected patterns
    forti_approach: str      # recommended FortiADC approach
    manual_effort: str       # LOW | MEDIUM | HIGH
    blocking:    bool        # blocks migration until resolved

    def to_dict(self) -> dict:
        return asdict(self)


class DataScriptAdvisor:
    """
    DataScript → FortiADC policy translation intelligence.

    AVI DataScripts (Lua) have no direct equivalent in FortiADC.
    This advisor classifies each DataScript by complexity, detects
    the business pattern it implements, and recommends the best
    FortiADC approach — reducing the manual effort for the engineer.

    Pattern library is deterministic (no LLM required at runtime).
    For complex DataScripts, the advisor generates a sanitized
    LLM pack for external assistance.
    """

    # Pattern detection rules: (pattern_name, description, forti_approach)
    PATTERNS = [
        # HTTP redirects
        (r"avi\.http\.redirect",
         "HTTP redirect",
         "FortiADC Content Routing → Action: redirect. Specify redirect URL and code.",
         "SIMPLE", "LOW"),

        # Header manipulation
        (r"avi\.http\.(add|replace|remove)_header",
         "Header manipulation",
         "FortiADC HTTP Profile → X-Forwarded-For settings, or WAF custom header rule.",
         "SIMPLE", "LOW"),

        # URL rewriting
        (r"avi\.http\.set_uri|avi\.http\.replace_uri",
         "URL rewrite",
         "FortiADC Content Routing → Rewrite rule. Map path patterns explicitly.",
         "MEDIUM", "MEDIUM"),

        # Rate limiting / connection limits
        (r"rate_limit|connection_limit|throttle",
         "Rate limiting",
         "FortiADC Virtual Server → connection-limit field. "
         "For per-IP rate limiting use DDoS protection profile.",
         "MEDIUM", "MEDIUM"),

        # Auth / token validation
        (r"jwt|bearer|token|auth|authenticate",
         "Authentication / token validation",
         "FortiADC WAF policy with custom signature. "
         "Or offload auth to application layer. Consider FortiWeb if complex.",
         "COMPLEX", "HIGH"),

        # Cookie manipulation
        (r"avi\.http\.set_cookie|avi\.http\.get_cookie",
         "Cookie manipulation",
         "FortiADC HTTP Profile → cookie settings. "
         "Complex cookie logic may need WAF custom rule.",
         "MEDIUM", "MEDIUM"),

        # SSL / TLS inspection
        (r"avi\.ssl\.|tls\.|ssl\.",
         "SSL/TLS inspection",
         "FortiADC SSL profile settings. "
         "Client certificate inspection via SSL profile.",
         "MEDIUM", "MEDIUM"),

        # Logging / analytics
        (r"avi\.utils\.log|print|avi\.analytics",
         "Custom logging",
         "FortiADC syslog profile. "
         "Detailed application logging may not be replicated — accept the gap.",
         "SIMPLE", "LOW"),
    ]

    def assess(self, datascript: dict) -> DataScriptAssessment:
        """Assess a single DataScript and recommend FortiADC approach."""
        import re

        name   = datascript.get("name", "?")
        events = datascript.get("_events", [])
        lines  = datascript.get("_total_lines", 0)

        # Collect all script code
        code = " ".join(
            s.get("script", "") for s in datascript.get("datascript", [])
        )

        # Pattern detection
        detected_patterns  = []
        detected_approachs = []
        max_complexity     = "SIMPLE"
        max_effort         = "LOW"

        for (pattern, desc, approach, complexity, effort) in self.PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                detected_patterns.append(desc)
                detected_approachs.append(approach)
                if complexity == "COMPLEX":
                    max_complexity = "COMPLEX"
                    max_effort     = "HIGH"
                elif complexity == "MEDIUM" and max_complexity != "COMPLEX":
                    max_complexity = "MEDIUM"
                    max_effort     = "MEDIUM" if max_effort != "HIGH" else "HIGH"

        # Complexity from line count
        if lines > 100 and max_complexity != "COMPLEX":
            max_complexity = "COMPLEX"
            max_effort     = "HIGH"
        elif lines > 30 and max_complexity == "SIMPLE":
            max_complexity = "MEDIUM"
            max_effort     = "MEDIUM"

        # No patterns matched — unknown
        if not detected_patterns:
            detected_patterns  = ["Custom/unknown logic"]
            detected_approachs = [
                "Review script manually. "
                "No automatic pattern detected. "
                "Use llm-pack datascript command to generate sanitized analysis prompt."
            ]
            max_complexity = "COMPLEX"
            max_effort     = "HIGH"

        forti_approach = " | ".join(dict.fromkeys(detected_approachs))

        return DataScriptAssessment(
            name=name,
            complexity=max_complexity,
            events=events,
            line_count=lines,
            patterns=detected_patterns,
            forti_approach=forti_approach,
            manual_effort=max_effort,
            blocking=max_complexity == "COMPLEX",
        )

    def assess_all(self, discovery: dict) -> list[DataScriptAssessment]:
        """Assess all DataScripts in discovery. Returns sorted by effort."""
        assessments = [self.assess(ds) for ds in discovery.get("datascripts", [])]
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        return sorted(assessments, key=lambda a: order.get(a.manual_effort, 9))


# ── 6. AlertBridge ────────────────────────────────────────────────────────────

@dataclass
class NormalisedAlert:
    source:    str   # fortiadc
    vs_name:   str
    severity:  str   # P1 | P2 | P3 | P4
    summary:   str
    detail:    str
    action:    str
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


class AlertBridge:
    """
    Normalises FortiADC alerts to the AVI-equivalent severity model.

    AVI's alert system was rich and context-aware. FortiADC has SNMP traps
    and syslog but with different severity taxonomy. AlertBridge normalises
    FortiADC alert patterns to P1-P4 severity using AVI-equivalent definitions,
    so operations teams don't need to re-learn alert meanings.

    P1: VS completely down, no members healthy — immediate response
    P2: VS degraded (< 50% members), health declining
    P3: VS warning (single member down, cert expiry, drift detected)
    P4: VS informational (performance within bounds, low risk)
    """

    def classify_vs_health(self, signal: HealthSignal) -> NormalisedAlert:
        risk_to_priority = {"RED": "P1", "AMBER": "P2", "GREEN": "P4"}
        priority = risk_to_priority.get(signal.risk, "P3")

        action_map = {
            "P1": "Immediate investigation. Consider rollback to AVI.",
            "P2": "Investigate pool members within 30 minutes.",
            "P4": "No action required. Monitor.",
        }

        return NormalisedAlert(
            source="fortiadc",
            vs_name=signal.vs_name,
            severity=priority,
            summary=f"[{priority}] VS {signal.vs_name}: {signal.risk} health ({signal.health_pct:.0%})",
            detail=signal.recommendation,
            action=action_map.get(priority, "Review and investigate."),
            timestamp=signal.checked_at,
        )

    def classify_capacity(self, signal: CapacitySignal) -> NormalisedAlert:
        risk_to_priority = {"RED": "P1", "AMBER": "P2", "GREEN": "P4"}
        priority = risk_to_priority.get(signal.risk, "P3")

        return NormalisedAlert(
            source="fortiadc",
            vs_name=f"VDOM:{signal.vdom}",
            severity=priority,
            summary=f"[{priority}] FortiADC capacity {signal.risk}: VS={signal.vs_count}, conn={signal.connection_pct:.0%}",
            detail=signal.recommendation,
            action="Provision additional FortiADC capacity or redistribute load."
                   if priority in ("P1", "P2") else "Monitor.",
            timestamp=_now(),
        )


# ── CLI entry point ────────────────────────────────────────────────────────────

def run_ops_check(env: str, config_path: str = "config.yaml",
                  state_dir: str = "state") -> dict:
    """
    Run all operational automation checks for an environment.
    Returns structured report. Called by migrate.py ops-check command.
    """
    import yaml
    from core.fortiadc_client import FortiADCClient
    from pathlib import Path

    # Load config
    cfg = yaml.safe_load(Path(config_path).read_text())
    envs = {e["name"]: e for e in cfg.get("environments", [])}
    if env not in envs:
        return {"error": f"Environment '{env}' not found in config"}

    env_cfg  = envs[env]
    vdom     = env_cfg.get("fortiadc_vdom", "root")
    fadc_cfg = cfg.get("fortiadc", {})
    capacity_cfg = fadc_cfg.get("capacity", None)

    # Load FortiADC config (last transform output)
    config_file = Path(f"fortiadc/{env}-config.json")
    if not config_file.exists():
        return {"error": f"No transform output found: {config_file}. Run transform first."}
    fortiadc_config = json.loads(config_file.read_text())

    # Load discovery (for pool sync)
    discovery_file = Path(f"discovery/{env}.json")
    discovery = json.loads(discovery_file.read_text()) if discovery_file.exists() else {}

    # Connect to FortiADC (read-only)
    client = FortiADCClient(
        host=fadc_cfg.get("host", ""),
        username=fadc_cfg.get("username", ""),
        password=fadc_cfg.get("password",
                  os.environ.get("OPSAI_FORTIADC_PASSWORD", "")),
        vdom=vdom,
        verify_ssl=fadc_cfg.get("verify_ssl", True),
        dry_run=False,
    )

    report: dict = {"env": env, "checked_at": _now(), "vdom": vdom}

    # 1. Health check
    watcher = HealthWatcher(client, f"{state_dir}/health-history.json")
    signals = watcher.check_all(fortiadc_config)
    report["health"] = [s.to_dict() for s in signals]

    # 2. Capacity
    advisor = CapacityAdvisor(client, capacity_cfg)
    cap     = advisor.assess(vdom, fortiadc_config)
    report["capacity"] = cap.to_dict()

    # 3. Pool sync
    sync_advisor = PoolSyncAdvisor()
    pool_drifts  = sync_advisor.compare(discovery, client, fortiadc_config)
    report["pool_sync"] = [d.to_dict() for d in pool_drifts]

    # 4. Drift guard
    guard        = DriftGuard(client, state_dir)
    drift_report = guard.run(env, fortiadc_config)
    report["config_drift"] = drift_report

    # 5. DataScript assessment (from discovery)
    if discovery:
        ds_advisor   = DataScriptAdvisor()
        ds_assessments = ds_advisor.assess_all(discovery)
        report["datascripts"] = [a.to_dict() for a in ds_assessments]

    # 6. Alert normalisation
    bridge = AlertBridge()
    alerts = []
    for s in signals:
        alerts.append(bridge.classify_vs_health(s).to_dict())
    alerts.append(bridge.classify_capacity(cap).to_dict())
    alerts = [a for a in alerts if a["severity"] in ("P1", "P2", "P3")]
    report["alerts"] = alerts

    # Summary
    p1 = sum(1 for a in alerts if a["severity"] == "P1")
    p2 = sum(1 for a in alerts if a["severity"] == "P2")
    report["summary"] = {
        "p1_alerts":   p1,
        "p2_alerts":   p2,
        "pool_drifts": len(pool_drifts),
        "config_drifts": drift_report.get("total_diffs", 0),
        "action_required": p1 > 0 or p2 > 0 or len(pool_drifts) > 0,
    }

    # Persist
    out = Path(state_dir) / f"{env}-ops-check.json"
    out.write_text(json.dumps(report, indent=2, default=str))

    return report
