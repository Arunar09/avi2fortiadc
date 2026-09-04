"""
transformers/http_policy_set.py
Avi HTTPPolicySet → FortiADC Content Rewriting.
"""
from __future__ import annotations
import re
from core.base import BaseTransformer
from core.events import Phase
from transformers.mappings import FORTIADC_PATHS

class HTTPPolicySetTransformer(BaseTransformer):
    object_type   = "http_policy_sets"
    fortiadc_path = FORTIADC_PATHS.get("content_rewriting", "load_balance/content_rewriting")

    def transform(self, avi_policy: dict) -> list[dict]:
        """
        Transforms Avi HTTPPolicySet rules into a list of FortiADC Content Rewriting rules (1:1).
        Returns a list of FortiADC object definitions.
        """
        name = avi_policy.get("name", "unnamed")
        rules = avi_policy.get("http_request_policy", {}).get("rules", [])
        
        fadc_objects = []
        
        for idx, rule in enumerate(rules):
            rule_name = rule.get("name", f"{name}-rule-{idx}")
            fadc_rule = self._transform_rule(rule_name, rule, name)
            if fadc_rule:
                fadc_objects.append(fadc_rule)
                
        return fadc_objects

    def _transform_rule(self, rule_name: str, rule: dict, parent_name: str) -> dict | None:
        """Helper to transform a single Avi rule into a FortiADC Content Rewriting object."""
        
        # 1. Determine Action Type
        action = rule.get("switching_action") or rule.get("hdr_action") or rule.get("redirect_action") or {}
        
        # FortiADC Content Rewriting supports:
        # redirect, request-header-add, request-header-delete, request-header-rewrite, etc.
        
        action_type = "request-header-add" # Default placeholder
        payload = {
            "mkey": rule_name,
            "action": "",
            "content": "",
        }

        # Handle Redirect
        if "redirect_action" in rule:
            redir = rule["redirect_action"]
            action_type = "redirect"
            target = redir.get("path", "") or "/"
            payload["action"] = "redirect"
            payload["content"] = target
            payload["redirect-status-code"] = str(redir.get("status_code", "302")).replace("HTTP_REDIRECT_STATUS_CODE_", "")

        # Handle Header Manipulation
        elif "hdr_action" in rule:
            hdr_actions = rule["hdr_action"]
            if not isinstance(hdr_actions, list): hdr_actions = [hdr_actions]
            
            # Since we are doing 1:1, we only take the first action if multiple exist in one Avi rule
            act = hdr_actions[0]
            hdr_name = act.get("hdr", {}).get("name", "")
            hdr_val = act.get("hdr", {}).get("value", "")
            
            if act.get("action") == "HTTP_ADD_HDR":
                payload["action"] = "request-header-add"
                payload["header-name"] = hdr_name
                payload["content"] = hdr_val
            elif act.get("action") == "HTTP_REMOVE_HDR":
                payload["action"] = "request-header-delete"
                payload["header-name"] = hdr_name
            else:
                return None # Unsupported action

        else:
            return None # No actionable content found
        
        return {
            "fortiadc_path": self.fortiadc_path,
            "name": rule_name,
            "payload": payload,
            "depends_on": []
        }
