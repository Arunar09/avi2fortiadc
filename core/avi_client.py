"""
core/avi_client.py
Avi Networks REST API client.
Handles: token auth, session cookies, pagination (200/page),
         multi-tenant, API version negotiation, retry on 401.
Read-only by design — this tool never writes to Avi.
"""
from __future__ import annotations

import time
import logging
from typing import Any, Generator, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


class AviAuthError(Exception): pass
class AviAPIError(Exception):
    def __init__(self, status: int, url: str, body: str):
        self.status = status
        self.url    = url
        super().__init__(f"HTTP {status} on {url}: {body[:200]}")


class AviClient:
    """
    Read-only Avi API client.
    Authentication: username/password → session cookie + CSRF token.
    All GET requests include X-Avi-Tenant header for tenant scoping.
    Pagination is handled automatically — callers receive full lists.
    """

    DEFAULT_PAGE_SIZE = 200   # Avi API maximum
    MAX_RETRIES       = 3
    RETRY_BACKOFF     = 0.5   # seconds

    def __init__(
        self,
        controller:  str,
        username:    str,
        password:    str,
        tenant:      str = "admin",
        api_version: str = "22.1.5",
        verify_ssl:  bool = True,
        timeout:     int  = 30,
    ):
        self.controller  = controller.rstrip("/")
        self.tenant      = tenant
        self.api_version = api_version
        self._timeout    = timeout
        self._session    = self._make_session(verify_ssl)
        self._csrf_token: Optional[str] = None
        self._username = username
        self._password = password
        self._authenticate(username, password)

    # ── Session setup ────────────────────────────────────────────────────────

    def _make_session(self, verify_ssl: bool) -> requests.Session:
        session = requests.Session()
        session.verify = verify_ssl
        retry = Retry(
            total=self.MAX_RETRIES,
            backoff_factor=self.RETRY_BACKOFF,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _common_headers(self) -> dict:
        h = {
            "X-Avi-Version":  self.api_version,
            "X-Avi-Tenant":   self.tenant,
            "Accept":         "application/json",
            "Content-Type":   "application/json",
        }
        if self._csrf_token:
            h["X-CSRFToken"] = self._csrf_token
        return h

    # ── Authentication ───────────────────────────────────────────────────────

    def _authenticate(self, username: str, password: str) -> None:
        url = f"{self.controller}/login"
        try:
            resp = self._session.post(
                url,
                json={"username": username, "password": password},
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise AviAuthError(f"Cannot connect to Avi controller at {self.controller}: {e}")

        if resp.status_code == 401:
            raise AviAuthError("Avi authentication failed — check username/password")
        if not resp.ok:
            raise AviAuthError(f"Avi login returned HTTP {resp.status_code}: {resp.text[:200]}")

        # Extract CSRF token from cookie
        self._csrf_token = resp.cookies.get("csrftoken")
        # Session cookie is stored automatically by requests.Session

    def switch_tenant(self, tenant: str) -> None:
        """Switch tenant context for subsequent requests."""
        self.tenant = tenant

    # ── Core GET ─────────────────────────────────────────────────────────────

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        """
        Single GET request. Returns parsed JSON.
        Raises AviAPIError on non-2xx.
        """
        url = f"{self.controller}/api/{path.lstrip('/')}"
        params = params or {}

        resp = self._session.get(
            url,
            params=params,
            headers=self._common_headers(),
            timeout=self._timeout,
        )

        # Re-authenticate on 401 and retry once
        if resp.status_code == 401:
            log.warning("Got 401 on %s — re-authenticating", path)
            self._authenticate(self._username, self._password)
            resp = self._session.get(
                url, params=params,
                headers=self._common_headers(),
                timeout=self._timeout,
            )

        if not resp.ok:
            raise AviAPIError(resp.status_code, url, resp.text)

        return resp.json()

    def get_all(self, object_type: str,
                fields: Optional[list[str]] = None,
                filters: Optional[dict] = None) -> list[dict]:
        """
        Fetch all objects of a given type, handling pagination automatically.
        Returns the full list — may be large.

        Args:
            object_type: Avi API object name e.g. "virtualservice"
            fields:      Optional list of fields to include (reduces payload)
            filters:     Optional filter parameters e.g. {"enabled": "true"}
        """
        results: list[dict] = []
        page = 1

        while True:
            params: dict = {
                "page_size": self.DEFAULT_PAGE_SIZE,
                "page":      page,
            }
            if fields:
                params["fields"] = ",".join(fields)
            if filters:
                params.update(filters)

            data = self.get(object_type, params=params)
            batch = data.get("results", [])
            results.extend(batch)

            count    = data.get("count", len(batch))
            received = len(results)
            log.debug("%-25s page %d: got %d / %d total",
                      object_type, page, received, count)

            if received >= count or len(batch) == 0:
                break
            page += 1

        return results

    def get_object(self, object_type: str, uuid: str) -> dict:
        """Fetch a single object by UUID."""
        return self.get(f"{object_type}/{uuid}")

    def get_runtime(self, object_type: str, uuid: str) -> Optional[dict]:
        """
        Fetch runtime state for an object.
        Returns None if runtime endpoint not available for this type.
        """
        try:
            return self.get(f"{object_type}/{uuid}/runtime")
        except AviAPIError as e:
            if e.status == 404:
                return None
            raise

    def list_tenants(self) -> list[dict]:
        """List all tenants. Requires admin context."""
        return self.get_all("tenant")

    def ping(self) -> tuple[bool, str]:
        """
        Test connectivity and auth. Returns (ok, version_string).
        """
        try:
            data = self.get("cluster/version")
            version = data.get("Version", data.get("version", "unknown"))
            return True, version
        except (AviAPIError, AviAuthError, Exception) as e:
            return False, str(e)

    def close(self) -> None:
        try:
            self._session.get(
                f"{self.controller}/logout",
                headers=self._common_headers(),
                timeout=5,
            )
        except Exception:
            pass
        self._session.close()
