"""
analyzers/compatibility.py
Systematic compatibility check for every discovered object.
Produces a categorised compatibility matrix:
  AUTO     — can be migrated automatically
  WARN     — can be migrated but needs review
  MANUAL   — requires human action before migration
  BLOCKED  — cannot proceed until prerequisite resolved
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from analyzers.dependency_graph import VSProfile
from core.events import EventBus, Level


class Compat(str, Enum):
    AUTO    = "AUTO"
    WARN    = "WARN"
    MANUAL  = "MANUAL"
    BLOCKED = "BLOCKED"


@dataclass
class CompatibilityResult:
    object_type:  str
    object_name:  str
    status:       Compat
    reason:       str
    actions:      list[str] = field(default_factory=list)
    associations: list[str] = field(default_factory=list) # Dependencies (Pools, Monitors, etc.)


def analyse_compatibility(discovery: dict,
                          vs_profiles: dict[str, VSProfile],
                          bus: EventBus | None = None
                          ) -> list[CompatibilityResult]:
    """Run all compatibility checks and return full result list."""
    results: list[CompatibilityResult] = []

    for vs_name, profile in vs_profiles.items():
        # Linked content counts
        assoc = []
        if profile.pool: assoc.append("Pools:1")
        if profile.health_monitor: assoc.append("Monitors:1")
        if profile.ssl_certs: assoc.append(f"Certs:{len(profile.ssl_certs)}")
        
        # Health-Aware Governance: Flag inactive services
        state = (profile.health_state or "UNKNOWN").upper()
        if "DOWN" in state or "DISABLED" in state:
            profile.warnings.append(f"Service state is '{state}'. Confirm if migration is required for this inactive service.")
        
        if profile.blockers:
            reasons = "; ".join(profile.blockers[:3]) # Show top 3 reasons
            if len(profile.blockers) > 3: reasons += "..."
            results.append(CompatibilityResult(
                "virtualservice", vs_name, Compat.BLOCKED,
                f"BLOCKED: {reasons}",
                actions=profile.blockers,
                associations=assoc
            ))
        elif profile.warnings:
            reasons = "; ".join(profile.warnings[:3])
            if len(profile.warnings) > 3: reasons += "..."
            results.append(CompatibilityResult(
                "virtualservice", vs_name, Compat.WARN,
                f"WARNING: {reasons}",
                actions=profile.warnings,
                associations=assoc
            ))
        else:
            results.append(CompatibilityResult(
                "virtualservice", vs_name, Compat.AUTO,
                "All dependencies mappable",
                associations=assoc
            ))

    # Health monitors — include parent linkage if possible
    _HM_TYPE_MAP = {
        "HEALTH_MONITOR_HTTP":    Compat.AUTO,
        "HEALTH_MONITOR_HTTPS":   Compat.AUTO,
        "HEALTH_MONITOR_TCP":     Compat.AUTO,
        "HEALTH_MONITOR_UDP":     Compat.AUTO,
        "HEALTH_MONITOR_PING":    Compat.AUTO,
        "HEALTH_MONITOR_DNS":     Compat.WARN,
        "HEALTH_MONITOR_EXTERNAL":Compat.MANUAL,
        "HEALTH_MONITOR_SIP":     Compat.MANUAL,
        "HEALTH_MONITOR_RADIUS":  Compat.MANUAL,
    }
    for hm in discovery.get("health_monitors", []):
        hm_name = hm.get("name", "?")
        hm_type = hm.get("type", "UNKNOWN")
        status  = _HM_TYPE_MAP.get(hm_type, Compat.MANUAL)
        
        # Associated Content (Reverse Lookup)
        assoc = []
        for vs_name, profile in vs_profiles.items():
            if profile.health_monitor == hm_name:
                # Extracts the 'real' pool name if it was decorated in dependency_graph
                pool_base = profile.pool.split(" (")[0] if profile.pool else "N/A"
                assoc.append(f"Pool:{pool_base} → VS:{vs_name}")

        reason  = (f"Type '{hm_type}' not supported in FortiADC"
                   if status == Compat.MANUAL
                   else f"Type '{hm_type}' → FortiADC health check")
        
        results.append(CompatibilityResult(
            "HealthMonitor", hm_name, status, reason,
            actions=(["Replace with HTTP/TCP health check"] if status == Compat.MANUAL else []),
            associations=assoc
        ))

    # Persistence profiles
    for pp in discovery.get("persistence_profiles", []):
        fadc_type = pp.get("_fortiadc_persistence_type")
        status = Compat.AUTO if fadc_type else Compat.MANUAL
        results.append(CompatibilityResult(
            "ApplicationPersistenceProfile", pp.get("name", "?"), status,
            f"Persistence type: {pp.get('persistence_type', '?')} "
            f"→ {'FortiADC ' + fadc_type if fadc_type else 'NO EQUIVALENT'}",
            actions=(["Redesign persistence strategy"] if not fadc_type else []),
        ))

    # DataScripts — always MANUAL but with "Intelligence"
    for ds in discovery.get("datascripts", []):
        ds_name = ds.get("name", "?")
        script_code = ds.get("script", "").lower()
        
        # Basic Categorization
        reasons = []
        suggested_fadc = "Implement in Content Routing"
        
        if "http_redirect" in script_code or "http.redirect" in script_code:
            reasons.append("Contains HTTP Redirect")
            suggested_fadc = "Use FortiADC Content Routing (HTTP Redirect)"
        if "http_request_header_insert" in script_code or "http.replace_header" in script_code:
            reasons.append("Header Manipulation")
            suggested_fadc = "Use FortiADC Content Routing (Modification)"
        
        reason_msg = f"Requires Manual Porting ({ds.get('_total_lines', 0)} lines)"
        if reasons:
            reason_msg = f"{', '.join(reasons)}. {reason_msg}"

        results.append(CompatibilityResult(
            "VSDataScriptSet", ds_name, Compat.MANUAL,
            reason_msg,
            actions=[
                f"SUGGESTION: {suggested_fadc}",
                "Use LLM assistance via sanitized output",
            ],
        ))

    # SSL Certificates
    for cert in discovery.get("ssl_certificates", []):
        cert_name = cert.get("name", "?")
        if not cert.get("_exportable", True):
            results.append(CompatibilityResult(
                "SSLKeyAndCertificate", cert_name, Compat.BLOCKED,
                "HSM-backed. Private key remains in HSM and cannot be migrated.",
                actions=["Issue new certificate or use HSM-integration on FortiADC"],
            ))
        elif (cert.get("_days_until_expiry") or 999) < 0:
            results.append(CompatibilityResult(
                "SSLKeyAndCertificate", cert_name, Compat.BLOCKED,
                "Certificate EXPIRED. Blocking active traffic.",
                actions=["Renew certificate before proceed"],
            ))
        else:
            results.append(CompatibilityResult(
                "SSLKeyAndCertificate", cert_name, Compat.AUTO,
                "Exportable — private key and chain present",
            ))

    # WAF Policies
    for waf in discovery.get("waf_policies", []):
        results.append(CompatibilityResult(
            "WafPolicy", waf.get("name", "?"), Compat.MANUAL,
            "WAF signatures and profiles require manual performance tuning on FortiADC.",
            actions=["Map signatures to FortiADC WAF", "Review False Positive posture"],
        ))

    # Application Profiles
    for ap in discovery.get("application_profiles", []):
        ap_type = ap.get("type", "UNKNOWN")
        if ap_type == "APPLICATION_PROFILE_TYPE_HTTP":
            results.append(CompatibilityResult(
                "ApplicationProfile", ap.get("name", "?"), Compat.AUTO,
                "HTTP Profile → FortiADC HTTP Profile",
            ))
        else:
            results.append(CompatibilityResult(
                "ApplicationProfile", ap.get("name", "?"), Compat.WARN,
                f"Check '{ap_type}' specific L7 settings for FortiADC parity.",
                actions=["Review compression and buffer settings"],
            ))

    # Log results to bus for reporting
    if bus:
        for r in results:
            level = None
            if r.status == Compat.BLOCKED: level = Level.CRITICAL
            elif r.status == Compat.MANUAL: level = Level.MANUAL
            elif r.status == Compat.WARN: level = Level.WARN
            
            if level:
                bus.log(level, r.object_name, r.reason, 
                        object_type=r.object_type, 
                        action="; ".join(r.actions) if r.actions else "Manual review required")

    return results


def summary(results: list[CompatibilityResult]) -> dict:
    counts = {c.value: 0 for c in Compat}
    for r in results:
        counts[r.status.value] += 1
    return counts
