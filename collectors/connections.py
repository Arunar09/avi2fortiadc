"""
collectors/connections.py
External system connection discovery — standalone collector.

Queries multiple Avi API endpoints to build a complete map of every
external system Avi integrates with. This answers the critical question:
"What else does Avi talk to that we need to reconnect after migration?"

Systems discovered:
  - Infoblox (IPAM/DNS integration)
  - OpenStack (cloud/VIP management)
  - LDAP/Active Directory (authentication)
  - SNMP trap receivers (monitoring)
  - Syslog targets (logging)
  - GSLB upstream DNS (global load balancing)
  - vCenter/compute (SE placement — informational)

Each discovery emits a structured event with:
  - What was found
  - What action is required after migration
  - Why it matters
  - Sanitized details safe for LLM assistance
"""
from __future__ import annotations

from core.base import BaseCollector
from core.events import Phase


class ConnectionDiscoveryCollector(BaseCollector):
    """
    Multi-endpoint collector — queries several Avi API object types
    to build the full external integration map.
    Not a standard Avi object type: does not use get_all() directly.
    """
    object_type  = "connections"
    display_name = "External System Connections"

    def collect(self) -> list[dict]:
        self._bus.info(
            Phase.COLLECT,
            "Discovering all external system connections...",
            object_type=self.object_type,
        )
        connections: list[dict] = []
        discoverers = [
            ("Infoblox/IPAM/DNS",      self._discover_infoblox),
            ("OpenStack integration",  self._discover_openstack),
            ("LDAP/Auth profiles",     self._discover_ldap),
            ("SNMP/Syslog/Alerting",   self._discover_alerting),
            ("GSLB/DNS",               self._discover_gslb),
            ("vCenter/SE placement",   self._discover_vcenter),
        ]
        for label, fn in discoverers:
            try:
                found = fn()
                connections.extend(found)
            except Exception as e:
                self._bus.warn(
                    Phase.COLLECT,
                    f"Connection discovery partial failure ({label}): {e}",
                    object_type=self.object_type,
                    detail={"discoverer": label, "error": str(e)},
                )

        self._bus.info(
            Phase.COLLECT,
            f"Connection discovery complete: {len(connections)} integration(s) found",
            object_type=self.object_type,
        )
        return connections

    # ── Infoblox IPAM/DNS ────────────────────────────────────────────────────

    def _discover_infoblox(self) -> list[dict]:
        items = self._client.get_all("ipamdnsproviderprofile")
        out   = []
        for p in items:
            ptype = p.get("type", "")
            is_infoblox = "INFOBLOX" in ptype.upper()
            is_external = "EXTERNAL" in ptype.upper()

            if is_infoblox or is_external:
                self._bus.info(
                    Phase.COLLECT,
                    f"Found Infoblox/DNS integration: '{p['name']}' (type={ptype})",
                    object_type="connection:ipam_dns",
                    object_name=p["name"],
                    object_uuid=p.get("uuid", ""),
                    detail={
                        "connection_type": "infoblox" if is_infoblox else "external_dns",
                        "profile_name":    p["name"],
                        "profile_type":    ptype,
                        "action": (
                            "FortiADC does not natively integrate with Infoblox IPAM. "
                            "VIPs currently auto-assigned by Avi must be pre-allocated "
                            "as static IPs in Infoblox before migration.\n"
                            "After migration: configure FortiADC DNS settings to forward "
                            "to Infoblox (same approach as OSP Controller-C named forwarder).\n"
                            "Coordinate with the IPNS team to:\n"
                            "  1. Reserve all current Avi VIPs as static hosts in Infoblox\n"
                            "  2. Prevent IP reuse during migration window\n"
                            "  3. Update DNS A-records post-cutover"
                        ),
                        "priority": "HIGH",
                    },
                )
            out.append({
                "type":         "ipam_dns",
                "name":         p["name"],
                "provider_type": ptype,
                "uuid":         p.get("uuid", ""),
                "is_infoblox":  is_infoblox,
                "action_required": True,
            })
        return out

    # ── OpenStack ────────────────────────────────────────────────────────────

    def _discover_openstack(self) -> list[dict]:
        items = self._client.get_all("cloud")
        out   = []
        for c in items:
            ctype = c.get("vtype", "")
            if "OPENSTACK" in ctype.upper():
                osc = c.get("openstack_configuration", {})
                self._bus.info(
                    Phase.COLLECT,
                    f"Found OpenStack cloud integration: '{c['name']}' — "
                    f"FortiADC does not integrate with OpenStack",
                    object_type="connection:openstack",
                    object_name=c["name"],
                    object_uuid=c.get("uuid", ""),
                    detail={
                        "connection_type":  "openstack",
                        "cloud_name":       c["name"],
                        "cloud_type":       ctype,
                        "keystone_host":    osc.get("keystone_host", "[host redacted]"),
                        "mgmt_network":     osc.get("mgmt_network_name", ""),
                        "admin_tenant":     osc.get("admin_tenant", "[tenant redacted]"),
                        "action": (
                            "Pre-migration steps required:\n"
                            "  1. Run: migrate.py discover --output-vips to list all VIPs\n"
                            "  2. Reserve each VIP as a fixed IP in OpenStack Neutron\n"
                            "  3. Coordinate with IPNS team to avoid IP conflicts\n"
                            "  4. Configure FortiADC virtual servers with static VIPs\n"
                            "  5. Do NOT remove VIPs from Avi IPAM until FortiADC validated"
                        ),
                        "priority": "HIGH",
                    },
                )
                out.append({
                    "type":            "cloud:openstack",
                    "name":            c["name"],
                    "cloud_type":      ctype,
                    "uuid":            c.get("uuid", ""),
                    "action_required": True,
                })
            elif "VMWARE" in ctype.upper() or "VCENTER" in ctype.upper():
                self._bus.info(
                    Phase.COLLECT,
                    f"Found VMware/vCenter cloud integration: '{c['name']}' — informational",
                    object_type="connection:vmware",
                    object_name=c["name"],
                    detail={
                        "connection_type": "vmware",
                        "action": "FortiADC manages its own hardware. No migration action.",
                    },
                )
                out.append({
                    "type": "cloud:vmware", "name": c["name"],
                    "uuid": c.get("uuid", ""), "action_required": False,
                })
        return out

    # ── LDAP / Authentication ────────────────────────────────────────────────

    def _discover_ldap(self) -> list[dict]:
        items = self._client.get_all("authprofile")
        out   = []
        for p in items:
            ptype = p.get("type", "")
            if "LDAP" in ptype.upper():
                ldap = p.get("ldap", {})
                self._bus.info(
                    Phase.COLLECT,
                    f"Found LDAP auth integration: '{p['name']}' — "
                    f"FortiADC supports LDAP, reconfigure with same server details",
                    object_type="connection:ldap",
                    object_name=p["name"],
                    object_uuid=p.get("uuid", ""),
                    detail={
                        "connection_type":  "ldap",
                        "auth_type":        ptype,
                        "base_dn":          ldap.get("base_dn", ""),
                        "bind_as_admin":    ldap.get("bind_as_administrator", False),
                        "security":         ldap.get("security", ""),
                        "action": (
                            "Configure LDAP authentication in FortiADC:\n"
                            "  System → Remote Authentication Servers → LDAP\n"
                            "  Use same server details as Avi auth profile.\n"
                            "  Test LDAP auth before migration window."
                        ),
                        "priority": "MEDIUM",
                    },
                )
                out.append({
                    "type": "auth:ldap", "name": p["name"],
                    "uuid": p.get("uuid", ""), "action_required": True,
                })
            elif "SAML" in ptype.upper():
                self._bus.manual(
                    Phase.COLLECT,
                    f"SAML auth profile '{p['name']}' — "
                    f"update IdP to trust FortiADC SP metadata after migration",
                    object_type="connection:saml",
                    object_name=p["name"],
                    detail={
                        "action": (
                            "1. Configure SAML SP in FortiADC\n"
                            "2. Export FortiADC SP metadata\n"
                            "3. Upload to IdP (Azure AD / ADFS / Okta)\n"
                            "4. Test authentication before cutover"
                        ),
                        "priority": "HIGH",
                    },
                )
                out.append({
                    "type": "auth:saml", "name": p["name"],
                    "uuid": p.get("uuid", ""), "action_required": True,
                })
        return out

    # ── Alerting: SNMP / Syslog / Email ─────────────────────────────────────

    def _discover_alerting(self) -> list[dict]:
        out = []
        # SNMP traps
        try:
            for t in self._client.get_all("snmptrapprofile"):
                servers = t.get("trap_servers", [])
                self._bus.info(
                    Phase.COLLECT,
                    f"SNMP trap profile '{t['name']}' ({len(servers)} target(s)) — "
                    f"add same targets in FortiADC SNMP settings",
                    object_type="connection:snmp",
                    object_name=t["name"],
                    detail={
                        "server_count":   len(servers),
                        "action": "Add SNMP trap receivers in FortiADC: "
                                  "System → SNMP → Trap Community",
                        "priority": "LOW",
                    },
                )
                out.append({"type": "snmp", "name": t["name"],
                            "uuid": t.get("uuid",""), "server_count": len(servers),
                            "action_required": True})
        except Exception:
            pass

        # Syslog
        try:
            for s in self._client.get_all("syslogappprofile"):
                self._bus.info(
                    Phase.COLLECT,
                    f"Syslog profile '{s['name']}' — "
                    f"configure equivalent syslog in FortiADC log settings",
                    object_type="connection:syslog",
                    object_name=s["name"],
                    detail={
                        "action": "Configure syslog in FortiADC: "
                                  "System → Log → Syslog",
                        "priority": "LOW",
                    },
                )
                out.append({"type": "syslog", "name": s["name"],
                            "uuid": s.get("uuid",""), "action_required": True})
        except Exception:
            pass

        # Alert action groups (email etc)
        try:
            for a in self._client.get_all("actiongroupconfig"):
                targets = []
                if a.get("snmp_trap_profile_ref"):   targets.append("SNMP")
                if a.get("syslog_config_ref"):        targets.append("Syslog")
                if a.get("email_config_ref"):         targets.append("Email")
                if targets:
                    out.append({"type": "alert_action", "name": a["name"],
                                "targets": targets, "action_required": True})
        except Exception:
            pass

        return out

    # ── GSLB ────────────────────────────────────────────────────────────────

    def _discover_gslb(self) -> list[dict]:
        out = []
        try:
            for g in self._client.get_all("gslb"):
                dns_vses = g.get("dns_configs", [])
                self._bus.manual(
                    Phase.COLLECT,
                    f"GSLB global config '{g.get('name','global')}' with "
                    f"{len(dns_vses)} DNS VS(es) — requires FortiGSLB or DNS redesign",
                    object_type="connection:gslb",
                    object_name=g.get("name","global"),
                    detail={
                        "dns_vs_count": len(dns_vses),
                        "action": (
                            "GSLB migration is OUT OF SCOPE for this tool.\n"
                            "Engage network team and Fortinet SE for GSLB design.\n"
                            "Keep Avi GSLB live until FortiGSLB is validated."
                        ),
                        "priority": "CRITICAL",
                    },
                )
                out.append({
                    "type": "gslb", "name": g.get("name","global"),
                    "dns_vs_count": len(dns_vses), "action_required": True,
                })
        except Exception:
            pass

        try:
            for svc in self._client.get_all("gslbservice"):
                out.append({
                    "type": "gslb:service", "name": svc.get("name","?"),
                    "algorithm": svc.get("algorithm",""), "action_required": True,
                })
        except Exception:
            pass
        return out

    # ── vCenter / SE Placement ───────────────────────────────────────────────

    def _discover_vcenter(self) -> list[dict]:
        """SE group details — informational only, no migration action."""
        out = []
        try:
            for seg in self._client.get_all("serviceenginegroup"):
                out.append({
                    "type": "se_group", "name": seg.get("name","?"),
                    "max_vs": seg.get("max_vs_per_se", 0),
                    "action_required": False,
                    "note": "SE Groups are Avi-only. FortiADC manages its own hardware.",
                })
        except Exception:
            pass
        return out
