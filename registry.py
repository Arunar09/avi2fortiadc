"""
registry.py
Central registry of all collectors and transformers.
To add a new object type: add collector here + transformer.
No other files need changing.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Type, Optional

from core.base import BaseCollector, BaseTransformer
from core.avi_client import AviClient
from core.events import EventBus

# ── Collector imports ────────────────────────────────────────────────────────
from collectors.virtual_service import VirtualServiceCollector
from collectors.pool import PoolCollector
from collectors.ssl import SSLProfileCollector, SSLCertificateCollector
from collectors.profiles import (ApplicationProfileCollector,
                                  NetworkProfileCollector,
                                  PersistenceProfileCollector)
from collectors.policies import (HTTPPolicySetCollector,
                                  WAFPolicyCollector,
                                  AuthProfileCollector)
from collectors.datascript import DataScriptCollector
from collectors.system import (ServiceEngineGroupCollector,
                                GSLBCollector, AlertConfigCollector,
                                GSLBGlobalCollector, GSLBGeoDbCollector,
                                CloudContextCollector, TenantContextCollector,
                                VrfContextCollector, IpAddrGroupCollector,
                                IpamDnsProviderCollector, NetworkContextCollector)
from collectors.connections import ConnectionDiscoveryCollector


@dataclass
class CollectorEntry:
    cls:          Type[BaseCollector]
    key:          str     # key in discovery JSON output
    required:     bool    # if True, failure is fatal
    description:  str


@dataclass
class TransformerEntry:
    cls:         Type[BaseTransformer]
    source_key:  str    # key in discovery JSON (matches CollectorEntry.key)
    description: str


# ── All collectors — in collection order ─────────────────────────────────────
# Order matters: collect foundations before things that reference them
COLLECTORS: list[CollectorEntry] = [
    CollectorEntry(VirtualServiceCollector,    "virtual_services",      True,
                   "Virtual Services (core migration objects)"),
    CollectorEntry(PoolCollector,              "pools",                 True,
                   "Server pools and members"),
    CollectorEntry(SSLCertificateCollector,    "ssl_certificates",      True,
                   "SSL certificates and keys"),
    CollectorEntry(SSLProfileCollector,        "ssl_profiles",          False,
                   "SSL cipher and TLS version profiles"),
    CollectorEntry(ApplicationProfileCollector,"application_profiles",  False,
                   "HTTP/TCP/UDP application profiles"),
    CollectorEntry(NetworkProfileCollector,    "network_profiles",      False,
                   "TCP/UDP network profiles"),
    CollectorEntry(PersistenceProfileCollector,"persistence_profiles",  False,
                   "Session persistence profiles"),
    CollectorEntry(HTTPPolicySetCollector,     "http_policy_sets",      False,
                   "HTTP request/response policy sets"),
    CollectorEntry(WAFPolicyCollector,         "waf_policies",          False,
                   "WAF policies"),
    CollectorEntry(AuthProfileCollector,       "auth_profiles",         False,
                   "Authentication profiles (LDAP/SAML)"),
    CollectorEntry(DataScriptCollector,        "datascripts",           False,
                   "DataScript sets (Lua/Python — all MANUAL)"),
    CollectorEntry(ServiceEngineGroupCollector,"se_groups",             False,
                   "Service Engine Groups (informational only)"),
    CollectorEntry(GSLBCollector,              "gslb_services",         False,
                   "GSLB services"),
    CollectorEntry(AlertConfigCollector,       "alert_configs",         False,
                   "Alert configurations"),
    CollectorEntry(GSLBGlobalCollector,        "gslb_global",           False,
                   "Global GSLB Configuration"),
    CollectorEntry(GSLBGeoDbCollector,         "gslb_geo_db",           False,
                   "GSLB GeoDB Profiles"),
    CollectorEntry(CloudContextCollector,      "clouds",                False,
                   "Cloud Context"),
    CollectorEntry(TenantContextCollector,     "tenants",               False,
                   "Tenant Context"),
    CollectorEntry(VrfContextCollector,        "vrf_contexts",          False,
                   "VRF Context"),
    CollectorEntry(IpAddrGroupCollector,       "ip_addr_groups",        False,
                   "IP Address Groups"),
    CollectorEntry(IpamDnsProviderCollector,   "ipam_dns_providers",    False,
                   "IPAM/DNS Provider Profiles"),
    CollectorEntry(NetworkContextCollector,    "networks",              False,
                   "Network Context"),
    CollectorEntry(ConnectionDiscoveryCollector,"connections",          False,
                   "External system connections (Infoblox, OpenStack, LDAP, SNMP)"),
]

# ── Transformer imports ───────────────────────────────────────────────────────
from transformers.pool import (PoolTransformer, HealthCheckTransformer,
                                SSLCertTransformer, VirtualServerTransformer)
from transformers.profiles import (ApplicationProfileTransformer,
                                    NetworkProfileTransformer,
                                    PersistenceProfileTransformer)

# ── Transformer registry ──────────────────────────────────────────────────────
# Order matches DEPLOY_ORDER in fortiadc_deployer.py:
# certs → health_checks → pools → persistence → virtual_servers
TRANSFORMERS: list[TransformerEntry] = [
    TransformerEntry(SSLCertTransformer,          "ssl_certificates",
                     "SSL certificates → FortiADC local certificates"),
    TransformerEntry(HealthCheckTransformer,       "health_monitors",
                     "Health monitors → FortiADC health checks"),
    TransformerEntry(PoolTransformer,              "pools",
                     "Pools → FortiADC real server pools"),
    TransformerEntry(ApplicationProfileTransformer,"application_profiles",
                     "Application profiles → FortiADC HTTP/TCP/UDP profiles"),
    TransformerEntry(NetworkProfileTransformer,    "network_profiles",
                     "Network profiles → FortiADC TCP profiles"),
    TransformerEntry(PersistenceProfileTransformer,"persistence_profiles",
                     "Persistence profiles → FortiADC persistence"),
    TransformerEntry(VirtualServerTransformer,     "virtual_services",
                     "Virtual services → FortiADC virtual servers"),
]


def run_all_collectors(client: AviClient, bus: EventBus) -> dict:
    """
    Run all registered collectors. Returns discovery dict keyed by CollectorEntry.key.
    Errors in non-required collectors are logged but do not stop collection.
    """
    discovery: dict = {
        "_meta": {
            "tool_version": "0.1.0",
            "avi_controller": client.controller,
            "tenant":         client.tenant,
        }
    }

    for entry in COLLECTORS:
        collector = entry.cls(client, bus)
        try:
            results = collector.collect()
            discovery[entry.key] = results
        except Exception as e:
            msg = f"Collector '{entry.cls.__name__}' failed: {e}"
            if entry.required:
                bus.critical(
                    __import__("core.events", fromlist=["Phase"]).Phase.COLLECT,
                    msg, detail={"error": str(e), "collector": entry.cls.__name__}
                )
                raise
            else:
                bus.error(
                    __import__("core.events", fromlist=["Phase"]).Phase.COLLECT,
                    msg, detail={"error": str(e)}
                )
                discovery[entry.key] = []

    return discovery


def get_transformer(source_key: str,
                    bus: EventBus) -> Optional[BaseTransformer]:
    """Look up and instantiate a transformer by source key."""
    for entry in TRANSFORMERS:
        if entry.source_key == source_key:
            return entry.cls(bus)
    return None
