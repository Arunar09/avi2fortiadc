"""
collectors/system.py
Collects system-level Avi objects: SE groups, GSLB, cloud config,
IPAM/DNS, alert configs. These reveal the full ecosystem around Avi.
"""
from __future__ import annotations
from core.base import BaseCollector
from core.events import Phase


class ServiceEngineGroupCollector(BaseCollector):
    object_type  = "ServiceEngineGroup"
    display_name = "Service Engine Groups"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for seg in items:
            if not isinstance(seg, dict):
                continue
            self._bus.info(
                Phase.COLLECT,
                f"SE Group '{seg['name']}': "
                f"max_vs_per_se={seg.get('max_vs_per_se', '?')}, "
                f"min_scaleout_per_vs={seg.get('min_scaleout_per_vs', '?')}",
                object_type="ServiceEngineGroup", object_name=seg["name"],
                detail={"note": "SE Groups are Avi-only. FortiADC manages its own "
                                "hardware resources. No migration action needed."},
            )
        return items


class GSLBCollector(BaseCollector):
    object_type  = "GslbService"
    display_name = "GSLB Services"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for svc in items:
            if not isinstance(svc, dict):
                continue
            groups = svc.get("groups", [])
            members = sum(len(g.get("members", [])) for g in groups)
            self._bus.manual(
                Phase.COLLECT,
                f"GSLB service '{svc['name']}' ({members} members across "
                f"{len(groups)} groups) — different model in FortiADC/FortiGSLB",
                object_type="GslbService", object_name=svc["name"],
                detail={
                    "member_count": members,
                    "algorithm": svc.get("algorithm", ""),
                    "action": (
                        "Reconfigure using FortiGSLB or DNS-based GSLB. "
                        "Coordinate with network team for DNS delegation changes."
                    ),
                },
            )
        return items


class GSLBGlobalCollector(BaseCollector):
    object_type = "Gslb"
    display_name = "Global GSLB Configuration"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for g in items:
            if not isinstance(g, dict): continue
            dns_vses = len(g.get("dns_configs", []))
            sites = len(g.get("sites", []))
            self._bus.manual(
                Phase.COLLECT,
                f"Global GSLB '{g.get('name', 'global')}' with {sites} sites and {dns_vses} DNS VSes. "
                f"Reconfigure via FortiGSLB and check the corresponding DNS virtual service.",
                object_type="gslb_global", object_name=g.get("name", "global"),
                detail={"sites": sites, "dns_vses": dns_vses, "action": "Config manual FortiGSLB policy."}
            )
        return items


class GSLBGeoDbCollector(BaseCollector):
    object_type = "GslbGeoDbProfile"
    display_name = "GSLB GeoDB Profiles"

    def _post_process(self, items: list[dict]) -> list[dict]:
        for profile in items:
            if not isinstance(profile, dict): continue
            self._bus.info(
                Phase.COLLECT,
                f"GSLB GeoDB Profile '{profile.get('name')}' - configure equivalent Geo location routing in FortiADC.",
                object_type="gslb_geo_db", object_name=profile.get("name")
            )
        return items


class CloudContextCollector(BaseCollector):
    object_type = "Cloud"
    display_name = "Cloud Context"
    def _post_process(self, items: list[dict]) -> list[dict]:
        for c in items:
            if not isinstance(c, dict): continue
            self._bus.info(Phase.COLLECT, f"Cloud context: {c.get('name', '')} (Type: {c.get('vtype', '')}). Requires manual verification for strict networking translation.", object_type="cloud", object_name=c.get("name"))
        return items


class TenantContextCollector(BaseCollector):
    object_type = "Tenant"
    display_name = "Tenant Context"
    def _post_process(self, items: list[dict]) -> list[dict]:
        for t in items:
            if not isinstance(t, dict): continue
            self._bus.info(Phase.COLLECT, f"Tenant context: {t.get('name', '')}. FortiADC VDOM mapping depends on tenant architecture.", object_type="tenant", object_name=t.get("name"))
        return items


class VrfContextCollector(BaseCollector):
    object_type = "VrfContext"
    display_name = "VRF Context"
    def _post_process(self, items: list[dict]) -> list[dict]:
        for v in items:
            if not isinstance(v, dict): continue
            self._bus.info(Phase.COLLECT, f"VRF Context: {v.get('name', '')}. FortiADC maps VRF to VDOMs and isolated routing tables.", object_type="vrfcontext", object_name=v.get("name"))
        return items


class IpAddrGroupCollector(BaseCollector):
    object_type = "IpAddrGroup"
    display_name = "IP Address Groups"
    def _post_process(self, items: list[dict]) -> list[dict]:
        for i in items:
            if not isinstance(i, dict): continue
            self._bus.info(Phase.COLLECT, f"IP Address Group: {i.get('name', '')}. Migrate as Address Group in FortiADC.", object_type="ipaddrgroup", object_name=i.get("name"))
        return items


class IpamDnsProviderCollector(BaseCollector):
    object_type = "IpamDnsProviderProfile"
    display_name = "IPAM/DNS Provider Profiles"
    def _post_process(self, items: list[dict]) -> list[dict]:
        for i in items:
            if not isinstance(i, dict): continue
            self._bus.info(Phase.COLLECT, f"IPAM/DNS Provider: {i.get('name', '')} ({i.get('type', '')}). Adjust FortiADC DNS settings accordingly.", object_type="ipamdnsproviderprofile", object_name=i.get("name"))
        return items


class NetworkContextCollector(BaseCollector):
    object_type = "Network"
    display_name = "Network Context"
    def _post_process(self, items: list[dict]) -> list[dict]:
        for n in items:
            if not isinstance(n, dict): continue
            subnets = len(n.get("configured_subnets", []))
            self._bus.info(Phase.COLLECT, f"Network Context: {n.get('name', '')} ({subnets} subnets). Verify FortiADC physical interfaces and routing bounds.", object_type="network", object_name=n.get("name"))
        return items


class AlertConfigCollector(BaseCollector):
    object_type  = "AlertConfig"
    display_name = "Alert Configurations"


    def _post_process(self, items: list[dict]) -> list[dict]:
        for alert in items:
            if not isinstance(alert, dict):
                continue
            self._bus.info(
                Phase.COLLECT,
                f"Alert config '{alert['name']}' found — "
                f"reconfigure equivalent alerting in FortiADC after migration",
                object_type="alertconfig", object_name=alert["name"],
            )
        return items


"""
collectors/connections.py
External system connection discovery — answers:
"What else does Avi talk to that must be reconnected after migration?"
"""
from core.base import BaseCollector
from core.events import Phase


class ConnectionDiscoveryCollector(BaseCollector):
    """
    Multi-endpoint collector that maps all external integrations.
    Not a standard Avi object — queries multiple API endpoints.
    """
    object_type  = "connections"
    display_name = "External System Connections"

    def collect(self) -> list[dict]:
        self._bus.info(Phase.COLLECT,
                       "Discovering all external system connections...")
        connections = []
        for fn in [self._infoblox, self._openstack,
                   self._ldap, self._snmp_syslog, self._gslb_dns]:
            try:
                connections.extend(fn())
            except Exception as e:
                self._bus.warn(Phase.COLLECT,
                               f"Connection discovery partial failure: {e}",
                               detail={"discoverer": fn.__name__, "error": str(e)})
        return connections

    def _infoblox(self) -> list[dict]:
        items = self._client.get_all("ipamdnsproviderprofile")
        out = []
        for p in items:
            ptype = p.get("type", "")
            if "INFOBLOX" in ptype.upper() or "EXTERNAL" in ptype.upper():
                cfg = p.get("infoblox_profile", p.get("custom_profile", {}))
                self._bus.info(
                    Phase.COLLECT,
                    f"Infoblox/DNS integration: '{p['name']}' (type={ptype})",
                    object_type="connection:ipam_dns", object_name=p["name"],
                    detail={
                        "connection_type": "infoblox",
                        "profile_type": ptype,
                        "action": (
                            "FortiADC does not natively integrate with Infoblox. "
                            "VIP addresses currently auto-assigned by Avi IPAM must "
                            "be pre-allocated as static IPs. "
                            "Configure FortiADC DNS forwarder to point at Infoblox "
                            "(same approach as OSP Controller C named forwarder)."
                        ),
                    },
                )
            out.append({"type": "ipam_dns", "name": p["name"],
                        "provider_type": ptype})
        return out

    def _openstack(self) -> list[dict]:
        items = self._client.get_all("cloud")
        out = []
        for c in items:
            ctype = c.get("vtype", "")
            if "OPENSTACK" in ctype.upper():
                osc = c.get("openstack_configuration", {})
                self._bus.info(
                    Phase.COLLECT,
                    f"OpenStack cloud integration: '{c['name']}' — "
                    f"FortiADC uses static VIP assignment, not cloud-native",
                    object_type="connection:openstack", object_name=c["name"],
                    detail={
                        "auth_host": osc.get("keystone_host", "[REDACTED]"),
                        "tenant": osc.get("admin_tenant", "[REDACTED]"),
                        "mgmt_network": osc.get("mgmt_network_name", ""),
                        "action": (
                            "1. Identify all VIPs currently assigned by Avi IPAM. "
                            "2. Reserve those IPs as fixed IPs in OpenStack Neutron. "
                            "3. Assign them statically in FortiADC virtual server config. "
                            "4. Update DNS records to point to FortiADC VIPs."
                        ),
                    },
                )
            out.append({"type": "cloud", "name": c["name"], "cloud_type": ctype})
        return out

    def _ldap(self) -> list[dict]:
        items = self._client.get_all("authprofile")
        out = []
        for p in items:
            ptype = p.get("type", "")
            if "LDAP" in ptype.upper():
                self._bus.info(
                    Phase.COLLECT,
                    f"LDAP auth integration: '{p['name']}' — "
                    f"FortiADC supports LDAP, reconfigure with same server details",
                    object_type="connection:ldap", object_name=p["name"],
                    detail={"type": ptype,
                            "base_dn": p.get("ldap", {}).get("base_dn", ""),
                            "action": "Configure LDAP in FortiADC User Remote section."},
                )
            out.append({"type": "auth", "name": p["name"], "auth_type": ptype})
        return out

    def _snmp_syslog(self) -> list[dict]:
        out = []
        try:
            traps = self._client.get_all("snmptrapprofile")
            for t in traps:
                servers = t.get("trap_servers", [])
                self._bus.info(
                    Phase.COLLECT,
                    f"SNMP trap profile '{t['name']}' "
                    f"({len(servers)} server(s)) — configure in FortiADC SNMP settings",
                    object_type="connection:snmp", object_name=t["name"],
                    detail={"server_count": len(servers),
                            "action": "Add same SNMP trap receivers in FortiADC."},
                )
                out.append({"type": "snmp", "name": t["name"],
                            "server_count": len(servers)})
        except Exception:
            pass

        try:
            syslogs = self._client.get_all("syslogappprofile")
            for s in syslogs:
                self._bus.info(
                    Phase.COLLECT,
                    f"Syslog profile '{s['name']}' — "
                    f"configure equivalent in FortiADC log settings",
                    object_type="connection:syslog", object_name=s["name"],
                )
                out.append({"type": "syslog", "name": s["name"]})
        except Exception:
            pass

        return out

    def _gslb_dns(self) -> list[dict]:
        out = []
        try:
            gslb_cfg = self._client.get_all("gslb")
            for g in gslb_cfg:
                dns_vses = g.get("dns_configs", [])
                self._bus.manual(
                    Phase.COLLECT,
                    f"GSLB config '{g.get('name','global')}' with "
                    f"{len(dns_vses)} DNS VS(es) — requires FortiGSLB or DNS redesign",
                    object_type="connection:gslb",
                    detail={"dns_vs_count": len(dns_vses),
                            "action": "Engage network team for GSLB migration plan."},
                )
                out.append({"type": "gslb", "name": g.get("name", "global")})
        except Exception:
            pass
        return out
