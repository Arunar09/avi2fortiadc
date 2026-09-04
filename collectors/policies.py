"""
collectors/policies.py
HTTP policy sets, WAF policies, auth profiles.
"""
from __future__ import annotations
from core.base import BaseCollector
from core.events import Phase


class HTTPPolicySetCollector(BaseCollector):
    object_type  = "HTTPPolicySet"
    display_name = "HTTP Policy Sets"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for policy in items:
            req_rules  = policy.get("http_request_policy",  {}).get("rules", [])
            resp_rules = policy.get("http_response_policy", {}).get("rules", [])
            sec_rules  = policy.get("http_security_policy", {}).get("rules", [])
            total = len(req_rules) + len(resp_rules) + len(sec_rules)
            policy["_total_rules"] = total

            # Classify rule actions
            complex_actions = {"REDIRECT", "REWRITE_URL", "SEND_RESPONSE",
                               "RATE_LIMIT", "DOS_MITIGATION"}
            has_complex = False
            for rule in req_rules + resp_rules + sec_rules:
                for action_key in rule.get("switching_action", {}).keys():
                    if any(c in action_key.upper() for c in complex_actions):
                        has_complex = True

            if has_complex:
                self._bus.warn(
                    Phase.COLLECT,
                    f"HTTP policy '{policy['name']}' has complex actions "
                    f"({total} rules) — FortiADC content routing has different syntax",
                    object_type="HTTPPolicySet", object_name=policy["name"],
                    detail={
                        "rule_count": total,
                        "action": (
                            "Review each rule. Simple redirects and header modifications "
                            "can be recreated in FortiADC content routing. Complex "
                            "rate-limiting or custom responses need WAF policy or "
                            "application-layer changes. Use sanitized output with LLM."
                        ),
                    },
                )
        return items


class WAFPolicyCollector(BaseCollector):
    object_type  = "WafPolicy"
    display_name = "WAF Policies"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for policy in items:
            self._bus.manual(
                Phase.COLLECT,
                f"WAF policy '{policy['name']}' — FortiADC WAF uses different "
                f"rule format (FortiGuard signatures, not Avi CRS)",
                object_type="WafPolicy", object_name=policy["name"],
                detail={
                    "crs_group": policy.get("crs_groups", []),
                    "action": (
                        "Enable FortiADC WAF on the corresponding virtual server "
                        "and configure FortiGuard WAF signatures. CRS rules from "
                        "Avi must be manually mapped to FortiGuard signature IDs."
                    ),
                },
            )
        return items


class AuthProfileCollector(BaseCollector):
    object_type  = "AuthProfile"
    display_name = "Authentication Profiles"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for profile in items:
            ptype = profile.get("type", "")
            if "LDAP" in ptype.upper():
                ldap = profile.get("ldap", {})
                # Redact server details — keep structure
                profile["_ldap_summary"] = {
                    "base_dn":        ldap.get("base_dn", ""),
                    "bind_as_administrator": ldap.get("bind_as_administrator", False),
                    "security":       ldap.get("security", ""),
                }
                self._bus.info(
                    Phase.COLLECT,
                    f"Auth profile '{profile['name']}' (LDAP) — "
                    f"FortiADC supports LDAP, reconfigure with same server details",
                    object_type="authprofile", object_name=profile["name"],
                )
            elif "SAML" in ptype.upper():
                self._bus.manual(
                    Phase.COLLECT,
                    f"Auth profile '{profile['name']}' (SAML) — "
                    f"verify FortiADC SAML SP configuration matches your IdP",
                    object_type="AuthProfile", object_name=profile["name"],
                    detail={"action": "Reconfigure SAML SP in FortiADC. "
                                      "Update IdP to trust new FortiADC SP metadata."},
                )
        return items
