"""
deployers/fortiadc_deployer.py
Ordered deployment of transformed config to FortiADC.
Respects dependency order: certs → health checks → pools → virtual servers.
Dry-run by default — requires --execute flag to make real changes.
Every action is logged before execution.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from core.fortiadc_client import FortiADCClient
from core.events import EventBus, Phase


@dataclass
class DeployResult:
    name:       str
    path:       str
    success:    bool
    dry_run:    bool
    error:      str = ""
    skipped:    bool = False
    skip_reason: str = ""


class FortiADCDeployer:
    """
    Deploys objects to FortiADC in correct dependency order.
    Always dry-run unless client.dry_run=False.
    """

    # Deployment order — dependencies must come first
    # real_servers must come AFTER real_server_pools (pool must exist before members)
    DEPLOY_ORDER = [
        "ssl_certificates",
        "ssl_profiles",
        "health_checks",
        "real_server_pools",
        "real_servers",
        "pool_members",
        "persistence_profiles",
        "virtual_servers",
    ]

    def __init__(self, client: FortiADCClient, bus: EventBus):
        self._client = client
        self._bus    = bus

    def deploy_all(self, fortiadc_config: dict) -> list[DeployResult]:
        results: list[DeployResult] = []
        dry_run = self._client.dry_run

        mode = "DRY-RUN" if dry_run else "EXECUTE"
        self._bus.info(Phase.DEPLOY,
                       f"Starting deployment — mode: {mode}")

        for section in self.DEPLOY_ORDER:
            objects = fortiadc_config.get(section, [])
            if not objects:
                continue
            self._bus.info(Phase.DEPLOY,
                           f"Deploying {len(objects)} {section}...")
            for obj in objects:
                result = self._deploy_one(obj, section, dry_run)
                results.append(result)
                if result.success and not dry_run:
                    results.extend(self.deploy_sub_objects(obj, dry_run))

        passed  = sum(1 for r in results if r.success and not r.skipped)
        skipped = sum(1 for r in results if r.skipped)
        failed  = sum(1 for r in results if not r.success)

        self._bus.info(
            Phase.DEPLOY,
            f"Deployment complete — {passed} deployed, "
            f"{skipped} skipped (dry-run), {failed} failed",
        )
        return results

    def preflight_all(self, fortiadc_config: dict) -> list[DeployResult]:
        """Runs pre-flight syntax checks for all objects in the config."""
        results: list[DeployResult] = []
        self._bus.info(Phase.VALIDATE, "Starting pre-flight API syntax validation...")

        for section in self.DEPLOY_ORDER:
            objects = fortiadc_config.get(section, [])
            if not objects:
                continue
                
            self._bus.info(Phase.VALIDATE, f"Pre-flight: Checking {len(objects)} items in {section}...")
            for obj in objects:
                name = obj.get("name", "?")
                path = obj.get("fortiadc_path", "")
                payload = obj.get("payload", {})
                vdom = obj.get("vdom")
                
                if not path or not payload:
                    continue
                    
                try:
                    resp = self._client.preflight_check(path, payload, vdom=vdom)
                    # Handle different success formats (FortiADC response vs local simulation)
                    success = resp.get("success") is True or resp.get("code") == 200
                    error_msg = resp.get("message") or resp.get("error_msg") or ""
                    
                    results.append(DeployResult(
                        name=name, path=path, success=success, 
                        dry_run=self._client.dry_run, error=error_msg
                    ))
                    
                    if not success:
                        self._bus.error(Phase.VALIDATE, f"Pre-flight FAILED for {name} (vdom: {vdom}): {error_msg}")
                    else:
                        self._bus.info(Phase.VALIDATE, f"Pre-flight PASSED for {name} (vdom: {vdom})")
                        
                except Exception as e:
                    results.append(DeployResult(name=name, path=path, success=False, dry_run=self._client.dry_run, error=str(e)))
                    self._bus.error(Phase.VALIDATE, f"Pre-flight error for {name}: {e}")
                    
        return results

    def _deploy_one(self, obj: dict, section: str,
                    dry_run: bool) -> DeployResult:
        name    = obj.get("name", "?")
        path    = obj.get("fortiadc_path", "")
        payload = obj.get("payload", {})
        vdom    = obj.get("vdom")

        if not path or not payload:
            return DeployResult(name=name, path=path, success=False,
                                dry_run=dry_run,
                                error="Missing fortiadc_path or payload")

        # Log before every action — audit trail
        self._bus.info(
            Phase.DEPLOY,
            f"{'[DRY-RUN] ' if dry_run else ''}Deploy {section}/{name} → {path} (vdom: {vdom})",
            object_name=name,
            detail={"path": path, "payload_keys": list(payload.keys()),
                    "dry_run": dry_run, "vdom": vdom},
        )

        if dry_run:
            return DeployResult(name=name, path=path, success=True,
                                dry_run=True, skipped=True,
                                skip_reason="dry-run mode")

        # Check if already exists — update vs create
        try:
            if self._client.exists(path, name, vdom=vdom):
                self._bus.warn(
                    Phase.DEPLOY,
                    f"Object '{name}' already exists in vdom '{vdom}' — updating",
                    object_name=name,
                )
                self._client.update(path, name, payload, vdom=vdom)
            else:
                self._client.create(path, payload, vdom=vdom)

            self._bus.info(Phase.DEPLOY,
                           f"Deployed {section}/{name} to vdom '{vdom}' ✓",
                           object_name=name)
            return DeployResult(name=name, path=path, success=True, dry_run=False)

        except Exception as e:
            self._bus.error(
                Phase.DEPLOY,
                f"Failed to deploy {section}/{name}: {e}",
                object_name=name,
                detail={"error": str(e),
                        "path": path,
                        "payload": payload},
            )
            return DeployResult(name=name, path=path, success=False,
                                dry_run=False, error=str(e))

    def deploy_sub_objects(self, obj: dict, dry_run: bool) -> list[DeployResult]:
        """Deploy sub-objects (e.g. real servers within a pool)."""
        results = []
        for sub in obj.get("sub_objects", []):
            results.append(self._deploy_one(sub, "sub_object", dry_run))
        return results
