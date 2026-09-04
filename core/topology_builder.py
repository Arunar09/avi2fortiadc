"""
core/topology_builder.py
Generates hierarchical adjacency lists for V-A-N-R (VDOM-App-Network-Route) graph visualization.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from core.resolver import ConfigurationGraph

def build_vdom_topology(data: dict, manifest: dict) -> list[dict]:
    """
    Builds a D3-friendly hierarchical structure.
    Prioritizes FortiADC-native structure if 'virtual_servers' is present and formatted for FADC.
    """
    # Detect if we are looking at a FortiADC target config or Avi discovery
    is_forti = "virtual_servers" in data and any("vdom" in vs for vs in data["virtual_servers"])
    
    if is_forti:
        return _build_from_forti(data)
    else:
        return _build_from_avi(data, manifest)

def _build_from_forti(config: dict) -> list[dict]:
    """Parses generated FortiADC JSON to show EXACT result on the target."""
    vdoms: dict[str, dict] = {}
    
    # 1. Initialize VDOMs
    for vs in config.get("virtual_servers", []):
        v_name = vs.get("vdom", "root")
        if v_name not in vdoms:
            vdoms[v_name] = {
                "id": f"vdom:{v_name}",
                "name": v_name,
                "type": "vdom",
                "status": "generated",
                "children": [
                    {"id": f"net:{v_name}", "name": "Network Stack", "type": "net_group", "children": []},
                    {"id": f"app:{v_name}", "name": "Application Stack", "type": "app_group", "children": []}
                ]
            }

    # 2. Populate App Stack (VS -> Pool -> RS)
    for vs in config.get("virtual_servers", []):
        v_name = vs.get("vdom", "root")
        app_branch = next(c for c in vdoms[v_name]["children"] if c["type"] == "app_group")
        
        vs_node = {
            "id": f"vs:{vs.get('mkey') or vs.get('name')}",
            "name": vs.get("mkey") or vs.get("name"),
            "type": "virtual_service",
            "status": "ready",
            "children": []
        }
        
        # Link Pools from the central list
        pool_name = vs.get("pool")
        if pool_name:
            pool_data = next((p for p in config.get("real_server_pools", []) if p.get("mkey") == pool_name), None)
            if pool_data:
                p_node = {
                    "id": f"pool:{pool_name}",
                    "name": pool_name,
                    "type": "pool",
                    "status": "active",
                    "children": []
                }
                # Members
                for member in pool_data.get("members", []):
                    p_node["children"].append({
                        "name": member.get("rs_ip") or member.get("ip") or "?",
                        "type": "real_server",
                        "id": f"rs:{member.get('mkey')}"
                    })
                vs_node["children"].append(p_node)
        
        app_branch["children"].append(vs_node)
    
    return list(vdoms.values())

def _build_from_avi(discovery: dict, manifest: dict) -> list[dict]:
    """Parses Avi Discovery to show a simulation of how it WILL land on FortiADC."""
    graph = ConfigurationGraph(discovery)
    vdom_mappings = manifest.get("vdom_mapping", {}).get("mappings", {})
    
    # 1. Group Tenants by VDOM Target
    vdom_to_tenants: dict[str, list[str]] = {}
    
    # Pre-populate with all mapped VDOMs from manifest to ensure 'manual' ones show up
    for avi_tenant, mapping in vdom_mappings.items():
        target_vdom = mapping.get("target") or avi_tenant
        vdom_to_tenants.setdefault(target_vdom, [])
        if avi_tenant and not mapping.get("params", {}).get("manual_baseline"):
            vdom_to_tenants[target_vdom].append(avi_tenant)
    
    # Fallback for unmapped tenants
    if not vdom_to_tenants:
        for t in discovery.get("tenants", []):
            name = t.get("name", "admin")
            vdom_to_tenants[name] = [name]

    topology = []
    
    for vdom_name, avi_tenants in vdom_to_tenants.items():
        vdom_node = {
            "id": f"vdom:{vdom_name}",
            "name": vdom_name,
            "type": "vdom",
            "status": "simulation",
            "conflicts": [],
            "children": []
        }
        
        # 1. Manual Baseline Identification
        if not avi_tenants:
            vdom_node["status"] = "manual_baseline"

        # 2. Advanced Conflict Detection (Names)
        seen_names: dict[str, str] = {}
        if len(avi_tenants) > 1:
            for obj_type, key in [("virtual_services", "name"), ("pools", "name"), ("ssl_certificates", "name")]:
                for obj in discovery.get(obj_type, []):
                    t_ref = obj.get("tenant_ref", "")
                    t_name = t_ref.split("name=")[-1] if "name=" in t_ref else "admin"
                    if t_name in avi_tenants:
                        obj_name = obj.get(key)
                        if obj_name in seen_names and seen_names[obj_name] != t_name:
                            vdom_node["status"] = "conflict"
                            vdom_node["conflicts"].append(f"{obj_type.capitalize()} Collision: {obj_name} (shared by {t_name} and {seen_names[obj_name]})")
                        seen_names[obj_name] = t_name

        # 3. Branches
        net_branch = {"id": f"net:{vdom_name}", "name": "Network Stack", "type": "net_group", "children": []}
        app_branch = {"id": f"app:{vdom_name}", "name": "Application Stack", "type": "app_group", "children": []}
        
        # 4. Populate App Stack (Simulated)
        for vs in discovery.get("virtual_services", []):
            t_ref = vs.get("tenant_ref", "")
            t_name = t_ref.split("name=")[-1] if "name=" in t_ref else "admin"
            if t_name in avi_tenants:
                vs_node = {"id": f"vs:{vs.get('name')}", "name": vs.get('name'), "type": "virtual_service", "status": "simulated", "children": []}
                
                pool_ref = vs.get("pool_ref")
                if pool_ref:
                    p_obj = graph.resolve_ref("Pool", pool_ref)
                    if p_obj:
                        p_node = {"id": f"pool:{p_obj.get('name')}", "name": p_obj.get('name'), "type": "pool", "children": []}
                        for srv in p_obj.get("servers", []):
                            p_node["children"].append({"name": srv.get("ip", {}).get("addr", "?"), "type": "real_server", "id": f"rs:{srv.get('ip', {}).get('addr', '0')}"})
                        vs_node["children"].append(p_node)
                app_branch["children"].append(vs_node)
        
        vdom_node["children"] = [net_branch, app_branch]
        topology.append(vdom_node)
        
    return topology
