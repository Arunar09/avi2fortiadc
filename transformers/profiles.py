"""
transformers/profiles.py
Transforms Avi profile objects to FortiADC profile format.
Covers: application profiles (HTTP/TCP/UDP), network profiles, persistence profiles.
"""
from __future__ import annotations

from core.base import BaseTransformer
from core.events import Phase
from transformers.mappings import (
    map_app_profile_type, map_persistence_type,
    FORTIADC_PATHS, DEFAULTS,
)


class ApplicationProfileTransformer(BaseTransformer):
    object_type   = "applicationprofile"
    fortiadc_path = FORTIADC_PATHS["http_profile"]   # default; overridden per type

    def transform(self, avi_profile: dict) -> dict | None:
        name  = avi_profile.get("name", "?")
        atype = avi_profile.get("type", "")
        fadc_type = map_app_profile_type(atype)

        if fadc_type is None:
            self._flag_unsupported(
                avi_profile,
                reason=f"Application profile type '{atype}' has no FortiADC equivalent",
                suggestion="Replace with TCP profile or redesign service.",
            )
            return None

        # Map to the correct FortiADC profile path
        path_map = {
            "http": FORTIADC_PATHS["http_profile"],
            "tcp":  FORTIADC_PATHS["tcp_profile"],
            "udp":  FORTIADC_PATHS["udp_profile"],
            "dns":  "load_balance/profile/dns",
        }
        fortiadc_path = path_map.get(fadc_type, FORTIADC_PATHS["http_profile"])

        payload: dict = {"mkey": name}

        if fadc_type == "http":
            http = avi_profile.get("http_profile", {})
            payload.update({
                "x-forwarded-for":        "enable" if http.get("x_forwarded_proto_enabled") else "disable",
                "max-header-size":        str(http.get("max_header_size", 4096)),
                "keepalive":              "enable",
                "ssl-mirror":             "enable" if http.get("ssl_everywhere_enabled") else "disable",
            })
            if http.get("websockets_enabled"):
                payload["websocket"] = "enable"
            if http.get("compression_profile"):
                self._bus.warn(
                    Phase.TRANSFORM,
                    f"Application profile '{name}' has HTTP compression — "
                    f"manually configure compression in FortiADC HTTP profile",
                    object_type="applicationprofile", object_name=name,
                    detail={"action": "Enable compression in FortiADC HTTP profile settings"},
                )

            # CSP Header Auto-Mapping
            if http.get("csp_config") or http.get("http_dynamic_header_rewrite"):
                payload["_has_csp"] = True
                self._bus.info(
                    Phase.TRANSFORM,
                    f"Profile '{name}' has CSP/Dynamic Headers. "
                    f"Auto-generating FortiADC Content Rewriting 'Add-Header' rules.",
                    object_name=name,
                )

        elif fadc_type == "tcp":
            tcp = avi_profile.get("tcp_app_profile", {})
            payload.update({
                "timeout":         str(tcp.get("pkt_type_idle_timeout", DEFAULTS["connection_timeout"])),
                "syn-cookie":      "enable",
            })

        elif fadc_type == "udp":
            payload.update({"timeout": "300"})

        return {
            "fortiadc_path": fortiadc_path,
            "name":          name,
            "payload":       payload,
            "depends_on":    [],
            "_avi_type":     atype,
            "_fortiadc_type": fadc_type,
        }


class NetworkProfileTransformer(BaseTransformer):
    object_type   = "networkprofile"
    fortiadc_path = FORTIADC_PATHS["tcp_profile"]

    def transform(self, avi_profile: dict) -> dict | None:
        name = avi_profile.get("name", "?")
        tcp  = avi_profile.get("profile", {}).get("tcp_proxy_profile", {})

        payload = {
            "mkey":    name,
            "timeout": str(tcp.get("idle_connection_timeout",
                                    DEFAULTS["connection_timeout"])),
        }
        return {
            "fortiadc_path": self.fortiadc_path,
            "name":          name,
            "payload":       payload,
            "depends_on":    [],
        }


class PersistenceProfileTransformer(BaseTransformer):
    object_type   = "applicationpersistenceprofile"
    fortiadc_path = FORTIADC_PATHS["cookie_persist"]   # default

    def transform(self, avi_profile: dict) -> dict | None:
        name  = avi_profile.get("name", "?")
        ptype = avi_profile.get("persistence_type", "")
        fadc_type = map_persistence_type(ptype)

        if fadc_type is None:
            self._flag_unsupported(
                avi_profile,
                reason=f"Persistence type '{ptype}' has no FortiADC equivalent",
                suggestion="Replace with cookie or source-address persistence.",
            )
            return None

        timeout = avi_profile.get("persistence_timeout", 300)

        if fadc_type == "cookie":
            # FortiADC cookie persistence max timeout is 1800s
            if timeout > 1800:
                self._bus.warn(
                    Phase.TRANSFORM,
                    f"Persistence profile '{name}' timeout {timeout}s exceeds "
                    f"FortiADC maximum of 1800s — capped at 1800s",
                    object_type="applicationpersistenceprofile", object_name=name,
                    detail={
                        "avi_timeout_s":     timeout,
                        "fortiadc_timeout_s": 1800,
                        "action": "Verify applications tolerate 1800s max persistence.",
                    },
                )
                timeout = 1800

            http_only    = avi_profile.get("http_cookie_persistence_profile", {})
            cookie_name  = http_only.get("cookie_name", "FATPC")
            cookie_insert = http_only.get("always_send_cookie", False)

            payload = {
                "mkey":         name,
                "expire-type":  "duration" if timeout > 0 else "session",
                "expire":       str(timeout),
                "cookie-name":  cookie_name,
                "cookie-httponly": "enable",
                "match-across-servers": "disable",
            }
            path = FORTIADC_PATHS["cookie_persist"]

        elif fadc_type == "source-address":
            payload = {
                "mkey":         name,
                "timeout":      str(min(timeout, 3600)),
                "match-across-servers": "enable",
            }
            path = FORTIADC_PATHS["src_addr_persist"]

        elif fadc_type == "ssl-session-id":
            payload = {
                "mkey":         name,
                "timeout":      str(min(timeout, 3600)),
            }
            path = "load_balance/persistence/ssl_session_id"

        else:
            self._flag_unsupported(avi_profile,
                                   reason=f"Unhandled FortiADC type: {fadc_type}")
            return None

        return {
            "fortiadc_path": path,
            "name":          name,
            "payload":       payload,
            "depends_on":    [],
            "_avi_type":     ptype,
            "_fortiadc_type": fadc_type,
        }

class SSLProfileTransformer(BaseTransformer):
    """
    Transforms AVI SSL profiles to FortiADC client-SSL profiles.
    Maps TLS version constraints, warns on deprecated ciphers/versions.
    """
    object_type   = "sslprofile"
    fortiadc_path = FORTIADC_PATHS["ssl_profile"]

    # Deprecated cipher patterns — warn if found
    _DEPRECATED_CIPHERS = {
        "RC4", "EXPORT", "NULL", "DES", "MD5", "ADH", "AECDH",
        "SSLv2", "SSLv3",
    }

    def transform(self, avi_profile: dict) -> dict | None:
        from transformers.mappings import TLS_VERSION, TLS_DEPRECATED
        name = avi_profile.get("name", "?")

        # Map accepted TLS versions
        accepted_versions = avi_profile.get("accepted_versions", [])
        min_ver = "tls1.2"   # FortiADC default — safe minimum
        max_ver = "tls1.3"
        deprecated_found = []

        for ver_entry in accepted_versions:
            avi_ver = ver_entry.get("type", "")
            if avi_ver in TLS_DEPRECATED:
                deprecated_found.append(avi_ver)
            mapped = TLS_VERSION.get(avi_ver)
            if mapped == "tls1.2":
                min_ver = "tls1.2"
            elif mapped == "tls1.3":
                pass  # already set as max

        if deprecated_found:
            self._bus.warn(
                Phase.TRANSFORM,
                f"SSL profile '{name}' includes deprecated TLS versions: "
                f"{', '.join(deprecated_found)} — FortiADC blocks these by default. "
                f"Verify applications support TLS 1.2+.",
                object_type="sslprofile", object_name=name,
                detail={
                    "deprecated_versions": deprecated_found,
                    "action": (
                        "Update application TLS configuration to use TLS 1.2 or higher. "
                        "Do not enable TLS 1.0/1.1 in FortiADC — security regression."
                    ),
                },
            )

        # Cipher string check
        accepted_ciphers = avi_profile.get("accepted_ciphers", "")
        cipher_warnings = [
            c for c in self._DEPRECATED_CIPHERS
            if c.upper() in accepted_ciphers.upper()
        ]
        if cipher_warnings:
            self._bus.warn(
                Phase.TRANSFORM,
                f"SSL profile '{name}' references deprecated ciphers: "
                f"{', '.join(cipher_warnings)} — these will not be enabled in FortiADC.",
                object_type="sslprofile", object_name=name,
                detail={
                    "avi_ciphers": accepted_ciphers[:200],
                    "deprecated":  cipher_warnings,
                    "action": "Use FortiADC recommended cipher set — HIGH strength only.",
                },
            )

        # Build FortiADC client SSL profile payload
        payload = {
            "mkey":         name,
            "ssl-min-ver":  min_ver,
            "ssl-max-ver":  max_ver,
            "session-reuse": "enable",
            "session-reuse-time": "300",
        }

        # Map OCSP stapling
        if avi_profile.get("enable_ssl_session_reuse"):
            payload["session-reuse"] = "enable"

        return {
            "fortiadc_path": self.fortiadc_path,
            "name":          name,
            "payload":       payload,
            "depends_on":    [],
            "_avi_min_tls":  min_ver,
            "_deprecated_warned": bool(deprecated_found or cipher_warnings),
        }
