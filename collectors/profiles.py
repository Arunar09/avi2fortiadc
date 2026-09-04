"""
collectors/profiles.py
Collects application profiles, network profiles, and persistence profiles from Avi.
These map to FortiADC profiles with varying degrees of fidelity.
"""
from __future__ import annotations

from core.base import BaseCollector
from core.events import Phase


class ApplicationProfileCollector(BaseCollector):
    object_type = "ApplicationProfile"
    display_name = "Application Profiles"

    _TYPE_MAP = {
        "APPLICATION_PROFILE_TYPE_HTTP": "http",
        "APPLICATION_PROFILE_TYPE_HTTPS": "http",
        "APPLICATION_PROFILE_TYPE_TCP": "tcp",
        "APPLICATION_PROFILE_TYPE_UDP": "udp",
        "APPLICATION_PROFILE_TYPE_DNS": "dns",
        "APPLICATION_PROFILE_TYPE_SIP": None,
        "APPLICATION_PROFILE_TYPE_L4": "tcp",
    }

    def _post_process(self, items: list[dict]) -> list[dict]:
        for profile in items:
            if not isinstance(profile, dict):
                continue
            ptype = profile.get("type", "")
            fortiadc_type = self._TYPE_MAP.get(ptype)
            profile["_fortiadc_profile_type"] = fortiadc_type

            if fortiadc_type is None:
                self._bus.manual(
                    Phase.COLLECT,
                    f"Application profile '{profile['name']}' type '{ptype}' has no FortiADC equivalent",
                    object_type="ApplicationProfile",
                    object_name=profile["name"],
                    detail={
                        "avi_type": ptype,
                        "action": "Replace with TCP profile or redesign service.",
                    },
                )

            if ptype == "APPLICATION_PROFILE_TYPE_HTTP":
                http_cfg = profile.get("http_profile", {})
                profile["_xff_enabled"] = http_cfg.get("x_forwarded_proto_enabled", False)
                if http_cfg.get("compression_profile"):
                    self._bus.warn(
                        Phase.COLLECT,
                        f"Application profile '{profile['name']}' uses HTTP compression - FortiADC compression config differs",
                        object_type="ApplicationProfile",
                        object_name=profile["name"],
                        detail={"action": "Manually configure compression in FortiADC HTTP profile."},
                    )
                if http_cfg.get("websockets_enabled"):
                    self._bus.warn(
                        Phase.COLLECT,
                        f"Application profile '{profile['name']}' has WebSocket enabled - verify FortiADC virtual server supports WebSocket",
                        object_type="ApplicationProfile",
                        object_name=profile["name"],
                    )
        return items


class NetworkProfileCollector(BaseCollector):
    object_type = "NetworkProfile"
    display_name = "Network Profiles"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for profile in items:
            if not isinstance(profile, dict):
                continue
            tcp = profile.get("profile", {}).get("tcp_proxy_profile", {})
            profile["_connection_timeout"] = tcp.get("idle_connection_timeout", 0)
            profile["_max_connections"] = tcp.get("max_syn_retransmissions", 0)
        return items


class PersistenceProfileCollector(BaseCollector):
    object_type = "ApplicationPersistenceProfile"
    display_name = "Persistence Profiles"

    _PERSISTENCE_MAP = {
        "PERSISTENCE_TYPE_HTTP_COOKIE": "cookie",
        "PERSISTENCE_TYPE_APP_COOKIE": "cookie",
        "PERSISTENCE_TYPE_CLIENT_IP_ADDRESS": "source-address",
        "PERSISTENCE_TYPE_TLS": "ssl-session-id",
        "PERSISTENCE_TYPE_GSLB_SITE": None,
        "PERSISTENCE_TYPE_CUSTOM_HTTP_HEADER": None,
    }

    def _post_process(self, items: list[dict]) -> list[dict]:
        for profile in items:
            if not isinstance(profile, dict):
                continue
            ptype = profile.get("persistence_type", "")
            fortiadc_type = self._PERSISTENCE_MAP.get(ptype)
            profile["_fortiadc_persistence_type"] = fortiadc_type

            if fortiadc_type is None:
                self._bus.manual(
                    Phase.COLLECT,
                    f"Persistence profile '{profile['name']}' type '{ptype}' cannot be mapped to FortiADC",
                    object_type="ApplicationPersistenceProfile",
                    object_name=profile["name"],
                    detail={
                        "avi_type": ptype,
                        "action": "Replace with source-address or cookie persistence.",
                    },
                )

            timeout = profile.get("persistence_timeout", 0)
            if ptype == "PERSISTENCE_TYPE_HTTP_COOKIE" and timeout > 1800:
                self._bus.warn(
                    Phase.COLLECT,
                    f"Persistence profile '{profile['name']}' timeout {timeout}s - FortiADC default cookie persistence max is 1800s",
                    object_type="ApplicationPersistenceProfile",
                    object_name=profile["name"],
                    detail={
                        "avi_timeout_s": timeout,
                        "fortiadc_max_s": 1800,
                        "action": "Verify application tolerates reduced persistence window.",
                    },
                )
        return items
