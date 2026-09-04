"""
analyzers/impact.py
Traffic impact assessment per Virtual Service.
Answers: "If this VS migration goes wrong, what is the blast radius?"
Uses runtime health data collected during discovery.
"""
from __future__ import annotations
from dataclasses import dataclass
from analyzers.dependency_graph import VSProfile


@dataclass
class ImpactAssessment:
    vs_name:          str
    risk_level:       str   # LOW | MEDIUM | HIGH | CRITICAL
    reason:           str
    member_count:     int
    members_up:       int
    shared_objects:   list[str]
    recommendation:   str


def assess_impact(vs_profiles: dict[str, VSProfile],
                  discovery: dict,
                  shared_objects: dict[str, list[str]]) -> list[ImpactAssessment]:
    """Produce impact assessment for each VS."""
    assessments = []
    pools_by_name = {p["name"]: p for p in discovery.get("pools", []) if p.get("name")}

    for vs_name, profile in vs_profiles.items():
        pool = pools_by_name.get(profile.pool or "", {})
        member_count = pool.get("_member_count", 0)
        members_up   = pool.get("_members_up", 0)

        # Shared objects that affect multiple VSes
        shared = [obj for obj, users in shared_objects.items() if vs_name in users]

        # Risk classification
        if not profile.can_auto_migrate:
            risk = "HIGH"
            reason = f"{len(profile.blockers)} blocker(s) — cannot auto-migrate"
        elif profile.health_state not in ("OPER_UP", "UNKNOWN"):
            risk = "HIGH"
            reason = f"VS currently not healthy ({profile.health_state})"
        elif members_up < member_count and member_count > 0:
            risk = "MEDIUM"
            reason = f"Only {members_up}/{member_count} pool members healthy"
        elif shared:
            risk = "MEDIUM"
            reason = f"Uses {len(shared)} shared object(s) — changes affect other VSes"
        elif profile.warnings:
            risk = "LOW"
            reason = f"{len(profile.warnings)} warning(s) requiring review"
        else:
            risk = "LOW"
            reason = "All checks clean"

        # Recommendation
        if risk == "HIGH":
            rec = ("Resolve all blockers before scheduling migration. "
                   "Migrate last within this environment.")
        elif risk == "MEDIUM":
            rec = ("Test thoroughly in Dev before production. "
                   "Migrate during low-traffic window.")
        else:
            rec = "Candidate for early migration. Good test case."

        assessments.append(ImpactAssessment(
            vs_name=vs_name, risk_level=risk, reason=reason,
            member_count=member_count, members_up=members_up,
            shared_objects=shared, recommendation=rec,
        ))

    return sorted(assessments, key=lambda a: {"CRITICAL":0,"HIGH":1,"MEDIUM":2,"LOW":3}
                  .get(a.risk_level, 9))
