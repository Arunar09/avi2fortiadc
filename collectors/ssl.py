"""
collectors/ssl.py
Collects SSL profiles and SSL key+certificate objects from Avi.
Flags HSM-backed certs (non-exportable) as CRITICAL blockers.
Extracts certificate PEM where available for re-import to FortiADC.
"""
from __future__ import annotations
from datetime import datetime, timezone
from core.base import BaseCollector
from core.events import Phase


class SSLProfileCollector(BaseCollector):
    object_type  = "sslprofile"
    display_name = "SSL Profiles"

    # Ciphers FortiADC does not support — flag for review
    _UNSUPPORTED_CIPHERS = {
        "TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA",
        "TLS_RSA_EXPORT_WITH_RC4_40_MD5",
        "TLS_RSA_WITH_RC4_128_MD5",
        "TLS_RSA_WITH_RC4_128_SHA",
    }

    def _post_process(self, items: list[dict]) -> list[dict]:
        for profile in items:
            if not isinstance(profile, dict):
                continue
            # Handle cases where accepted_ciphers contains raw strings
            raw_ciphers = profile.get("accepted_ciphers", [])
            accepted = []
            for c in raw_ciphers:
                if isinstance(c, dict):
                    accepted.append(c.get("name", ""))
                elif isinstance(c, str):
                    accepted.append(c)
            
            unsupported = [c for c in accepted if c in self._UNSUPPORTED_CIPHERS]
            profile["_unsupported_ciphers"] = unsupported

            # TLS version check
            versions = profile.get("accepted_versions", [])
            min_ver = ""
            if versions and isinstance(versions, list):
                first_v = versions[0]
                if isinstance(first_v, dict):
                    min_ver = first_v.get("type", "")
                elif isinstance(first_v, str):
                    min_ver = first_v
            
            if "SSL" in min_ver or "TLS1_0" in min_ver or "TLS1_1" in min_ver:
                self._bus.warn(
                    Phase.COLLECT,
                    f"SSL profile '{profile['name']}' allows deprecated TLS "
                    f"versions ({min_ver}) — FortiADC will enforce TLS 1.2+ by default",
                    object_type="sslprofile", object_name=profile["name"],
                    detail={"min_version": min_ver,
                            "action": "Review if any backend clients require old TLS versions. "
                                      "Adjust FortiADC SSL profile after migration."},
                )

            if unsupported:
                self._bus.warn(
                    Phase.COLLECT,
                    f"SSL profile '{profile['name']}' contains "
                    f"{len(unsupported)} cipher(s) not supported by FortiADC",
                    object_type="sslprofile", object_name=profile["name"],
                    detail={"unsupported_ciphers": unsupported},
                )
        return items


class SSLCertificateCollector(BaseCollector):
    object_type  = "sslkeyandcertificate"
    display_name = "SSL Certificates and Keys"

    def _post_process(self, items: list[dict]) -> list[dict]:
        now = datetime.now(timezone.utc)
        enriched = []
        for cert in items:
            if not isinstance(cert, dict):
                continue
            name = cert.get("name", "?")

            # HSM-backed = non-exportable private key
            hsm_ref = cert.get("hardwaresecuritymodulegroup_ref", "")
            if hsm_ref:
                cert["_exportable"] = False
                cert["_export_method"] = "hsm_blocked"
                self._bus.critical(
                    Phase.COLLECT,
                    f"Certificate '{name}' is HSM-backed — "
                    f"private key CANNOT be exported",
                    object_type="sslkeyandcertificate", object_name=name,
                    object_uuid=cert.get("uuid", ""),
                    detail={
                        "hsm_ref": hsm_ref,
                        "action": (
                            "Request a new certificate from your internal CA (Wintel team). "
                            "Import the new cert + key directly into FortiADC before migration. "
                            "Identify all Virtual Services referencing this cert "
                            "and plan for cert swap during migration window."
                        ),
                    },
                )
            else:
                cert["_exportable"] = True
                cert["_export_method"] = "api_export"

            # Extract certificate metadata
            cert_data = cert.get("certificate", {})
            cert["_subject"]     = cert_data.get("subject", {}).get("common_name", "")
            cert["_issuer"]      = cert_data.get("issuer", {}).get("common_name", "")
            cert["_not_after"]   = cert_data.get("not_after", "")
            cert["_sans"]        = cert_data.get("subject_alt_names", [])
            cert["_fingerprint"] = cert_data.get("fingerprint", "")

            # Expiry check
            not_after_str = cert["_not_after"]
            if not_after_str:
                try:
                    # Avi format: "2026-06-01 00:00:00"
                    not_after = datetime.strptime(
                        not_after_str, "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                    days_left = (not_after - now).days
                    cert["_days_until_expiry"] = days_left

                    if days_left < 0:
                        self._bus.critical(
                            Phase.COLLECT,
                            f"Certificate '{name}' EXPIRED {abs(days_left)} days ago "
                            f"— do not migrate without renewing",
                            object_type="sslkeyandcertificate", object_name=name,
                            detail={"expiry": not_after_str, "days_left": days_left},
                        )
                    elif days_left < 30:
                        self._bus.warn(
                            Phase.COLLECT,
                            f"Certificate '{name}' expires in {days_left} days "
                            f"— renew before or immediately after migration",
                            object_type="sslkeyandcertificate", object_name=name,
                            detail={"expiry": not_after_str, "days_left": days_left},
                        )
                    else:
                        cert["_days_until_expiry"] = days_left
                        self._bus.info(
                            Phase.COLLECT,
                            f"Certificate '{name}' valid for {days_left} days",
                            object_type="sslkeyandcertificate", object_name=name,
                        )
                except ValueError:
                    cert["_days_until_expiry"] = None

            enriched.append(cert)
        return enriched
