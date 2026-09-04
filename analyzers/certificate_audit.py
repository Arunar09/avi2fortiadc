from __future__ import annotations
"""
analyzers/certificate_audit.py
Full audit of all SSL certificates: expiry, exportability, usage, SANs.
Produces the certificate section of the migration report.
"""
from dataclasses import dataclass, field


@dataclass
class CertAudit:
    name:             str
    subject:          str
    issuer:           str
    expiry:           str
    days_until_expiry: int | None
    exportable:       bool
    sans:             list[str]
    used_by_vses:     list[str]
    action:           str
    priority:         str   # IMMEDIATE | BEFORE_MIGRATION | MONITOR


def audit_certificates(discovery: dict,
                       vs_profiles: dict) -> list[CertAudit]:
    """Build full certificate audit list."""
    # Map cert names to VSes that use them
    cert_to_vses: dict[str, list[str]] = {}
    for vs_name, profile in vs_profiles.items():
        for cert_name in profile.ssl_certs:
            cert_to_vses.setdefault(cert_name, []).append(vs_name)

    audits = []
    for cert in discovery.get("ssl_certificates", []):
        name    = cert.get("name", "?")
        days    = cert.get("_days_until_expiry")
        exp     = cert.get("_exportable", True)
        used_by = cert_to_vses.get(name, [])

        # Determine action + priority
        if not exp:
            action   = "Request new certificate from CA. Import directly to FortiADC."
            priority = "IMMEDIATE"
        elif days is not None and days < 0:
            action   = "Certificate expired. Renew immediately."
            priority = "IMMEDIATE"
        elif days is not None and days < 30:
            action   = "Renew within 2 weeks. Import to FortiADC before migration."
            priority = "BEFORE_MIGRATION"
        else:
            action   = "Export from Avi and import to FortiADC."
            priority = "BEFORE_MIGRATION"

        audits.append(CertAudit(
            name=name,
            subject=cert.get("_subject", ""),
            issuer=cert.get("_issuer", ""),
            expiry=cert.get("_not_after", ""),
            days_until_expiry=days,
            exportable=exp,
            sans=cert.get("_sans", []),
            used_by_vses=used_by,
            action=action,
            priority=priority,
        ))

    return sorted(audits, key=lambda c: {"IMMEDIATE":0,"BEFORE_MIGRATION":1,"MONITOR":2}
                  .get(c.priority, 9))
