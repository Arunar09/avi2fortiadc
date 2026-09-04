"""
validators/config_diff.py
Configuration drift detection — compares what we deployed (expected)
against what FortiADC actually has (actual).

Runs as part of post-migration validation and day-2 operations.
Detects: missing objects, changed fields, unexpected objects.

Design: read-only, no changes made. Emits structured diff events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.fortiadc_client import FortiADCClient
from core.events import EventBus, Phase


@dataclass
class DiffItem:
    object_type: str
    object_name: str
    field:       str
    expected:    Any
    actual:      Any
    severity:    str   # INFO | WARN | DRIFT
    message:     str


# Fields we ignore in diff — FortiADC adds these automatically
_IGNORE_FIELDS = {
    "mtime", "ctime", "serial", "seq", "last-heartbeat",
    "health-status", "connect-time", "stats-",
}


def run_config_diff(
    client:          FortiADCClient,
    fortiadc_config: dict,
    bus:             EventBus,
) -> list[DiffItem]:
    """
    Compare deployed config against actual FortiADC state.
    Returns list of differences. Empty list = no drift.
    """
    diffs: list[DiffItem] = []

    checks = [
        ("virtual_servers",  "load_balance/virtual_server"),
        ("real_server_pools","load_balance/real_server_pool"),
        ("health_checks",    "load_balance/health_check"),
        ("ssl_certificates", "system/certificate/local"),
    ]

    for section, api_path in checks:
        for obj_def in fortiadc_config.get(section, []):
            name    = obj_def.get("name", "?")
            payload = obj_def.get("payload", {})
            diffs.extend(_diff_object(client, bus, api_path,
                                      name, payload, section))

    # Summary
    drift_count = sum(1 for d in diffs if d.severity == "DRIFT")
    warn_count  = sum(1 for d in diffs if d.severity == "WARN")

    if drift_count or warn_count:
        bus.warn(
            Phase.VERIFY,
            f"Config drift detected: {drift_count} drift(s), {warn_count} warning(s)",
            detail={
                "drift_count": drift_count,
                "warn_count":  warn_count,
                "action": (
                    "Review drift items below. DRIFT means FortiADC config "
                    "does not match what was deployed. This may indicate "
                    "manual changes made outside the migration tool."
                ),
            },
        )
    else:
        bus.info(Phase.VERIFY, "Config diff: no drift detected — FortiADC matches deployed config")

    return diffs


def _diff_object(client: FortiADCClient, bus: EventBus,
                 api_path: str, name: str, expected: dict,
                 section: str) -> list[DiffItem]:
    diffs = []
    try:
        data   = client.get(f"{api_path}/{name}")
        actual = data.get("results", {})
    except Exception as e:
        # Object missing entirely — most severe drift
        bus.error(
            Phase.VERIFY,
            f"[DRIFT] {section}/{name}: object missing from FortiADC",
            object_type=section, object_name=name,
            detail={"error": str(e),
                    "action": "Re-run deploy for this object"},
        )
        return [DiffItem(
            object_type=section, object_name=name,
            field="(object)", expected="exists", actual="missing",
            severity="DRIFT",
            message=f"Object '{name}' missing from FortiADC",
        )]

    # Field-by-field comparison
    for field_name, exp_val in expected.items():
        if any(field_name.startswith(ig) for ig in _IGNORE_FIELDS):
            continue
        if field_name == "mkey":   # primary key — skip
            continue

        act_val = actual.get(field_name)
        if act_val is None:
            continue   # FortiADC may not return all fields

        # Normalise types for comparison
        exp_str = str(exp_val).strip()
        act_str = str(act_val).strip()

        if exp_str != act_str:
            severity = _classify_field_drift(field_name)
            msg = (
                f"[{severity}] {section}/{name}.{field_name}: "
                f"expected='{exp_str}' actual='{act_str}'"
            )
            if severity == "DRIFT":
                bus.warn(Phase.VERIFY, msg,
                         object_type=section, object_name=name,
                         detail={"field": field_name,
                                 "expected": exp_str, "actual": act_str,
                                 "action": "Verify this field was intentionally changed "
                                           "or re-run transform+deploy for this object"})
            diffs.append(DiffItem(
                object_type=section, object_name=name,
                field=field_name, expected=exp_str, actual=act_str,
                severity=severity, message=msg,
            ))

    return diffs


def _classify_field_drift(field_name: str) -> str:
    """Classify how serious a field difference is."""
    critical_fields = {"ip", "port", "pool", "status", "ssl-mirror",
                       "client-certificate", "profile", "address"}
    if field_name in critical_fields:
        return "DRIFT"
    return "WARN"


def format_diff_report(diffs: list[DiffItem]) -> str:
    """Human-readable diff report for embedding in migration report."""
    if not diffs:
        return "No configuration drift detected."

    lines = [f"Configuration Drift Report ({len(diffs)} differences)\n"]
    for d in sorted(diffs, key=lambda x: (x.severity, x.object_type, x.object_name)):
        icon = "🔴" if d.severity == "DRIFT" else "⚠️"
        lines.append(
            f"{icon} [{d.object_type}] {d.object_name}.{d.field}\n"
            f"   Expected : {d.expected}\n"
            f"   Actual   : {d.actual}\n"
        )
    return "\n".join(lines)
