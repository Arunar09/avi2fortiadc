"""
validators/pre_migration.py
Pre-migration validation: check FortiADC is ready and no conflicts exist.
Runs BEFORE deploying anything. Dry-run safe — read-only.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from core.fortiadc_client import FortiADCClient
from core.events import EventBus, Phase


@dataclass
class ValidationResult:
    passed:   bool
    check:    str
    message:  str
    blocking: bool = True


def run_pre_migration_checks(
    client: FortiADCClient,
    fortiadc_config: dict,
    bus: EventBus,
) -> list[ValidationResult]:
    results = []

    # 1. FortiADC reachable
    try:
        client.get("system/status")
        results.append(ValidationResult(True, "fortiadc_reachable",
                                         "FortiADC API is responding"))
        bus.info(Phase.VALIDATE, "FortiADC: API reachable")
    except Exception as e:
        results.append(ValidationResult(False, "fortiadc_reachable",
                                         f"FortiADC unreachable: {e}"))
        bus.critical(Phase.VALIDATE, f"FortiADC API unreachable: {e}")
        return results   # no point continuing if unreachable

    # 2. VDOM exists
    vdom = client.vdom
    try:
        client.get(f"system/vdom/{vdom}")
        results.append(ValidationResult(True, "vdom_exists",
                                         f"VDOM '{vdom}' exists"))
    except Exception:
        results.append(ValidationResult(False, "vdom_exists",
                                         f"VDOM '{vdom}' not found"))
        bus.critical(Phase.VALIDATE, f"VDOM '{vdom}' does not exist in FortiADC",
                     detail={"action": "Create VDOM or update config.yaml vdom setting"})

    # 3. No name conflicts on virtual servers
    existing_vs = []
    try:
        data = client.get("load_balance/virtual_server")
        existing_vs = [r.get("mkey", "") for r in data.get("results", [])]
    except Exception:
        pass

    for vs in fortiadc_config.get("virtual_servers", []):
        if vs.get("name") in existing_vs:
            results.append(ValidationResult(
                False, f"no_conflict:{vs['name']}",
                f"Virtual server '{vs['name']}' already exists in FortiADC",
                blocking=True,
            ))
            bus.critical(Phase.VALIDATE,
                         f"Name conflict: VS '{vs['name']}' already exists",
                         detail={"action": "Delete existing VS or rename before migrating"})
        else:
            results.append(ValidationResult(
                True, f"no_conflict:{vs['name']}",
                f"VS '{vs['name']}' — no conflict"))

    # 4. SSL certificate space
    try:
        cert_data = client.get("system/certificate/local")
        cert_count = cert_data.get("total", 0)
        if cert_count > 900:
            results.append(ValidationResult(False, "cert_capacity",
                                             f"FortiADC has {cert_count}/1000 certs — near limit"))
            bus.warn(Phase.VALIDATE, f"FortiADC certificate store near capacity: {cert_count}/1000")
        else:
            results.append(ValidationResult(True, "cert_capacity",
                                             f"Certificate capacity OK: {cert_count}/1000"))
    except Exception:
        pass

    passed  = sum(1 for r in results if r.passed)
    failed  = sum(1 for r in results if not r.passed)
    bus.info(Phase.VALIDATE,
             f"Pre-migration validation: {passed} passed, {failed} failed")

    return results


"""
validators/post_migration.py
Post-migration validation: verify all VS are healthy in FortiADC.
Runs AFTER deploying. Compares expected state vs actual.
"""
import time
from dataclasses import dataclass


@dataclass
class HealthCheck:
    vs_name:  str
    expected: str   # "enable"
    actual:   str
    hc_status: str  # health check state


def run_post_migration_checks(
    client: FortiADCClient,
    fortiadc_config: dict,
    bus: EventBus,
    wait_seconds: int = 30,
) -> list[HealthCheck]:
    """Wait for health checks to stabilize, then verify all VS are UP."""
    bus.info(Phase.VERIFY, f"Waiting {wait_seconds}s for health checks to stabilize...")
    time.sleep(wait_seconds)

    results = []
    for vs_def in fortiadc_config.get("virtual_servers", []):
        name = vs_def.get("name", "?")
        try:
            data   = client.get(f"load_balance/virtual_server/{name}")
            actual = data.get("results", {}).get("status", "unknown")
            pool   = data.get("results", {}).get("pool", "")

            # Check pool health
            hc_status = "unknown"
            if pool:
                try:
                    pool_data  = client.get(f"load_balance/real_server_pool/{pool}")
                    hc_status  = pool_data.get("results", {}).get("status", "unknown")
                except Exception:
                    pass

            results.append(HealthCheck(vs_name=name, expected="enable",
                                        actual=actual, hc_status=hc_status))
            if actual == "enable" and hc_status in ("active", "enable", "up"):
                bus.info(Phase.VERIFY, f"VS '{name}': UP — pool health {hc_status}",
                         object_type="virtualservice", object_name=name)
            else:
                bus.error(Phase.VERIFY,
                          f"VS '{name}': status={actual}, pool={hc_status}",
                          object_type="virtualservice", object_name=name,
                          detail={"action": "Check pool members and health monitor config"})
        except Exception as e:
            bus.error(Phase.VERIFY, f"Cannot verify VS '{name}': {e}",
                      object_type="virtualservice", object_name=name)
            results.append(HealthCheck(vs_name=name, expected="enable",
                                        actual="error", hc_status="error"))
    return results
