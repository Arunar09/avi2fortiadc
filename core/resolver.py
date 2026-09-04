"""
core/resolver.py
Centralized Avi object reference resolution and shared-object tracking.

Avi objects reference each other via URL strings in multiple formats:
  - Query-string:  /api/pool/?tenant=X&name=Y&cloud=Z
  - REST path:     /api/pool/pool-UUID
  - Hash:          https://controller/api/pool/pool-UUID#pool-name

This module provides a single resolver that handles all formats and builds
multi-key indexes (url, uuid, name) for fast lookups.

It also tracks cross-tenant shared object usage so that global objects
(typically in the 'admin' tenant) are correctly attributed to all
consuming tenants.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Optional


def _extract_name_from_ref(ref: str) -> str:
    """Extract the object name from any Avi reference format."""
    if not ref:
        return ""
    if "#" in ref:
        return ref.split("#")[-1]
    m = re.search(r"name=([^&]+)", ref)
    if m:
        return urllib.parse.unquote(m.group(1))
    # Fallback: last path segment (may be UUID)
    return ref.rstrip("/").split("/")[-1]


def _extract_tenant_from_ref(ref: str) -> str:
    """Extract tenant name from an Avi reference string."""
    if not ref:
        return ""

    # 1. Prioritize Tenant objects which use ?name= for the actual tenant name
    if "/api/tenant/" in ref:
        m = re.search(r"name=([^&]+)", ref)
        if m:
            return urllib.parse.unquote(m.group(1))

    # 2. Extract generic tenant parameter
    m = re.search(r"tenant=([^&]+)", ref)
    if m:
        return urllib.parse.unquote(m.group(1))
    
    # 3. Extract from path segment
    m = re.search(r"tenant/([^/#?&]+)", ref)
    if m:
        return urllib.parse.unquote(m.group(1))
    
    return ""


def _extract_query_params(ref: str) -> dict[str, str]:
    """Extract all key=value pairs from a query-string reference."""
    params: dict[str, str] = {}
    if "?" not in ref:
        return params
    qs = ref.split("?", 1)[1]
    for part in qs.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = urllib.parse.unquote(v)
    return params


class ConfigurationGraph:
    """Indexes all objects in a raw Avi snapshot for fast, format-agnostic lookups.

    Supports three lookup dimensions per object type:
      - by URL   (exact `url` field match)
      - by UUID  (exact `uuid` field match)
      - by name  (exact `name` field match)

    Usage::

        graph = ConfigurationGraph(raw_snapshot)
        vsvip_obj = graph.resolve_ref("VsVip", "/api/vsvip/?tenant=X&name=Y")
        ip = graph.get_vip_for_vs(vs_object)
    """

    def __init__(self, raw_data: dict):
        # Dynamically flatten the snapshot to handle inconsistent nesting 
        # (e.g. AviConfig vs Root siblings like GslbService)
        self._raw = self._flatten_snapshot(raw_data)
        self._indexes: dict[str, dict] = {}
        self._shared_usage: dict[tuple[str, str], set[str]] = {} # (rk, name) -> set(tenants)
        
        self._build_indexes()
        self._build_shared_usage()

    def _flatten_snapshot(self, data: dict) -> dict:
        """
        Generically merges nested configuration envelopes (AviConfig, raw_config) 
        into the root key-space to ensure all siblings are preserved.
        """
        if not isinstance(data, dict):
            return {}
        
        result = data.copy()
        # Known envelopes that might contain the bulk of the configuration
        ENVELOPES = ["AviConfig", "raw_config", "config"]
        
        for env in ENVELOPES:
            if env in result and isinstance(result[env], dict):
                inner = result.pop(env)
                # Merge inner keys into result if they don't collide or if we prioritize inner
                for k, v in inner.items():
                    if k not in result or (isinstance(v, list) and not result[k]):
                        result[k] = v
        return result

    # ── Index Construction ────────────────────────────────────────────────

    def _build_indexes(self) -> None:
        """Build multi-key indexes for every list-type top-level key."""
        for top_key, items in self._raw.items():
            if not isinstance(items, list):
                continue
            by_url: dict[str, dict] = {}
            by_uuid: dict[str, dict] = {}
            by_name: dict[str, list[dict]] = {}

            for item in items:
                if not isinstance(item, dict):
                    continue
                url = item.get("url", "")
                uuid = item.get("uuid", "")
                name = item.get("name", "")

                if url:
                    by_url[url] = item
                if uuid:
                    by_uuid[uuid] = item
                if name:
                    by_name.setdefault(name, []).append(item)

            self._indexes[top_key] = {
                "by_url": by_url,
                "by_uuid": by_uuid,
                "by_name": by_name,
            }

    def _build_shared_usage(self) -> None:
        """Trace the full object hierarchy to identify shared objects.
        
        Hierarchy:
          GslbService -> VirtualService (members)
          VirtualService -> PoolGroup -> Pool -> Pool Members
          VirtualService -> Profiles/Policies (SSL, WAF, etc.)
        """
        # 1. Trace GslbServices (Global objects that point to Tenant VSes)
        gslb_items = self._raw.get("GslbService") or []
        if isinstance(gslb_items, list):
            for gslb in gslb_items:
                gslb_tenant = self.get_tenant(gslb)
                
                # 1. Standard Reverse Attribution for GSLB Members
                for group in gslb.get("groups") or []:
                    if not isinstance(group, dict): continue
                    for member in group.get("members") or []:
                        vs_ref = member.get("vs_ref")
                        if vs_ref:
                            target_vs = self.resolve_ref("VirtualService", vs_ref)
                            if target_vs:
                                vs_tenant = self.get_tenant(target_vs)
                                if gslb_name:
                                    # Mark the GSLB service itself as relevant to the VS's tenant
                                    self._trace_shared_ref_by_name("GslbService", gslb_name, vs_tenant)
                
                # 2. Standard Forward attribution (GSLB -> Health Monitors)
                for hm_ref in gslb.get("health_monitor_refs") or []:
                    self._trace_shared_ref("HealthMonitor", hm_ref, gslb_tenant)

    def get_dependencies(self, rk: str, item: dict) -> dict[str, list[str]]:
        """Return a map of all objects referenced by this item: { type: [names] }."""
        deps = {}
        
        # 1. GSLB specific memberships
        if rk == "GslbService":
            for group in item.get("groups") or []:
                if not isinstance(group, dict): continue
                for member in group.get("members") or []:
                    if not isinstance(member, dict): continue
                    vs_ref = member.get("vs_ref")
                    vs_uuid = member.get("vs_uuid")
                    if vs_ref:
                        deps.setdefault("VirtualService", []).append(self.ref_name(vs_ref))
                    elif vs_uuid:
                        target = self.get_by_uuid("VirtualService", vs_uuid)
                        if target: deps.setdefault("VirtualService", []).append(target.get("name", "unnamed"))
            
            for hm_ref in item.get("health_monitor_refs") or []:
                deps.setdefault("HealthMonitor", []).append(self.ref_name(hm_ref))

        # 2. SLB (VirtualService) core dependencies
        if rk == "VirtualService":
            single_refs = [
                ("Pool", "pool_ref"), ("PoolGroup", "pool_group_ref"),
                ("SSLProfile", "ssl_profile_ref"), ("ApplicationProfile", "application_profile_ref"),
                ("NetworkProfile", "network_profile_ref"), ("WafPolicy", "waf_policy_ref"),
                ("VsVip", "vsvip_ref"), ("HttpPolicySet", "http_policy_set_ref"),
            ]
            for obj_type, field in single_refs:
                ref = item.get(field)
                if ref: deps.setdefault(obj_type, []).append(self.ref_name(ref))
            
            for ref in item.get("ssl_key_and_certificate_refs") or []:
                deps.setdefault("SSLKeyAndCertificate", []).append(self.ref_name(ref))
            
            for ds_entry in item.get("vs_datascripts") or []:
                if isinstance(ds_entry, dict) and ds_entry.get("vs_datascript_set_ref"):
                    deps.setdefault("VSDataScriptSet", []).append(self.ref_name(ds_entry["vs_datascript_set_ref"]))
        
        # 3. PoolGroup dependencies
        if rk == "PoolGroup":
            for member in item.get("members") or []:
                if isinstance(member, dict) and member.get("pool_ref"):
                    deps.setdefault("Pool", []).append(self.ref_name(member["pool_ref"]))

        # 4. Pool dependencies
        if rk == "Pool":
            for hm_ref in item.get("health_monitor_refs") or []:
                deps.setdefault("HealthMonitor", []).append(self.ref_name(hm_ref))
            # [FIX] Trace persistence profiles
            p_ref = item.get("application_persistence_profile_ref")
            if p_ref:
                deps.setdefault("ApplicationPersistenceProfile", []).append(self.ref_name(p_ref))

        # Deduplicate and sort
        for t in deps:
            deps[t] = sorted(list(set(deps[t])))
        return deps

    def _build_shared_usage(self) -> None:
        """Trace the full object hierarchy to identify shared objects (in 'admin' tenant)."""
        # 1. Trace VirtualServices (SLB)
        vs_items = self._raw.get("VirtualService") or []
        if isinstance(vs_items, list):
            for vs in vs_items:
                if not isinstance(vs, dict): continue
                vs_tenant = self.get_tenant(vs)
                if vs_tenant.lower() == "admin": continue
                
                # Trace all direct and recursive dependencies
                deps = self.get_dependencies("VirtualService", vs)
                for obj_type, obj_names in deps.items():
                    for name in obj_names:
                        self._trace_shared_ref_by_name(obj_type, name, vs_tenant)

        # 2. Trace GslbServices (Global objects that point to Tenant VSes)
        gslb_items = self._raw.get("GslbService") or []
        if isinstance(gslb_items, list):
            for gslb in gslb_items:
                gslb_tenant = self.get_tenant(gslb)
                gslb_name = gslb.get("name")
                
                for group in gslb.get("groups") or []:
                    if not isinstance(group, dict): continue
                    for member in group.get("members") or []:
                        vs_ref = member.get("vs_ref")
                        if vs_ref:
                            target_vs = self.resolve_ref("VirtualService", vs_ref)
                            if target_vs:
                                vs_tenant = self.get_tenant(target_vs)
                                if gslb_name and vs_tenant.lower() != "admin":
                                    # Mark GSLB as shared with the VS's tenant
                                    self._trace_shared_ref_by_name("GslbService", gslb_name, vs_tenant)

        # 3. Trace PoolGroups -> Pools
        pg_items = self._raw.get("PoolGroup") or []
        if isinstance(pg_items, list):
            for pg in pg_items:
                if not isinstance(pg, dict): continue
                pg_tenant = self.get_tenant(pg)
                for member in pg.get("members") or []:
                    if not isinstance(member, dict): continue
                    pool_ref = member.get("pool_ref")
                    if pool_ref:
                        self._trace_shared_ref("Pool", pool_ref, pg_tenant)

    def _trace_shared_ref_by_name(self, obj_type: str, name: str, consumer_tenant: str) -> None:
        """Record usage if the object exists in 'admin' but is consumed by another tenant."""
        target = self.get_by_name(obj_type, name)
        if not target: return
        
        target_tenant = self.get_tenant(target)
        if target_tenant.lower() == "admin" and consumer_tenant.lower() != "admin":
            self._shared_usage.setdefault((obj_type, name), set()).add(consumer_tenant.upper())

    def _trace_shared_ref(self, obj_type: str, ref: str, consumer_tenant: str) -> None:
        """Record usage if the referenced object is in 'admin' but consumed by another tenant."""
        target = self.resolve_ref(obj_type, ref)
        if not target: return
        
        target_tenant = self.get_tenant(target)
        # If the object is in 'admin' but the consumer is someone else, it's shared.
        if target_tenant.lower() == "admin" and consumer_tenant.lower() != "admin":
            obj_name = target.get("name", "")
            if obj_name:
                self._shared_usage.setdefault((obj_type, obj_name), set()).add(consumer_tenant.upper())

    # ── Public API ────────────────────────────────────────────────────────

    def resolve_ref(self, object_type: str, ref: str) -> Optional[dict]:
        """Resolve an Avi reference string to the actual object dict.

        Tries multiple strategies in order:
        1. Exact URL match
        2. Name and Tenant extracted from query-string -> by_name lookup
        3. UUID extracted from path -> by_uuid lookup
        """
        if not ref or object_type not in self._indexes:
            return None

        idx = self._indexes[object_type]

        # Strategy 1: exact URL match
        obj = idx["by_url"].get(ref)
        if obj:
            return obj

        # Strategy 2: extract name & tenant from query-string (?name=X&tenant=Y)
        name = ""
        tenant = ""
        if "name=" in ref:
            m_name = re.search(r"name=([^&]+)", ref)
            if m_name:
                import urllib.parse
                name = urllib.parse.unquote(m_name.group(1))
        if "tenant=" in ref:
            m_tenant = re.search(r"tenant=([^&]+)", ref)
            if m_tenant:
                tenant = urllib.parse.unquote(m_tenant.group(1)).upper()

        if name:
            candidates = idx["by_name"].get(name, [])
            if candidates:
                if tenant:
                    # Deterministic tenant-match
                    for cand in candidates:
                        if self.get_tenant(cand).upper() == tenant:
                            return cand
                # Fallback: first one found
                return candidates[0]

        # Strategy 3: extract from hash (#name)
        if "#" in ref:
            hash_name = ref.split("#")[-1]
            candidates = idx["by_name"].get(hash_name, [])
            if candidates:
                return candidates[0]

        # Strategy 4: last path segment as UUID
        segment = ref.rstrip("/").split("/")[-1]
        if segment and segment != ref:
            obj = idx["by_uuid"].get(segment)
            if obj:
                return obj

        return None

    def get_by_name(self, object_type: str, name: str, tenant: str = "") -> Optional[dict]:
        """Direct lookup by name within an object type, with optional tenant disambiguation."""
        if object_type not in self._indexes:
            return None
        candidates = self._indexes[object_type]["by_name"].get(name, [])
        if not candidates:
            return None
            
        if tenant:
            t_upper = tenant.upper()
            for cand in candidates:
                if self.get_tenant(cand).upper() == t_upper:
                    return cand
        
        return candidates[0]

    def get_by_uuid(self, object_type: str, uuid: str) -> Optional[dict]:
        """Direct lookup by UUID within an object type."""
        if object_type not in self._indexes:
            return None
        return self._indexes[object_type]["by_uuid"].get(uuid)

    def get_all(self, object_type: str) -> list[dict]:
        """Return all objects of a given type from the raw snapshot."""
        items = self._raw.get(object_type, [])
        return items if isinstance(items, list) else []

    def get_tenant(self, obj: dict) -> str:
        """Extract the canonical tenant name from an object's tenant_ref."""
        t_ref = obj.get("tenant_ref", "")
        if not t_ref:
            return "admin"
        tenant = _extract_tenant_from_ref(t_ref)
        return tenant if tenant else "admin"

    def get_vip_for_vs(self, vs: dict) -> str:
        """Resolve the Virtual IP address for a VirtualService object.

        Resolution order:
        1. Inline `vip` list on the VS itself
        2. Follow `vsvip_ref` to VsVip object -> traverse vip[].ip_address.addr
        3. Fallback: "no-vip"
        """
        # 1. Inline VIP
        vips = vs.get("vip", [])
        if isinstance(vips, list) and vips:
            first = vips[0]
            if isinstance(first, dict):
                addr = first.get("ip_address", {})
                if isinstance(addr, dict):
                    ip = addr.get("addr", "")
                    if ip:
                        return ip

        # 2. Follow vsvip_ref
        vsvip_ref = vs.get("vsvip_ref", "")
        if vsvip_ref:
            vsvip_obj = self.resolve_ref("VsVip", vsvip_ref)
            if vsvip_obj:
                vsvip_vips = vsvip_obj.get("vip", [])
                if isinstance(vsvip_vips, list) and vsvip_vips:
                    first_vip = vsvip_vips[0]
                    if isinstance(first_vip, dict):
                        ip_addr = first_vip.get("ip_address", {})
                        if isinstance(ip_addr, dict):
                            return ip_addr.get("addr", "no-addr")

        return "no-vip"

    def get_all_vips_for_vs(self, vs: dict) -> list[str]:
        """Return all VIP addresses for a VS (some VSes have multiple VIPs)."""
        addrs: list[str] = []

        # Inline
        for vip in vs.get("vip", []):
            if isinstance(vip, dict):
                ip = vip.get("ip_address", {})
                if isinstance(ip, dict) and ip.get("addr"):
                    addrs.append(ip["addr"])

        # VsVip ref
        if not addrs:
            vsvip_ref = vs.get("vsvip_ref", "")
            if vsvip_ref:
                vsvip_obj = self.resolve_ref("VsVip", vsvip_ref)
                if vsvip_obj:
                    for vip in vsvip_obj.get("vip", []):
                        if isinstance(vip, dict):
                            ip = vip.get("ip_address", {})
                            if isinstance(ip, dict) and ip.get("addr"):
                                addrs.append(ip["addr"])

        return addrs

    def get_shared_usage(self) -> dict[tuple[str, str], set[str]]:
        """Return the shared-usage map: {(obj_type, obj_name): {tenant_names}}."""
        return dict(self._shared_usage)

    def is_shared_object(self, object_type: str, obj_name: str) -> bool:
        """Check if an object is shared across multiple tenants."""
        return (object_type, obj_name) in self._shared_usage

    def get_shared_tenants(self, object_type: str, obj_name: str) -> set[str]:
        """Return the set of tenant names that reference this shared object."""
        return self._shared_usage.get((object_type, obj_name), set())

    def ref_name(self, ref: str) -> str:
        """Extract object name from any reference format. Convenience alias."""
        return _extract_name_from_ref(ref)

    def ref_tenant(self, ref: str) -> str:
        """Extract tenant name from any reference format. Convenience alias."""
        return _extract_tenant_from_ref(ref)
