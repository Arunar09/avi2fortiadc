"""
analyzers/dependency_graph.py
Builds a complete dependency graph for every Virtual Service.
Maps: VS → pool → members → health monitors → certs → profiles → datascripts.
Also identifies shared objects (certs/profiles used by multiple VSes).
Output used by reporters and deployer (deploy order must respect dependencies).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DependencyNode:
    object_type:  str
    object_name:  str
    object_uuid:  str
    deps:         list["DependencyNode"] = field(default_factory=list)
    can_auto_migrate: bool  = True
    blocking_reason:  str   = ""


@dataclass
class VSProfile:
    """Complete dependency profile for one Virtual Service."""
    vs_name:        str
    vs_uuid:        str
    vips:           list[str]
    health_state:   str
    # Dependencies
    pool:           Optional[str]        = None
    pool_members:   list[str]            = field(default_factory=list)
    health_monitor: Optional[str]        = None
    ssl_certs:      list[str]            = field(default_factory=list)
    ssl_profile:    Optional[str]        = None
    app_profile:    Optional[str]        = None
    net_profile:    Optional[str]        = None
    persistence:    Optional[str]        = None
    datascripts:    list[str]            = field(default_factory=list)
    http_policies:  list[str]            = field(default_factory=list)
    waf_policy:     Optional[str]        = None
    auth_profile:   Optional[str]        = None
    # Migration readiness
    can_auto_migrate: bool               = True
    blockers:       list[str]            = field(default_factory=list)
    warnings:       list[str]            = field(default_factory=list)


def build_dependency_graph(discovery: dict) -> dict[str, VSProfile]:
    """
    Walk the full discovery dict and build VSProfile for each Virtual Service.
    Returns dict keyed by VS name.
    """
    # Build lookup indexes for fast resolution
    pools       = _index_by_name(discovery.get("pools", []))
    ssl_certs   = _index_by_name(discovery.get("ssl_certificates", []))
    ssl_profiles = _index_by_name(discovery.get("ssl_profiles", []))
    app_profiles = _index_by_name(discovery.get("application_profiles", []))
    net_profiles = _index_by_name(discovery.get("network_profiles", []))
    persistence  = _index_by_name(discovery.get("persistence_profiles", []))
    datascripts  = _index_by_name(discovery.get("datascripts", []))

    profiles: dict[str, VSProfile] = {}

    for vs in discovery.get("virtual_services", []):
        res = vs.get("_resolved", {})
        name = vs.get("name", "?")

        p = VSProfile(
            vs_name=name,
            vs_uuid=vs.get("uuid", ""),
            vips=vs.get("_vips", []),
            health_state=vs.get("_health", "UNKNOWN"),
        )

        # Pool
        pool_name = res.get("pool_ref", "")
        p.pool = pool_name
        pool_obj = pools.get(pool_name, {})
        p.pool_members = pool_obj.get("_member_ips", [])

        # Health monitor (from pool)
        hm_refs = pool_obj.get("health_monitor_refs", [])
        if hm_refs:
            p.health_monitor = _name_from_ref(hm_refs[0])

        # SSL certs
        p.ssl_certs = res.get("ssl_key_and_certificate_refs", [])
        for cert_name in p.ssl_certs:
            cert = ssl_certs.get(cert_name, {})
            if not cert.get("_exportable", True):
                p.blockers.append(
                    f"Certificate '{cert_name}' is HSM-backed and non-exportable. "
                    f"New cert must be issued and imported before migration."
                )
                p.can_auto_migrate = False
            days = cert.get("_days_until_expiry")
            if days is not None and days < 0:
                p.blockers.append(
                    f"Certificate '{cert_name}' has EXPIRED. Renew before migration."
                )
                p.can_auto_migrate = False
            elif days is not None and days < 30:
                p.warnings.append(
                    f"Certificate '{cert_name}' expires in {days} days. Renew soon."
                )

        # Profiles
        p.ssl_profile = res.get("ssl_profile_ref", "")
        if p.ssl_profile:
            ssl_prof_obj = ssl_profiles.get(p.ssl_profile, {})
            # Check for insecure TLS versions
            accepted_versions = [v.get("type", "") for v in ssl_prof_obj.get("accepted_versions", [])]
            insecure = [v for v in accepted_versions if v in ["SSL_V3", "TLS_V1", "TLS_V1_1"]]
            if insecure:
                p.warnings.append(
                    f"SSL Profile '{p.ssl_profile}' uses insecure protocols: {', '.join(insecure)}. "
                    "Modernize to TLS 1.2+ in FortiADC."
                )

        p.app_profile = res.get("application_profile_ref", "")
        p.net_profile = res.get("network_profile_ref", "")

        # Persistence (from pool)
        persist_ref = pool_obj.get("application_persistence_profile_ref", "")
        p.persistence = _name_from_ref(persist_ref)
        if p.persistence:
            persist_obj = persistence.get(p.persistence, {})
            if persist_obj.get("_fortiadc_persistence_type") is None:
                p.warnings.append(
                    f"Persistence type '{persist_obj.get('persistence_type','')}' "
                    f"has no direct FortiADC equivalent."
                )

        # DataScripts
        if vs.get("_has_datascripts"):
            ds_refs = vs.get("vs_datascripts", [])
            p.datascripts = [_name_from_ref(d.get("vs_datascript_set_ref", ""))
                             for d in ds_refs]
            for ds_name in p.datascripts:
                ds_obj = datascripts.get(ds_name, {})
                lines  = ds_obj.get("_total_lines", 0)
                p.blockers.append(
                    f"DataScript '{ds_name}' ({lines} lines) requires manual "
                    f"reimplementation in FortiADC."
                )
            p.can_auto_migrate = False

        # HTTP policies
        if vs.get("_has_http_policies"):
            for hp in vs.get("http_policies", []):
                p.http_policies.append(_name_from_ref(hp.get("http_policy_set_ref", "")))
            p.warnings.append(
                f"VS has {len(p.http_policies)} HTTP policy set(s) — "
                f"review FortiADC content routing rules."
            )

        profiles[name] = p

    return profiles


def find_shared_objects(profiles: dict[str, VSProfile]) -> dict[str, list[str]]:
    """
    Returns dict of {object_name: [vs_names_using_it]} for shared objects.
    Shared certs/profiles are high-priority — a migration issue affects multiple VSes.
    """
    usage: dict[str, list[str]] = {}
    for vs_name, p in profiles.items():
        for cert in p.ssl_certs:
            usage.setdefault(cert, []).append(vs_name)
        for obj in [p.ssl_profile, p.app_profile, p.net_profile, p.persistence]:
            if obj:
                usage.setdefault(obj, []).append(vs_name)
    return {k: v for k, v in usage.items() if len(v) > 1}


def _index_by_name(items: list[dict]) -> dict[str, dict]:
    return {item.get("name", ""): item for item in items if item.get("name")}


def _name_from_ref(ref: str) -> str:
    if not ref:
        return ""
    if "#" in ref:
        return ref.split("#")[-1]
    if "name=" in ref:
        return ref.split("name=")[-1].split("&")[0]
    return ref.split("/")[-1]
