"""
core/conflict_resolver.py
Adapts generated configurations to intelligently coexist with existing target FortiADC state.
"""
import logging
from typing import Any

class ConflictResolver:
    def __init__(self, target_config: dict):
        self.target_config = target_config or {}
        
    def resolve(self, generated_config: dict) -> tuple[dict, list[dict]]:
        """
        Compares generated config against target reference.
        Returns (adapted_config, collision_events).
        """
        adapted = {}
        events = []
        
        for section, items in generated_config.items():
            if not isinstance(items, list):
                adapted[section] = items
                continue
                
            adapted[section] = []
            existing_items = self.target_config.get(section, [])
            existing_by_mkey = {
                (obj.get("payload", {}).get("mkey") or obj.get("name")): obj 
                for obj in existing_items if isinstance(obj, dict)
            }
            
            for item in items:
                if not isinstance(item, dict):
                    adapted[section].append(item)
                    continue
                    
                mkey = item.get("payload", {}).get("mkey") or item.get("name", "")
                if not mkey:
                    adapted[section].append(item)
                    continue
                
                if mkey in existing_by_mkey:
                    existing_item = existing_by_mkey[mkey]
                    
                    # Check if payloads are identical
                    if item.get("payload") == existing_item.get("payload"):
                        # Identical: Reuse existing safely
                        events.append({
                            "level": "INFO",
                            "message": f"Coexistence: Reusing existing target {section[:-1] if section.endswith('s') else section} '{mkey}'.",
                            "object_type": section,
                            "object_name": mkey
                        })
                        adapted[section].append(existing_item)
                    else:
                        # Conflict: Adapt name to safely coexist
                        new_mkey = f"{mkey}_avi_migrated"
                        events.append({
                            "level": "WARNING",
                            "message": f"Conflict: Target state '{mkey}' differs from migration. Renaming payload to '{new_mkey}'.",
                            "object_type": section,
                            "object_name": mkey
                        })
                        
                        adapted_item = item.copy()
                        adapted_item["payload"] = item["payload"].copy()
                        adapted_item["payload"]["mkey"] = new_mkey
                        adapted_item["name"] = new_mkey
                        adapted[section].append(adapted_item)
                else:
                    # No conflict, keep generated
                    adapted[section].append(item)
                    
        return adapted, events
