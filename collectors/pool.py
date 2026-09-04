"""collectors/pool.py — Pool + member health collection."""
from core.base import BaseCollector
from core.events import Phase


class PoolCollector(BaseCollector):
    object_type    = "Pool"
    display_name   = "Pools"
    collect_runtime = True   # get per-member health state

    def _post_process(self, items):
        for pool in items:
            if not isinstance(pool, dict):
                continue
            members = pool.get("servers", [])
            pool["_member_count"] = len(members)
            pool["_member_ips"] = [
                s.get("ip", {}).get("addr", "")
                for s in members
            ]
            # Runtime: member health
            rt = pool.get("_runtime", {})
            servers_rt = rt.get("servers", [])
            pool["_members_up"]   = sum(1 for s in servers_rt
                                        if s.get("oper_status", {}).get("state") == "OPER_UP")
            pool["_members_down"] = sum(1 for s in servers_rt
                                        if s.get("oper_status", {}).get("state") != "OPER_UP")
            # LB algorithm mapping flag
            algo = pool.get("lb_algorithm", "")
            if algo not in ("LB_ALGORITHM_ROUND_ROBIN", "LB_ALGORITHM_LEAST_CONNECTIONS",
                            "LB_ALGORITHM_FEWEST_SERVERS"):
                self._bus.warn(
                    Phase.COLLECT,
                    f"Pool '{pool['name']}' uses LB algorithm '{algo}' "
                    f"which may not have a direct FortiADC equivalent",
                    object_type="Pool", object_name=pool["name"],
                    detail={"avi_algorithm": algo,
                            "fortiadc_options": ["Round Robin", "Least Connection",
                                                  "Weighted Round Robin", "IP Hash"]},
                )
        return items


"""collectors/health_monitor.py"""
from core.base import BaseCollector
from core.events import Phase

# Monitor types that cannot be auto-migrated
_UNSUPPORTED_HM_TYPES = {"HEALTH_MONITOR_EXTERNAL", "HEALTH_MONITOR_SIP",
                          "HEALTH_MONITOR_RADIUS", "HEALTH_MONITOR_SMTP"}


class HealthMonitorCollector(BaseCollector):
    object_type  = "HealthMonitor"
    display_name = "Health Monitors"

    def _post_process(self, items):
        for hm in items:
            if not isinstance(hm, dict):
                continue
            hm_type = hm.get("type", "")
            if hm_type in _UNSUPPORTED_HM_TYPES:
                self._bus.manual(
                    Phase.COLLECT,
                    f"Health monitor '{hm['name']}' type '{hm_type}' "
                    f"has no FortiADC equivalent — manual replacement required",
                    object_type="HealthMonitor",
                    object_name=hm["name"],
                    detail={
                        "type": hm_type,
                        "suggestion": (
                            "Replace with an HTTP health check against a /health "
                            "endpoint on the application, or a TCP port check."
                        ),
                    },
                )
        return items


"""collectors/ssl.py"""
from core.base import BaseCollector
from core.events import Phase


class SSLCertificateCollector(BaseCollector):
    object_type  = "SSLKeyAndCertificate"
    display_name = "SSL Certificates"

    def _post_process(self, items):
        for cert in items:
            if not isinstance(cert, dict):
                continue
            # Check if key is exportable
            key_params = cert.get("key_params", {})
            hsm_ref = cert.get("hardwaresecuritymodulegroup_ref", "")

            if hsm_ref:
                cert["_exportable"] = False
                self._bus.critical(
                    Phase.COLLECT,
                    f"Certificate '{cert['name']}' is HSM-backed — "
                    f"private key CANNOT be exported from Avi",
                    object_type="SSLKeyAndCertificate",
                    object_name=cert["name"],
                    detail={
                        "hsm_ref": hsm_ref,
                        "action": "Request a new certificate from your CA "
                                  "(Wintel team) and import directly into FortiADC. "
                                  "Identify all Virtual Services using this certificate "
                                  "before migration.",
                    },
                )
            else:
                cert["_exportable"] = True

            # Certificate expiry check
            expiry = cert.get("certificate", {}).get("not_after", "")
            cert["_expiry"] = expiry
        return items


"""collectors/connections.py
Discovers all external system connections referenced in Avi config.
This answers: "what else does Avi talk to that we need to reconnect?"
"""
from core.base import BaseCollector
from core.events import Phase


class ConnectionDiscoveryCollector(BaseCollector):
    """
    Not a standard Avi object collector — queries multiple endpoints
    to build a complete map of external system integrations.
    """
    object_type  = "connections"
    display_name = "External System Connections"

    def collect(self) -> list[dict]:
        self._bus.info(Phase.COLLECT, "Discovering external system connections...")
        connections = []
        discoveries = [
            self._collect_ipam_dns,
            self._collect_cloud_integration,
            self._collect_auth_profiles,
            self._collect_alert_targets,
            self._collect_gslb,
        ]
        for fn in discoveries:
            try:
                found = fn()
                connections.extend(found)
            except Exception as e:
                self._bus.warn(Phase.COLLECT,
                               f"Connection discovery partial failure: {e}",
                               detail={"error": str(e)})
        return connections

    def _collect_ipam_dns(self) -> list[dict]:
        items = self._client.get_all("ipamdnsproviderprofile")
        connections = []
        for p in items:
            ptype = p.get("type", "")
            if "INFOBLOX" in ptype.upper():
                self._bus.info(Phase.COLLECT,
                               f"Found Infoblox DNS integration: '{p['name']}'",
                               object_type="connection", object_name=p["name"],
                               detail={
                                   "type": "infoblox",
                                   "profile_name": p["name"],
                                   "action_required": (
                                       "After migration, configure FortiADC DNS to "
                                       "forward to Infoblox (same config as OpenStack "
                                       "Controller C named forwarder)."
                                   ),
                               })
            connections.append({
                "type": "ipam_dns", "name": p["name"],
                "provider_type": ptype,
                "action": "Reconfigure DNS forwarding in FortiADC",
            })
        return connections

    def _collect_cloud_integration(self) -> list[dict]:
        items = self._client.get_all("cloud")
        connections = []
        for c in items:
            ctype = c.get("vtype", "")
            if "OPENSTACK" in ctype.upper():
                osc = c.get("openstack_configuration", {})
                self._bus.info(Phase.COLLECT,
                               f"Found OpenStack cloud integration: '{c['name']}'",
                               object_type="connection", object_name=c["name"],
                               detail={
                                   "type": "openstack",
                                   "auth_url": osc.get("keystone_host", ""),
                                   "tenant": osc.get("admin_tenant", ""),
                                   "action": (
                                       "FortiADC does not integrate with OpenStack. "
                                       "VIP addresses currently auto-assigned by Avi IPAM "
                                       "must be pre-allocated as static IPs on FortiADC. "
                                       "Coordinate with IPNS team."
                                   ),
                               })
            connections.append({
                "type": "cloud", "name": c["name"],
                "cloud_type": ctype,
                "action": "Manual VIP allocation required",
            })
        return connections

    def _collect_auth_profiles(self) -> list[dict]:
        items = self._client.get_all("authprofile")
        connections = []
        for p in items:
            ptype = p.get("type", "")
            if "LDAP" in ptype.upper():
                ldap = p.get("ldap", {})
                self._bus.info(Phase.COLLECT,
                               f"Found LDAP auth integration: '{p['name']}'",
                               object_type="connection", object_name=p["name"],
                               detail={
                                   "type": "ldap",
                                   "server_field": "ldap.server (host details redacted)",
                                   "action": (
                                       "FortiADC supports LDAP auth. "
                                       "Reconfigure auth profile in FortiADC "
                                       "using same LDAP server details."
                                   ),
                               })
            connections.append({
                "type": "auth", "name": p["name"],
                "auth_type": ptype,
            })
        return connections

    def _collect_alert_targets(self) -> list[dict]:
        actions = self._client.get_all("actiongroupconfig")
        connections = []
        for a in actions:
            targets = []
            if a.get("snmp_trap_profile_ref"):
                targets.append("SNMP")
            if a.get("syslog_config_ref"):
                targets.append("Syslog")
            if a.get("email_config_ref"):
                targets.append("Email")
            if targets:
                self._bus.info(Phase.COLLECT,
                               f"Alert action '{a['name']}' sends to: {', '.join(targets)}",
                               object_type="connection", object_name=a["name"],
                               detail={
                                   "targets": targets,
                                   "action": "Configure equivalent alerting in FortiADC.",
                               })
            connections.append({"type": "alert", "name": a["name"], "targets": targets})
        return connections

    def _collect_gslb(self) -> list[dict]:
        try:
            gslb_services = self._client.get_all("gslbservice")
        except Exception:
            return []
        connections = []
        for g in gslb_services:
            self._bus.manual(Phase.COLLECT,
                             f"GSLB service '{g['name']}' found — "
                             f"FortiADC GSLB uses a different configuration model",
                             object_type="gslbservice", object_name=g["name"],
                             detail={"action": "Manually reconfigure GSLB in FortiGSLB or "
                                               "equivalent DNS-based load balancing."})
            connections.append({"type": "gslb", "name": g["name"]})
        return connections
