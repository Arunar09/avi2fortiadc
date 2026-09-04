"""
transformers/mappings.py
Explicit, version-controlled mapping tables: Avi enum → FortiADC equivalent.
Every mapping is auditable. Nothing is guessed.
CHANGE CONTROL: Any change to a mapping table must be documented in CHANGELOG.md.
"""
from __future__ import annotations

# ── Load balancing algorithms ─────────────────────────────────────────────────
LB_ALGORITHM: dict[str, str | None] = {
    "LB_ALGORITHM_ROUND_ROBIN":           "RR",          # Round Robin
    "LB_ALGORITHM_LEAST_CONNECTIONS":     "LC",          # Least Connection
    "LB_ALGORITHM_FEWEST_SERVERS":        "LC",          # closest equivalent
    "LB_ALGORITHM_RANDOM":                "RR",          # no random in FortiADC → RR + warn
    "LB_ALGORITHM_FASTEST_RESPONSE":      "LC",          # closest equivalent → LC
    "LB_ALGORITHM_CONSISTENT_HASH":       "PI",          # Predictive IP Hash
    "LB_ALGORITHM_LEAST_LOAD":            "LC",
    "LB_ALGORITHM_WEIGHTED_ROUND_ROBIN":  "WRR",
    "LB_ALGORITHM_TOPOLOGY":              None,           # no equivalent — MANUAL
    "LB_ALGORITHM_CORE_AFFINITY":         None,           # no equivalent — MANUAL
}

# Algorithms with no direct equivalent that need a warning even with a mapping
LB_ALGORITHM_APPROXIMATE: set[str] = {
    "LB_ALGORITHM_RANDOM",
    "LB_ALGORITHM_FASTEST_RESPONSE",
    "LB_ALGORITHM_FEWEST_SERVERS",
}

# ── Health monitor types ───────────────────────────────────────────────────────
HEALTH_MONITOR_TYPE: dict[str, str | None] = {
    "HEALTH_MONITOR_HTTP":     "HTTP",
    "HEALTH_MONITOR_HTTPS":    "HTTPS",
    "HEALTH_MONITOR_TCP":      "TCP",
    "HEALTH_MONITOR_UDP":      "UDP",
    "HEALTH_MONITOR_PING":     "ICMP",
    "HEALTH_MONITOR_DNS":      "DNS",
    "HEALTH_MONITOR_EXTERNAL": None,    # custom script — MANUAL
    "HEALTH_MONITOR_SIP":      None,    # no SIP HM in FortiADC
    "HEALTH_MONITOR_RADIUS":   None,    # no RADIUS HM in FortiADC
    "HEALTH_MONITOR_SMTP":     None,    # no SMTP HM
    "HEALTH_MONITOR_IMAP":     None,
    "HEALTH_MONITOR_POP3":     None,
    "HEALTH_MONITOR_FTP":      None,
}

# ── Persistence types ──────────────────────────────────────────────────────────
PERSISTENCE_TYPE: dict[str, str | None] = {
    "PERSISTENCE_TYPE_HTTP_COOKIE":       "cookie",
    "PERSISTENCE_TYPE_APP_COOKIE":        "cookie",
    "PERSISTENCE_TYPE_CLIENT_IP_ADDRESS": "source-address",
    "PERSISTENCE_TYPE_TLS":               "ssl-session-id",
    "PERSISTENCE_TYPE_GSLB_SITE":         None,
    "PERSISTENCE_TYPE_CUSTOM_HTTP_HEADER": None,
    "PERSISTENCE_TYPE_HASHING":           None,
}

# ── Application profile types ──────────────────────────────────────────────────
APP_PROFILE_TYPE: dict[str, str | None] = {
    "APPLICATION_PROFILE_TYPE_HTTP":  "http",
    "APPLICATION_PROFILE_TYPE_HTTPS": "http",
    "APPLICATION_PROFILE_TYPE_TCP":   "tcp",
    "APPLICATION_PROFILE_TYPE_UDP":   "udp",
    "APPLICATION_PROFILE_TYPE_DNS":   "dns",
    "APPLICATION_PROFILE_TYPE_L4":    "tcp",
    "APPLICATION_PROFILE_TYPE_SIP":   None,
}

# ── SSL/TLS versions ───────────────────────────────────────────────────────────
TLS_VERSION: dict[str, str | None] = {
    "SSL_VERSION_TLS1":    None,         # Deprecated — FortiADC blocks by default
    "SSL_VERSION_TLS1_1":  None,         # Deprecated
    "SSL_VERSION_TLS1_2":  "tls1.2",
    "SSL_VERSION_TLS1_3":  "tls1.3",
}

# Deprecated TLS versions — flag for review
TLS_DEPRECATED: set[str] = {"SSL_VERSION_TLS1", "SSL_VERSION_TLS1_1",
                              "SSL_VERSION_SSLV3", "SSL_VERSION_SSLV2"}

# ── FortiADC API paths ─────────────────────────────────────────────────────────
FORTIADC_PATHS = {
    "virtual_server":    "load_balance/virtual_server",
    "real_server_pool":  "load_balance/real_server_pool",
    "real_server":       "load_balance/real_server",
    "health_check":      "load_balance/health_check",
    "http_profile":      "load_balance/profile/http",
    "tcp_profile":       "load_balance/profile/tcp",
    "udp_profile":       "load_balance/profile/udp",
    "ssl_profile":       "load_balance/profile/client_ssl",
    "cookie_persist":    "load_balance/persistence/cookie",
    "src_addr_persist":  "load_balance/persistence/source_address",
    "ssl_cert":          "system/certificate/local",
    "ca_cert":           "system/certificate/ca",
    "content_route":     "load_balance/content_routing",
}

# ── Default values applied when Avi field is absent ───────────────────────────
DEFAULTS = {
    "health_check_interval":    5,
    "health_check_timeout":     2,
    "health_check_up_retry":    3,
    "health_check_down_retry":  3,
    "connection_timeout":       3600,
    "max_connections":          0,       # 0 = unlimited in FortiADC
    "cookie_expire_type":       "session",
    "ssl_min_version":          "tls1.2",
}


def map_lb_algorithm(avi_algo: str) -> tuple[str, bool]:
    """
    Returns (fortiadc_algo, is_approximate).
    is_approximate=True means the mapping is best-effort and should be reviewed.
    """
    mapped = LB_ALGORITHM.get(avi_algo)
    if mapped is None:
        return "RR", True   # safe fallback
    is_approx = avi_algo in LB_ALGORITHM_APPROXIMATE
    return mapped, is_approx


def map_health_monitor_type(avi_type: str) -> str | None:
    return HEALTH_MONITOR_TYPE.get(avi_type)


def map_persistence_type(avi_type: str) -> str | None:
    return PERSISTENCE_TYPE.get(avi_type)


def map_app_profile_type(avi_type: str) -> str | None:
    return APP_PROFILE_TYPE.get(avi_type)
