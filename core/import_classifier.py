"""core/import_classifier.py

Deterministic classification of all top-level keys in an Avi export JSON.

This module is intentionally LLM-free and provides:
- raw key inventory for every top-level key
- group classification for user-driven import-scope decisions

Groups:
- pipeline: keys that map directly into this tool's strict discovery families
- gslb_related: keys starting with "Gslb" (with mapped_valid highlighting for GslbService)
- context: useful but unmapped keys
- noise: internal/meta/test keys

The output is designed to be stored in decision manifests and to drive a user UI.
"""

from __future__ import annotations

import re
from typing import Any

from core.avi_import import RAW_TO_DISCOVERY_MAP, DISCOVERY_KEYS


GROUP_PIPELINE = "pipeline"
GROUP_CONTEXT = "context"
GROUP_NOISE = "noise"

VALID_RAW_DECISIONS = {
    "include_in_pipeline",
    "context_only",
    "exclude",
}


def _value_type(v: Any) -> str:
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "scalar"


def _count_value(v: Any) -> int:
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        return len(v)
    return 1 if v is not None else 0


def _default_decision_for_group(group: str) -> str:
    if group == GROUP_PIPELINE:
        return "include_in_pipeline"
    if group == GROUP_GSLB_RELATED:
        return "context_only"
    if group == GROUP_CONTEXT:
        return "context_only"
    if group == GROUP_NOISE:
        return "exclude"
    return "context_only"


def classify_raw_keys(raw_json: dict[str, Any]) -> dict[str, Any]:
    """Classify every top-level key in a JSON dict.

    Args:
        raw_json: parsed JSON object at root.

    Returns:
        dict with:
          - groups: mapping of group_name -> {keys: [raw_key,...], mapped_families: {...}}
          - inventory: mapping raw_key -> {count, value_type, group, mapped_family, default_decision}

    Determinism:
        The classification depends only on the key name and the JSON value type/size.
    """
    if not isinstance(raw_json, dict):
        raise ValueError("classify_raw_keys expects a dict at the JSON root")

    # Pre-compute strict family keys for normalized inputs.
    discovery_family_keys = {k for k in DISCOVERY_KEYS if k != "_meta"}

    groups: dict[str, dict[str, Any]] = {
        GROUP_PIPELINE: {"keys": [], "mapped_families": {}},
        GROUP_CONTEXT: {"keys": [], "mapped_families": {}},
        GROUP_NOISE: {"keys": [], "mapped_families": {}},
    }

    inventory: dict[str, Any] = {}

    noise_patterns = [
        lambda k: k == "META",
        lambda k: k.startswith("EtcdData"),
        lambda k: k.startswith("TestSeDatastore"),
        lambda k: k == "SystemDefaultObject",
        lambda k: k.startswith("ALBServices"),
        lambda k: k == "InitialConfiguration",
    ]

    for raw_key, val in raw_json.items():
        vtype = _value_type(val)
        cnt = _count_value(val)

        # Determine mapped family:
        mapped_family = None
        if raw_key in RAW_TO_DISCOVERY_MAP:
            mapped_family = RAW_TO_DISCOVERY_MAP[raw_key]

        # If the user uploads a normalized discovery JSON, some keys are already
        # strict discovery families. Treat them as pipeline keys.
        if raw_key in discovery_family_keys:
            mapped_family = raw_key

        # Group precedence:
        # - pipeline (directly mapped families and already-normalized discovery keys)
        # - noise
        # - context (default)
        if raw_key in RAW_TO_DISCOVERY_MAP or raw_key in discovery_family_keys:
            group = GROUP_PIPELINE
        elif any(p(raw_key) for p in noise_patterns):
            group = GROUP_NOISE
        else:
            group = GROUP_CONTEXT

        # Special-case _meta: it must exist for strict import. We treat it as noise
        # only for display, but the candidate discovery always keeps it.
        if raw_key == "_meta":
            group = GROUP_CONTEXT

        inv_item = {
            "raw_key": raw_key,
            "value_type": vtype,
            "count": cnt,
            "group": group,
            "mapped_family": mapped_family,
            "default_decision": _default_decision_for_group(group),
        }
        inventory[raw_key] = inv_item

        groups[group]["keys"].append(raw_key)
        if mapped_family and group in (GROUP_PIPELINE, GROUP_GSLB_RELATED):
            groups[group]["mapped_families"].setdefault(mapped_family, 0)
            groups[group]["mapped_families"][mapped_family] += 1

    # Sort keys for deterministic UI display.
    for g in groups.values():
        g["keys"].sort()

    return {
        "groups": groups,
        "inventory": inventory,
        "raw_key_total": len(inventory),
        "version": 1,
    }

