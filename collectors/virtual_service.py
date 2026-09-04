"""
collectors/virtual_service.py
Collects all Virtual Services from Avi with full dependency resolution.
Also collects runtime health state so migration report shows current VS status.
"""
from __future__ import annotations
from core.base import BaseCollector
from core.events import Phase


class VirtualServiceCollector(BaseCollector):
    object_type    = "VirtualService"
    display_name   = "Virtual Services"
    collect_runtime = True

    def _post_process(self, items: list[dict]) -> list[dict]:
        """Resolve all URL references to object names for readability."""
        enriched = []
        for vs in items:
            if not isinstance(vs, dict):
                continue
            vs["_resolved"] = {
                "pool_ref":               self._name_from_ref(vs.get("pool_ref", "")),
                "application_profile_ref":self._name_from_ref(vs.get("application_profile_ref", "")),
                "ssl_profile_ref":        self._name_from_ref(vs.get("ssl_profile_ref", "")),
                "network_profile_ref":    self._name_from_ref(vs.get("network_profile_ref", "")),
                "ssl_key_and_certificate_refs": [
                    self._name_from_ref(r)
                    for r in vs.get("ssl_key_and_certificate_refs", [])
                ],
                "vsvip_ref":              self._name_from_ref(vs.get("vsvip_ref", "")),
                "se_group_ref":           self._name_from_ref(vs.get("se_group_ref", "")),
            }

            # Flag DataScripts — always manual
            ds_refs = vs.get("vs_datascripts", [])
            if ds_refs:
                vs["_has_datascripts"] = True
                self._bus.manual(
                    Phase.COLLECT,
                    f"VS '{vs['name']}' has {len(ds_refs)} DataScript(s) — "
                    f"DataScripts cannot be automatically migrated to FortiADC",
                    object_type="VirtualService",
                    object_name=vs["name"],
                    object_uuid=vs.get("uuid", ""),
                    detail={
                        "datascript_count": len(ds_refs),
                        "datascript_refs":  [self._name_from_ref(d.get("vs_datascript_set_ref",""))
                                             for d in ds_refs],
                        "suggestion": (
                            "Review each DataScript's logic and implement equivalent "
                            "functionality in FortiADC WAF rules, or move logic to "
                            "the application layer. Use the sanitized output to "
                            "ask an LLM for translation assistance."
                        ),
                    },
                )
            else:
                vs["_has_datascripts"] = False

            # Flag HTTP policy sets
            if vs.get("http_policies"):
                vs["_has_http_policies"] = True
            else:
                vs["_has_http_policies"] = False

            # Capture VIP
            vip_list = vs.get("vip", [])
            vs["_vips"] = [v.get("ip_address", {}).get("addr", "") for v in vip_list]

            # Runtime state
            rt = vs.get("_runtime", {})
            vs["_health"] = rt.get("oper_status", {}).get("state", "UNKNOWN")

            enriched.append(vs)
        return enriched

    def _name_from_ref(self, ref: str) -> str:
        """Extract object name from Avi URL reference."""
        if not ref:
            return ""
        # Avi refs look like: /api/pool?name=my-pool&tenant=admin
        # or: https://controller/api/pool/uuid-here#my-pool
        if "#" in ref:
            return ref.split("#")[-1]
        if "name=" in ref:
            return ref.split("name=")[-1].split("&")[0]
        return ref.split("/")[-1]
