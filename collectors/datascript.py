"""
collectors/datascript.py
Collects DataScript sets from Avi.
DataScripts are Lua/Python code — NONE can be auto-migrated.
Captures full code for engineer review and LLM-assisted translation.
"""
from __future__ import annotations
from core.base import BaseCollector
from core.events import Phase


class DataScriptCollector(BaseCollector):
    object_type  = "VSDataScriptSet"
    display_name = "DataScript Sets"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for ds in items:
            if not isinstance(ds, dict):
                continue
            scripts = ds.get("datascript", [])
            total_lines = 0
            events_used = []

            for script in scripts:
                code  = script.get("script", "")
                event = script.get("evt", "")
                events_used.append(event)
                total_lines += len(code.splitlines())

            ds["_total_lines"] = total_lines
            ds["_events"]      = events_used

            # Always MANUAL — no exceptions
            self._bus.manual(
                Phase.COLLECT,
                f"DataScript '{ds['name']}' ({total_lines} lines, "
                f"events: {', '.join(events_used)}) — "
                f"cannot be automatically migrated to FortiADC",
                object_type="VSDataScriptSet",
                object_name=ds["name"],
                object_uuid=ds.get("uuid", ""),
                detail={
                    "total_lines": total_lines,
                    "events": events_used,
                    "scripts": [
                        {
                            "event": s.get("evt", ""),
                            "line_count": len(s.get("script", "").splitlines()),
                            # Full code captured for human review
                            # SANITIZED version replaces IPs in code
                            "code_preview": s.get("script", "")[:200] + (
                                "..." if len(s.get("script", "")) > 200 else ""
                            ),
                        }
                        for s in scripts
                    ],
                    "options": [
                        "1. Use FortiADC content routing rules for simple redirects/rewrites",
                        "2. Use FortiADC WAF custom signatures for security logic",
                        "3. Move business logic to application layer",
                        "4. Use nginx/HAProxy sidecar for complex scripting needs",
                        "5. Use OpsAI LLM (via sanitized output) for translation assistance",
                    ],
                    "llm_hint": (
                        "Run: python3 migrate.py sanitize-datascript "
                        f"--name '{ds['name']}' "
                        "to get LLM-safe version of this DataScript for AI translation."
                    ),
                },
            )
        return items
