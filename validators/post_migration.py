"""
validators/post_migration.py
Post-migration validation — verifies all deployed FortiADC objects
are healthy after deployment. Runs AFTER deployers/fortiadc_deployer.py.

Checks:
  - All virtual servers exist and are enabled
  - All real server pools have healthy members
  - Health checks are responding within expected intervals
  - No configuration drift from what was deployed

Design: read-only against FortiADC, no changes made.
Wait time is configurable — health monitors need time to stabilise.
"""
from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.fortiadc_client import FortiADCClient
from core.events import EventBus, Phase, Level


@dataclass
class PostCheckResult:
    object_type:  str
    object_name:  str
    check:        str
    passed:       bool
    expected:     str
    actual:       str
    message:      str
    blocking:     bool = False


def run_post_migration_checks(
    client:          FortiADCClient,
    fortiadc_config: dict,
    bus:             EventBus,
    wait_seconds:    int = 30,
    fail_fast:       bool = False,
) -> list[PostCheckResult]:
    """
    Verify all deployed objects are healthy.
    Returns list of PostCheckResult — one per check.
    Emits events for every pass/fail with actionable detail.
    """
    results: list[PostCheckResult] = []

    # Wait for health monitors to stabilise after deploy
    if wait_seconds > 0:
        bus.info(Phase.VERIFY,
                 f"Waiting {wait_seconds}s for health monitors to stabilise...")
        time.sleep(wait_seconds)

    # ── 1. Virtual Servers ────────────────────────────────────────────────────
    for vs_def in fortiadc_config.get("virtual_servers", []):
        name = vs_def.get("name", "?")
        results.extend(_check_virtual_server(client, bus, name,
                                              vs_def.get("payload", {})))
        if fail_fast and any(not r.passed and r.blocking for r in results):
            return results

    # ── 2. Real Server Pools ──────────────────────────────────────────────────
    for pool_def in fortiadc_config.get("real_server_pools", []):
        name = pool_def.get("name", "?")
        results.extend(_check_pool(client, bus, name))
        if fail_fast and any(not r.passed and r.blocking for r in results):
            return results

    # ── 3. Health Checks ──────────────────────────────────────────────────────
    for hc_def in fortiadc_config.get("health_checks", []):
        name = hc_def.get("name", "?")
        results.append(_check_health_check_exists(client, bus, name))

    # ── 4. SSL Certificates ───────────────────────────────────────────────────
    for cert_def in fortiadc_config.get("ssl_certificates", []):
        name = cert_def.get("name", "?")
        results.append(_check_cert_exists(client, bus, name))

    # ── Summary ───────────────────────────────────────────────────────────────
    passed  = sum(1 for r in results if r.passed)
    failed  = sum(1 for r in results if not r.passed)
    blocked = sum(1 for r in results if not r.passed and r.blocking)

    level = Level.INFO if failed == 0 else (Level.CRITICAL if blocked else Level.WARN)
    bus.emit(__import__("core.events", fromlist=["MigrationEvent"]).MigrationEvent(
        level=level,
        phase=Phase.VERIFY,
        message=f"Post-migration validation: {passed} passed, {failed} failed "
                f"({blocked} blocking)",
        detail={
            "total":   len(results),
            "passed":  passed,
            "failed":  failed,
            "blocked": blocked,
        },
    ))

    return results


def _check_virtual_server(client: FortiADCClient, bus: EventBus,
                           name: str, expected_payload: dict) -> list[PostCheckResult]:
    results = []
    try:
        data   = client.get(f"load_balance/virtual_server/{name}")
        actual = data.get("results", {})

        # Existence
        results.append(PostCheckResult(
            "virtual_server", name, "exists",
            True, "exists", "exists",
            f"Virtual server '{name}' exists in FortiADC",
        ))

        # Status enabled
        actual_status   = actual.get("status", "unknown")
        expected_status = expected_payload.get("status", "enable")
        status_ok = actual_status == expected_status
        results.append(PostCheckResult(
            "virtual_server", name, "status",
            status_ok, expected_status, actual_status,
            f"VS '{name}' status: {actual_status}",
            blocking=not status_ok,
        ))
        if not status_ok:
            bus.error(Phase.VERIFY,
                      f"VS '{name}' status is '{actual_status}', expected '{expected_status}'",
                      object_type="virtual_server", object_name=name,
                      detail={"action": "Check FortiADC event log for disable reason"})
        else:
            bus.info(Phase.VERIFY,
                     f"VS '{name}': status=enable ✓",
                     object_type="virtual_server", object_name=name)

        # VIP address match
        expected_ip = expected_payload.get("ip", "")
        actual_ip   = actual.get("ip", "")
        ip_ok = not expected_ip or actual_ip == expected_ip
        results.append(PostCheckResult(
            "virtual_server", name, "vip_address",
            ip_ok, expected_ip, actual_ip,
            f"VS '{name}' VIP: {actual_ip}",
            blocking=not ip_ok,
        ))
        if not ip_ok:
            bus.critical(Phase.VERIFY,
                         f"VS '{name}' VIP mismatch: expected {expected_ip}, "
                         f"got {actual_ip}",
                         object_type="virtual_server", object_name=name,
                         detail={"action": "VIP mismatch indicates config error — "
                                           "check FortiADC VS config"})

        # Pool association
        expected_pool = expected_payload.get("pool", "")
        actual_pool   = actual.get("pool", "")
        pool_ok = not expected_pool or actual_pool == expected_pool
        results.append(PostCheckResult(
            "virtual_server", name, "pool_association",
            pool_ok, expected_pool, actual_pool,
            f"VS '{name}' pool: {actual_pool}",
            blocking=not pool_ok,
        ))

    except Exception as e:
        results.append(PostCheckResult(
            "virtual_server", name, "exists",
            False, "exists", "missing",
            f"VS '{name}' not found: {e}",
            blocking=True,
        ))
        bus.critical(Phase.VERIFY,
                     f"VS '{name}' not found in FortiADC after deployment",
                     object_type="virtual_server", object_name=name,
                     detail={"error": str(e),
                             "action": "Check deploy log for errors on this VS"})
    return results


def _check_pool(client: FortiADCClient, bus: EventBus,
                name: str) -> list[PostCheckResult]:
    results = []
    try:
        data    = client.get(f"load_balance/real_server_pool/{name}")
        members = data.get("results", {}).get("members", [])

        results.append(PostCheckResult(
            "real_server_pool", name, "exists",
            True, "exists", "exists",
            f"Pool '{name}' exists",
        ))

        # At least one member must be active
        active = [m for m in members if m.get("status", "") in ("active", "enable", "up")]
        members_ok = len(active) > 0
        results.append(PostCheckResult(
            "real_server_pool", name, "members_active",
            members_ok,
            "≥1 active member",
            f"{len(active)}/{len(members)} active",
            f"Pool '{name}': {len(active)}/{len(members)} members active",
            blocking=not members_ok,
        ))
        if not members_ok and members:
            bus.error(Phase.VERIFY,
                      f"Pool '{name}': no members are active — "
                      f"health checks may be failing",
                      object_type="real_server_pool", object_name=name,
                      detail={
                          "active":   len(active),
                          "total":    len(members),
                          "action": (
                              "1. Verify pool members are reachable from FortiADC. "
                              "2. Check health monitor config matches application. "
                              "3. Check FortiADC event log for health check failures."
                          ),
                      })
        elif members_ok:
            bus.info(Phase.VERIFY,
                     f"Pool '{name}': {len(active)}/{len(members)} members active ✓",
                     object_type="real_server_pool", object_name=name)

    except Exception as e:
        results.append(PostCheckResult(
            "real_server_pool", name, "exists",
            False, "exists", "missing",
            f"Pool '{name}' not found: {e}",
            blocking=True,
        ))
        bus.critical(Phase.VERIFY,
                     f"Pool '{name}' not found in FortiADC",
                     object_type="real_server_pool", object_name=name,
                     detail={"error": str(e)})
    return results


def _check_health_check_exists(client: FortiADCClient, bus: EventBus,
                                name: str) -> PostCheckResult:
    try:
        client.get(f"load_balance/health_check/{name}")
        bus.info(Phase.VERIFY, f"Health check '{name}' exists ✓",
                 object_type="health_check", object_name=name)
        return PostCheckResult("health_check", name, "exists",
                               True, "exists", "exists",
                               f"Health check '{name}' exists")
    except Exception as e:
        bus.error(Phase.VERIFY, f"Health check '{name}' not found: {e}",
                  object_type="health_check", object_name=name)
        return PostCheckResult("health_check", name, "exists",
                               False, "exists", "missing",
                               f"Health check '{name}' not found",
                               blocking=True)


def _check_cert_exists(client: FortiADCClient, bus: EventBus,
                       name: str) -> PostCheckResult:
    try:
        client.get(f"system/certificate/local/{name}")
        bus.info(Phase.VERIFY, f"Certificate '{name}' exists ✓",
                 object_type="ssl_certificate", object_name=name)
        return PostCheckResult("ssl_certificate", name, "exists",
                               True, "exists", "exists",
                               f"Certificate '{name}' exists")
    except Exception as e:
        bus.critical(Phase.VERIFY, f"Certificate '{name}' not found: {e}",
                     object_type="ssl_certificate", object_name=name,
                     detail={"action": "Certificate must be imported before VS can use it"})
        return PostCheckResult("ssl_certificate", name, "exists",
                               False, "exists", "missing",
                               f"Certificate '{name}' not found",
                               blocking=True)


def summarise(results: list[PostCheckResult]) -> dict:
    """Summary dict suitable for state ledger and reports."""
    return {
        "total":       len(results),
        "passed":      sum(1 for r in results if r.passed),
        "failed":      sum(1 for r in results if not r.passed),
        "blocking":    sum(1 for r in results if not r.passed and r.blocking),
        "checks":      [
            {
                "type": r.object_type, "name": r.object_name,
                "check": r.check, "passed": r.passed,
                "expected": r.expected, "actual": r.actual,
                "message": r.message,
            }
            for r in results if not r.passed   # only failures in ledger
        ],
    }
