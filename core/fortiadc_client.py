"""
core/fortiadc_client.py
FortiADC REST API client — write operations only used in deploy phase.
Dry-run mode prints what WOULD be sent without touching the device.
"""
from __future__ import annotations
import json, logging
from typing import Any, Optional
import requests

log = logging.getLogger(__name__)


class FortiADCClient:
    def __init__(self, host: str, username: str, password: str,
                 vdom: str = "root", verify_ssl: bool = True,
                 timeout: int = 30, dry_run: bool = True):
        self.host     = host.rstrip("/")
        self.vdom     = vdom
        self._timeout = timeout
        self.dry_run  = dry_run
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._token: Optional[str] = None
        if not dry_run:
            self._authenticate(username, password)

    def _authenticate(self, username: str, password: str) -> None:
        resp = self._session.post(
            f"{self.host}/api/user/login",
            json={"username": username, "password": password},
            timeout=self._timeout,
        )
        if not resp.ok:
            raise RuntimeError(f"FortiADC auth failed: HTTP {resp.status_code}")
        self._token = resp.json().get("token")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _params(self, override_vdom: Optional[str] = None) -> dict:
        return {"vdom": override_vdom or self.vdom}

    def get(self, path: str, vdom: Optional[str] = None) -> dict:
        url = f"{self.host}/api/{path.lstrip('/')}"
        resp = self._session.get(url, headers=self._headers(),
                                  params=self._params(vdom), timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def create(self, path: str, payload: dict, vdom: Optional[str] = None) -> dict:
        url = f"{self.host}/api/{path.lstrip('/')}"
        if self.dry_run:
            print(f"  [DRY-RUN] POST {url} (vdom: {vdom or self.vdom})")
            print(f"  {json.dumps(payload, indent=4)}")
            return {"dry_run": True, "payload": payload}
        resp = self._session.post(url, json=payload, headers=self._headers(),
                                   params=self._params(vdom), timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def preflight_check(self, path: str, payload: dict, vdom: Optional[str] = None) -> dict:
        """Sends a POST request with ?dry-run=1 to verify syntax without applying changes."""
        url = f"{self.host}/api/{path.lstrip('/')}"
        # Merge VDOM with dry-run flag
        params = self._params(vdom).copy()
        params["dry-run"] = "1"
        
        if self.dry_run:
            return {"success": True, "message": f"[Local Simulation] Syntax check passed (vdom: {vdom or self.vdom})"}
            
        try:
            resp = self._session.post(url, json=payload, headers=self._headers(),
                                       params=params, timeout=self._timeout)
            return resp.json()
        except Exception as e:
            return {"success": False, "message": str(e)}

    def update(self, path: str, name: str, payload: dict, vdom: Optional[str] = None) -> dict:
        url = f"{self.host}/api/{path.lstrip('/')}/{name}"
        if self.dry_run:
            print(f"  [DRY-RUN] PUT {url} (vdom: {vdom or self.vdom})")
            print(f"  {json.dumps(payload, indent=4)}")
            return {"dry_run": True}
        resp = self._session.put(url, json=payload, headers=self._headers(),
                                  params=self._params(vdom), timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def exists(self, path: str, name: str, vdom: Optional[str] = None) -> bool:
        """Return False only for a confirmed 404; propagate all other failures.

        Treating authentication, authorization, timeout, or transport failures
        as "not found" could turn a safe update into an unintended create.
        """
        try:
            self.get(f"{path}/{name}", vdom=vdom)
            return True
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return False
            raise

    def close(self) -> None:
        self._session.close()
