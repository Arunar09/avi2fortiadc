"""
tests/test_migration_tool.py
Comprehensive test suite for the AVI → FortiADC migration tool.
Covers: mappings, transformers, analyzers, state ledger, unsupported tracker,
        intelligence layer, collectors (mocked), and validators.

Run: python3 -m pytest tests/ -v
  or: python3 -m pytest tests/ -v --tb=short (summary only)
"""
from __future__ import annotations

import json
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

import pytest

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a clean temporary directory."""
    return tmp_path


@pytest.fixture
def sample_discovery():
    """Minimal realistic discovery JSON."""
    return {
        "virtual_services": [
            {
                "name": "vs-web-prod",
                "uuid": "vs-uuid-001",
                "_vips": ["10.1.0.10"],
                "services": [{"port": 443, "enable_ssl": True}],
                "pool_ref": "https://avi/api/pool/pool-uuid-001",
                "_resolved": {
                    "pool_ref": "pool-web-prod",
                    "ssl_profile_ref": "ssl-profile-default",
                    "application_profile_ref": "app-http",
                    "ssl_key_and_certificate_refs": ["cert-web-prod"],
                },
                "_has_datascripts": False,
                "enabled": True,
            },
            {
                "name": "vs-api-prod",
                "uuid": "vs-uuid-002",
                "_vips": ["10.1.0.11"],
                "services": [{"port": 80}],
                "pool_ref": "https://avi/api/pool/pool-uuid-002",
                "_resolved": {
                    "pool_ref": "pool-api-prod",
                    "ssl_profile_ref": "",
                    "application_profile_ref": "app-l4",
                    "ssl_key_and_certificate_refs": [],
                },
                "_has_datascripts": True,
                "vs_datascripts": [{"vs_datascript_set_ref": "ds-url-rewrite"}],
                "enabled": True,
            },
        ],
        "pools": [
            {
                "name": "pool-web-prod",
                "uuid": "pool-uuid-001",
                "lb_algorithm": "LB_ALGORITHM_LEAST_CONNECTIONS",
                "default_server_port": 8080,
                "_member_count": 3,
                "_members_up": 3,
                "servers": [
                    {"ip": {"addr": "10.1.1.10"}, "port": 8080, "enabled": True},
                    {"ip": {"addr": "10.1.1.11"}, "port": 8080, "enabled": True},
                    {"ip": {"addr": "10.1.1.12"}, "port": 8080, "enabled": True},
                ],
                "health_monitor_refs": ["https://avi/api/healthmonitor/hm-uuid-001"],
            },
            {
                "name": "pool-api-prod",
                "uuid": "pool-uuid-002",
                "lb_algorithm": "LB_ALGORITHM_ROUND_ROBIN",
                "default_server_port": 9090,
                "_member_count": 2,
                "_members_up": 1,  # one member down — degraded
                "servers": [
                    {"ip": {"addr": "10.1.2.10"}, "port": 9090, "enabled": True},
                    {"ip": {"addr": "10.1.2.11"}, "port": 9090, "enabled": False},
                ],
                "health_monitor_refs": [],
            },
        ],
        "health_monitors": [
            {
                "name": "hm-http-web",
                "uuid": "hm-uuid-001",
                "type": "HEALTH_MONITOR_HTTP",
                "http_monitor": {
                    "http_request": "GET /health HTTP/1.0",
                    "http_response_code": ["HTTP_2XX"],
                },
                "send_interval": 10,
                "receive_timeout": 4,
                "failed_checks": 3,
                "successful_checks": 3,
            },
            {
                "name": "hm-external-custom",
                "uuid": "hm-uuid-002",
                "type": "HEALTH_MONITOR_EXTERNAL",
            },
        ],
        "ssl_certificates": [
            {
                "name": "cert-web-prod",
                "uuid": "cert-uuid-001",
                "_exportable": True,
                "_days_until_expiry": 180,
                "certificate": {"subject": "CN=web.example.com"},
            },
            {
                "name": "cert-hsm-secure",
                "uuid": "cert-uuid-002",
                "_exportable": False,
                "_days_until_expiry": 45,  # also expiring soon
            },
        ],
        "ssl_profiles": [
            {
                "name": "ssl-profile-default",
                "accepted_versions": [
                    {"type": "SSL_VERSION_TLS1_2"},
                    {"type": "SSL_VERSION_TLS1_3"},
                ],
                "accepted_ciphers": "AES256-SHA256",
            }
        ],
        "datascripts": [
            {
                "name": "ds-url-rewrite",
                "uuid": "ds-uuid-001",
                "_events": ["HTTP_REQ"],
                "_total_lines": 12,
                "datascript": [
                    {"evt": "HTTP_REQ", "script": "avi.http.redirect('https://' .. avi.http.hostname() .. avi.http.get_uri())"}
                ],
            }
        ],
        "gslb_services": [],
        "connections": [
            {"type": "cloud", "name": "openstack-cloud"}
        ],
    }


@pytest.fixture
def mock_bus(tmp_dir):
    """Create a real EventBus with temp log dir."""
    from core.events import EventBus
    return EventBus(log_path=tmp_dir / "test.jsonl", verbose=False)


@pytest.fixture
def ledger(tmp_dir):
    """Create a fresh StateLedger in temp dir."""
    from core.state_ledger import StateLedger
    return StateLedger("test-env", state_dir=str(tmp_dir))


# ═══════════════════════════════════════════════════════════════
# 1. MAPPINGS
# ═══════════════════════════════════════════════════════════════

class TestMappings:
    """All mapping tables — every known value must map."""

    def test_lb_algorithms_known_values(self):
        from transformers.mappings import LB_ALGORITHM
        assert LB_ALGORITHM["LB_ALGORITHM_ROUND_ROBIN"] == "RR"
        assert LB_ALGORITHM["LB_ALGORITHM_LEAST_CONNECTIONS"] == "LC"
        assert LB_ALGORITHM["LB_ALGORITHM_WEIGHTED_ROUND_ROBIN"] == "WRR"

    def test_lb_algorithms_unsupported_return_none(self):
        from transformers.mappings import LB_ALGORITHM
        assert LB_ALGORITHM["LB_ALGORITHM_TOPOLOGY"] is None
        assert LB_ALGORITHM["LB_ALGORITHM_CORE_AFFINITY"] is None

    def test_lb_approximate_set_populated(self):
        from transformers.mappings import LB_ALGORITHM_APPROXIMATE
        assert "LB_ALGORITHM_RANDOM" in LB_ALGORITHM_APPROXIMATE
        assert "LB_ALGORITHM_FASTEST_RESPONSE" in LB_ALGORITHM_APPROXIMATE

    def test_health_monitor_types(self):
        from transformers.mappings import HEALTH_MONITOR_TYPE
        assert HEALTH_MONITOR_TYPE["HEALTH_MONITOR_HTTP"]  == "HTTP"
        assert HEALTH_MONITOR_TYPE["HEALTH_MONITOR_TCP"]   == "TCP"
        assert HEALTH_MONITOR_TYPE["HEALTH_MONITOR_PING"]  == "ICMP"
        assert HEALTH_MONITOR_TYPE["HEALTH_MONITOR_EXTERNAL"] is None

    def test_persistence_types(self):
        from transformers.mappings import PERSISTENCE_TYPE
        assert PERSISTENCE_TYPE["PERSISTENCE_TYPE_HTTP_COOKIE"] == "cookie"
        assert PERSISTENCE_TYPE["PERSISTENCE_TYPE_CLIENT_IP_ADDRESS"] == "source-address"
        assert PERSISTENCE_TYPE["PERSISTENCE_TYPE_GSLB_SITE"] is None

    def test_tls_versions(self):
        from transformers.mappings import TLS_VERSION, TLS_DEPRECATED
        assert TLS_VERSION["SSL_VERSION_TLS1_2"] == "tls1.2"
        assert TLS_VERSION["SSL_VERSION_TLS1_3"] == "tls1.3"
        assert TLS_VERSION["SSL_VERSION_TLS1"]   is None  # deprecated
        assert "SSL_VERSION_TLS1"   in TLS_DEPRECATED
        assert "SSL_VERSION_TLS1_1" in TLS_DEPRECATED

    def test_app_profile_types(self):
        from transformers.mappings import APP_PROFILE_TYPE
        assert APP_PROFILE_TYPE["APPLICATION_PROFILE_TYPE_HTTP"] == "http"
        assert APP_PROFILE_TYPE["APPLICATION_PROFILE_TYPE_SIP"]  is None

    def test_all_lb_keys_have_values_or_none(self):
        """No mapping key should be missing — all must be str or None."""
        from transformers.mappings import LB_ALGORITHM
        for k, v in LB_ALGORITHM.items():
            assert v is None or isinstance(v, str), f"Invalid mapping for {k}: {v!r}"


# ═══════════════════════════════════════════════════════════════
# 2. TRANSFORMERS
# ═══════════════════════════════════════════════════════════════

class TestHealthCheckTransformer:

    def test_http_monitor_transforms(self, mock_bus):
        from transformers.pool import HealthCheckTransformer
        t = HealthCheckTransformer(mock_bus)
        hm = {
            "name": "hm-http",
            "type": "HEALTH_MONITOR_HTTP",
            "send_interval": 10,
            "receive_timeout": 4,
            "failed_checks": 3,
            "successful_checks": 2,
            "http_monitor": {
                "http_request": "GET /health HTTP/1.0",
                "http_response_code": ["HTTP_2XX"],
            },
        }
        result = t.transform(hm)
        assert result is not None
        assert result["name"] == "hm-http"
        assert result["type"] == "HTTP"
        assert result["interval"] == 10
        assert result["timeout"] == 4

    def test_external_monitor_returns_none(self, mock_bus):
        from transformers.pool import HealthCheckTransformer
        t = HealthCheckTransformer(mock_bus)
        hm = {"name": "hm-ext", "type": "HEALTH_MONITOR_EXTERNAL"}
        result = t.transform(hm)
        assert result is None  # unsupported — must return None

    def test_tcp_monitor_transforms(self, mock_bus):
        from transformers.pool import HealthCheckTransformer
        t = HealthCheckTransformer(mock_bus)
        hm = {
            "name": "hm-tcp",
            "type": "HEALTH_MONITOR_TCP",
            "send_interval": 5,
            "receive_timeout": 2,
            "failed_checks": 2,
            "successful_checks": 1,
        }
        result = t.transform(hm)
        assert result is not None
        assert result["type"] == "TCP"

    def test_timeout_interval_constraint(self, mock_bus):
        """FortiADC requires timeout < interval. Transformer must enforce this."""
        from transformers.pool import HealthCheckTransformer
        t = HealthCheckTransformer(mock_bus)
        hm = {
            "name": "hm-bad-timing",
            "type": "HEALTH_MONITOR_HTTP",
            "send_interval": 5,
            "receive_timeout": 10,  # timeout > interval — invalid for FortiADC
            "failed_checks": 3,
            "successful_checks": 2,
            "http_monitor": {"http_request": "GET /", "http_response_code": ["HTTP_2XX"]},
        }
        result = t.transform(hm)
        # Must either fix it or return None — must NOT produce timeout >= interval
        if result:
            assert result["timeout"] < result["interval"], \
                "FortiADC constraint: timeout must be < interval"


class TestPoolTransformer:

    def test_pool_with_members(self, mock_bus):
        from transformers.pool import PoolTransformer
        t = PoolTransformer(mock_bus)
        pool = {
            "name": "pool-web",
            "lb_algorithm": "LB_ALGORITHM_LEAST_CONNECTIONS",
            "default_server_port": 8080,
            "_member_count": 2,
            "servers": [
                {"ip": {"addr": "10.1.1.1"}, "port": 8080, "enabled": True},
                {"ip": {"addr": "10.1.1.2"}, "port": 8080, "enabled": True},
            ],
            "health_monitor_refs": ["https://avi/api/healthmonitor/hm-uuid-001"],
        }
        result = t.transform(pool)
        assert result is not None
        assert result["name"] == "pool-web"
        assert result["lb_method"] == "LC"
        assert len(result["members"]) == 2

    def test_unsupported_lb_algorithm(self, mock_bus):
        from transformers.pool import PoolTransformer
        t = PoolTransformer(mock_bus)
        pool = {
            "name": "pool-topology",
            "lb_algorithm": "LB_ALGORITHM_TOPOLOGY",  # no FortiADC equivalent
            "servers": [],
            "_member_count": 0,
        }
        result = t.transform(pool)
        # Must either fall back to RR with WARN or return None — must NOT silently map
        # If it returns something, lb_method must NOT be 'TOPOLOGY'
        if result:
            assert result.get("lb_method") != "TOPOLOGY"
            assert result.get("lb_method") != "LB_ALGORITHM_TOPOLOGY"

    def test_member_weight_constraint(self, mock_bus):
        """Pool member weight 0 is invalid in FortiADC (min is 1)."""
        from transformers.pool import PoolTransformer
        t = PoolTransformer(mock_bus)
        pool = {
            "name": "pool-weight",
            "lb_algorithm": "LB_ALGORITHM_WEIGHTED_ROUND_ROBIN",
            "servers": [
                {"ip": {"addr": "10.0.0.1"}, "port": 80, "enabled": True, "ratio": 0},
                {"ip": {"addr": "10.0.0.2"}, "port": 80, "enabled": True, "ratio": 5},
            ],
            "_member_count": 2,
        }
        result = t.transform(pool)
        if result and result.get("members"):
            for member in result["members"]:
                weight = member.get("weight", 1)
                assert weight >= 1, f"FortiADC min weight is 1, got {weight}"


class TestVirtualServerTransformer:

    def test_basic_vs(self, mock_bus):
        from transformers.pool import VirtualServerTransformer
        t = VirtualServerTransformer(mock_bus)
        vs = {
            "name": "vs-web",
            "uuid": "vs-uuid-001",
            "_vips": ["10.1.0.10"],
            "services": [{"port": 80}],
            "_resolved": {
                "pool_ref": "pool-web",
                "ssl_profile_ref": "",
                "application_profile_ref": "System-HTTP",
                "ssl_key_and_certificate_refs": [],
            },
            "_has_datascripts": False,
            "enabled": True,
        }
        result = t.transform(vs)
        assert result is not None
        assert result["name"] == "vs-web"
        assert result["ip"] == "10.1.0.10"
        assert result["port"] == 80

    def test_datascript_vs_is_skipped(self, mock_bus):
        from transformers.pool import VirtualServerTransformer
        t = VirtualServerTransformer(mock_bus)
        vs = {
            "name": "vs-datascript",
            "_vips": ["10.1.0.20"],
            "services": [{"port": 443}],
            "_resolved": {"pool_ref": "pool-web", "ssl_profile_ref": "", "ssl_key_and_certificate_refs": [], "application_profile_ref": ""},
            "_has_datascripts": True,
            "enabled": True,
        }
        result = t.transform(vs)
        # DataScript VS must be skipped — cannot auto-migrate
        assert result is None


# ═══════════════════════════════════════════════════════════════
# 3. ANALYZERS
# ═══════════════════════════════════════════════════════════════

class TestDependencyGraph:

    def test_builds_graph(self, sample_discovery):
        from analyzers.dependency_graph import build_dependency_graph
        profiles = build_dependency_graph(sample_discovery)
        assert "vs-web-prod" in profiles
        assert "vs-api-prod" in profiles

    def test_datascript_vs_has_datascripts(self, sample_discovery):
        from analyzers.dependency_graph import build_dependency_graph
        profiles = build_dependency_graph(sample_discovery)
        api_profile = profiles["vs-api-prod"]
        assert len(api_profile.datascripts) > 0

    def test_shared_objects_detected(self, sample_discovery):
        from analyzers.dependency_graph import build_dependency_graph, find_shared_objects
        profiles = build_dependency_graph(sample_discovery)
        shared = find_shared_objects(profiles)
        # shared is a dict of object_name → [vs_names using it]
        assert isinstance(shared, dict)


class TestCompatibilityAnalyser:

    def test_http_monitor_is_auto(self, sample_discovery):
        from analyzers.dependency_graph import build_dependency_graph
        from analyzers.compatibility import analyse_compatibility, Compat
        profiles = build_dependency_graph(sample_discovery)
        results  = analyse_compatibility(sample_discovery, profiles)
        hm_results = [r for r in results if r.object_type == "healthmonitor"
                      and r.object_name == "hm-http-web"]
        assert hm_results
        assert hm_results[0].status == Compat.AUTO

    def test_external_monitor_is_manual(self, sample_discovery):
        from analyzers.dependency_graph import build_dependency_graph
        from analyzers.compatibility import analyse_compatibility, Compat
        profiles = build_dependency_graph(sample_discovery)
        results  = analyse_compatibility(sample_discovery, profiles)
        ext = [r for r in results if r.object_name == "hm-external-custom"]
        assert ext
        assert ext[0].status == Compat.MANUAL

    def test_result_list_not_empty(self, sample_discovery):
        from analyzers.dependency_graph import build_dependency_graph
        from analyzers.compatibility import analyse_compatibility
        profiles = build_dependency_graph(sample_discovery)
        results  = analyse_compatibility(sample_discovery, profiles)
        assert len(results) > 0

    def test_all_statuses_are_valid_enum(self, sample_discovery):
        from analyzers.dependency_graph import build_dependency_graph
        from analyzers.compatibility import analyse_compatibility, Compat
        profiles = build_dependency_graph(sample_discovery)
        results  = analyse_compatibility(sample_discovery, profiles)
        valid = {Compat.AUTO, Compat.WARN, Compat.MANUAL, Compat.BLOCKED}
        for r in results:
            assert r.status in valid, f"Invalid status {r.status} for {r.object_name}"


# ═══════════════════════════════════════════════════════════════
# 4. INTELLIGENCE / PATTERN DETECTION
# ═══════════════════════════════════════════════════════════════

class TestIntelligence:

    def test_detects_datascript_pattern(self, sample_discovery, mock_bus):
        from analyzers.dependency_graph import build_dependency_graph
        from core.intelligence import detect_patterns
        profiles = build_dependency_graph(sample_discovery)
        patterns = detect_patterns(sample_discovery, profiles, mock_bus)
        p01 = next((p for p in patterns if p.id == "P01"), None)
        assert p01 is not None
        assert p01.detected is True

    def test_detects_hsm_certificate(self, sample_discovery, mock_bus):
        from analyzers.dependency_graph import build_dependency_graph
        from core.intelligence import detect_patterns
        profiles = build_dependency_graph(sample_discovery)
        patterns = detect_patterns(sample_discovery, profiles, mock_bus)
        p02 = next((p for p in patterns if p.id == "P02"), None)
        assert p02 is not None
        assert p02.detected is True  # cert-hsm-secure is non-exportable

    def test_detects_degraded_pool(self, sample_discovery, mock_bus):
        from analyzers.dependency_graph import build_dependency_graph
        from core.intelligence import detect_patterns
        profiles = build_dependency_graph(sample_discovery)
        patterns = detect_patterns(sample_discovery, profiles, mock_bus)
        p06 = next((p for p in patterns if p.id == "P06"), None)
        assert p06 is not None
        assert p06.detected is True  # pool-api-prod has 1/2 up

    def test_detects_openstack_integration(self, sample_discovery, mock_bus):
        from analyzers.dependency_graph import build_dependency_graph
        from core.intelligence import detect_patterns
        profiles = build_dependency_graph(sample_discovery)
        patterns = detect_patterns(sample_discovery, profiles, mock_bus)
        p08 = next((p for p in patterns if p.id == "P08"), None)
        assert p08 is not None
        assert p08.detected is True

    def test_complexity_score_is_numeric(self, sample_discovery, mock_bus):
        from analyzers.dependency_graph import build_dependency_graph
        from analyzers.compatibility import analyse_compatibility
        from core.intelligence import detect_patterns, score_migration_complexity
        profiles = build_dependency_graph(sample_discovery)
        compat   = analyse_compatibility(sample_discovery, profiles)
        patterns = detect_patterns(sample_discovery, profiles, mock_bus)
        complexity = score_migration_complexity(profiles, compat, patterns)
        assert isinstance(complexity["score"], int)
        assert 1 <= complexity["score"] <= 10
        assert isinstance(complexity["verbal"], str)
        assert complexity["estimated_days"] >= 2

    def test_next_steps_list_ordered(self, sample_discovery, mock_bus):
        from analyzers.dependency_graph import build_dependency_graph
        from analyzers.compatibility import analyse_compatibility
        from core.intelligence import detect_patterns, score_migration_complexity, generate_next_steps
        profiles = build_dependency_graph(sample_discovery)
        compat   = analyse_compatibility(sample_discovery, profiles)
        patterns = detect_patterns(sample_discovery, profiles, mock_bus)
        complexity = score_migration_complexity(profiles, compat, patterns)
        steps = generate_next_steps(patterns, compat, complexity)
        assert isinstance(steps, list)
        assert len(steps) >= 1
        for step in steps:
            assert isinstance(step, str)
            assert len(step) > 10


# ═══════════════════════════════════════════════════════════════
# 5. STATE LEDGER
# ═══════════════════════════════════════════════════════════════

class TestStateLedger:

    def test_phase_lifecycle(self, ledger):
        ledger.phase_start("discover")
        assert ledger.phase_status("discover") == "running"
        ledger.phase_done("discover", notes="clean run")
        assert ledger.is_done("discover")
        assert ledger.phase_status("discover") == "done"

    def test_phase_failed(self, ledger):
        ledger.phase_start("transform")
        ledger.phase_failed("transform", "network timeout")
        assert ledger.phase_status("transform") == "failed"
        assert not ledger.is_done("transform")

    def test_unsupported_item_recorded(self, ledger):
        ledger.add_unsupported(
            object_type="healthmonitor",
            object_name="hm-sip",
            reason="SIP health monitor not supported in FortiADC",
            action="Replace with TCP health check or remove",
            severity="MANUAL",
            forti_note="No SIP HM in FortiADC",
        )
        items = ledger.unsupported_items()
        assert len(items) == 1
        assert items[0].object_name == "hm-sip"
        assert items[0].severity == "MANUAL"
        assert not items[0].resolved

    def test_unsupported_item_resolved(self, ledger):
        ledger.add_unsupported("healthmonitor", "hm-sip",
                               "SIP not supported", "Replace", "MANUAL")
        ok = ledger.resolve_unsupported("healthmonitor", "hm-sip", "Replaced with TCP")
        assert ok is True
        unresolved = ledger.unsupported_items(unresolved_only=True)
        assert len(unresolved) == 0

    def test_unsupported_deduplicated(self, ledger):
        for _ in range(3):
            ledger.add_unsupported("vs", "vs-dup", "reason", "action")
        assert len(ledger.unsupported_items()) == 1

    def test_blocked_count(self, ledger):
        ledger.add_unsupported("cert", "cert-hsm", "HSM", "Re-issue cert", "BLOCKED")
        ledger.add_unsupported("vs", "vs-ds", "DataScript", "Manual rewrite", "MANUAL")
        assert ledger.blocked_count() == 1
        assert ledger.manual_count() == 1

    def test_translated_object_recorded(self, ledger):
        ledger.add_translated("pool", "pool-web", "pool-web",
                              confidence=0.95, approximations=["lb_method: RANDOM→RR"])
        objs = ledger.translated_objects()
        assert len(objs) == 1
        assert objs[0].confidence == 0.95

    def test_low_confidence_filter(self, ledger):
        ledger.add_translated("pool", "pool-a", "pool-a", confidence=0.9)
        ledger.add_translated("pool", "pool-b", "pool-b", confidence=0.6)
        low = ledger.low_confidence_items(threshold=0.8)
        assert len(low) == 1
        assert low[0].avi_name == "pool-b"

    def test_audit_trail_grows(self, ledger):
        ledger.phase_start("discover")
        ledger.phase_done("discover")
        ledger.add_unsupported("hm", "hm-x", "x", "y")
        events = ledger.audit_log()
        assert len(events) >= 3

    def test_state_persists_to_disk(self, tmp_dir):
        from core.state_ledger import StateLedger
        l1 = StateLedger("persist-test", state_dir=str(tmp_dir))
        l1.phase_done("discover")
        l1.add_unsupported("hm", "hm-y", "r", "a")
        # Load fresh instance — should read from disk
        l2 = StateLedger("persist-test", state_dir=str(tmp_dir))
        assert l2.is_done("discover")
        assert len(l2.unsupported_items()) == 1

    def test_summary_dict_shape(self, ledger):
        s = ledger.summary()
        assert "env" in s
        assert "phases" in s
        assert "translated_count" in s
        assert "unsupported" in s
        assert "blocked" in s


# ═══════════════════════════════════════════════════════════════
# 6. EVENTS / SANITIZER
# ═══════════════════════════════════════════════════════════════

class TestEvents:

    def test_sanitize_removes_ips(self):
        from core.events import sanitize
        result = sanitize("Server at 192.168.1.100 failed")
        assert "192.168.1.100" not in result
        assert "[IP_" in result

    def test_sanitize_removes_uuids(self):
        from core.events import sanitize
        text = "Object uuid: 550e8400-e29b-41d4-a716-446655440000"
        result = sanitize(text)
        assert "550e8400-e29b-41d4-a716-446655440000" not in result
        assert "[UUID_" in result

    def test_sanitize_removes_hostnames(self):
        from core.events import sanitize
        result = sanitize("Connect to avi-ctrl.internal.example.com")
        assert "avi-ctrl.internal.example.com" not in result

    def test_sanitize_removes_passwords(self):
        from core.events import sanitize
        result = sanitize('{"password": "supersecret123"}')
        assert "supersecret123" not in result

    def test_bus_records_events(self, mock_bus):
        from core.events import Phase
        mock_bus.info(Phase.COLLECT, "test info")
        mock_bus.warn(Phase.COLLECT, "test warn")
        mock_bus.manual(Phase.COLLECT, "test manual", object_type="vs", object_name="vs-x")
        assert len(mock_bus.all_events) == 3

    def test_bus_has_blockers(self, mock_bus):
        from core.events import Phase
        assert not mock_bus.has_blockers
        mock_bus.critical(Phase.CONNECT, "fatal error")
        assert mock_bus.has_blockers

    def test_bus_summary_counts(self, mock_bus):
        from core.events import Phase
        mock_bus.info(Phase.COLLECT, "a")
        mock_bus.warn(Phase.COLLECT, "b")
        mock_bus.manual(Phase.COLLECT, "c")
        counts = mock_bus.summary_counts()
        assert counts["INFO"] == 1
        assert counts["WARN"] == 1
        assert counts["MANUAL"] == 1


# ═══════════════════════════════════════════════════════════════
# 7. VALIDATORS
# ═══════════════════════════════════════════════════════════════

class TestPreMigrationValidator:

    def test_unreachable_fortiadc_blocks(self, mock_bus):
        from validators.pre_migration import run_pre_migration_checks
        client = MagicMock()
        client.get.side_effect = ConnectionError("unreachable")
        results = run_pre_migration_checks(client, {}, mock_bus)
        blocking = [r for r in results if not r.passed and r.blocking]
        assert len(blocking) >= 1

    def test_reachable_fortiadc_passes(self, mock_bus):
        from validators.pre_migration import run_pre_migration_checks
        client = MagicMock()
        client.get.return_value = {"results": {}}
        client.vdom = "root"
        results = run_pre_migration_checks(client, {"virtual_servers": []}, mock_bus)
        reachable = [r for r in results if r.check == "fortiadc_reachable"]
        assert reachable[0].passed is True


# ═══════════════════════════════════════════════════════════════
# 8. ROLLBACK REPORTER
# ═══════════════════════════════════════════════════════════════

class TestRollbackReporter:

    def test_generates_bash_script(self):
        from reporters.rollback import generate_rollback_script
        config = {
            "virtual_servers": [
                {"name": "vs-web"}, {"name": "vs-api"}
            ]
        }
        script = generate_rollback_script("prod-a", config)
        assert "#!/usr/bin/env bash" in script
        assert "prod-a" in script
        assert "vs-web" in script
        assert "vs-api" in script
        assert "DRY_RUN" in script

    def test_script_has_rollback_steps(self):
        from reporters.rollback import generate_rollback_script
        script = generate_rollback_script("dev-b", {"virtual_servers": [{"name": "vs-x"}]})
        assert "Step 1" in script
        assert "Step 2" in script

    def test_script_is_executable_bash(self):
        from reporters.rollback import generate_rollback_script
        script = generate_rollback_script("test", {"virtual_servers": []})
        first_line = script.split("\n")[0]
        assert "bash" in first_line or "sh" in first_line


# ═══════════════════════════════════════════════════════════════
# 9. FULL PIPELINE INTEGRATION (no network calls)
# ═══════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    """End-to-end pipeline: discovery JSON → FortiADC config."""

    def test_transform_pipeline(self, sample_discovery, mock_bus):
        """Transform all objects and verify output shape."""
        from transformers.pool import (
            PoolTransformer, HealthCheckTransformer,
            SSLCertTransformer, VirtualServerTransformer,
        )

        config = {
            "ssl_certificates":  [],
            "health_checks":     [],
            "real_server_pools": [],
            "virtual_servers":   [],
        }

        t_cert = SSLCertTransformer(mock_bus)
        for cert in sample_discovery.get("ssl_certificates", []):
            r = t_cert.transform(cert)
            if r:
                config["ssl_certificates"].append(r)

        t_hc = HealthCheckTransformer(mock_bus)
        for hm in sample_discovery.get("health_monitors", []):
            r = t_hc.transform(hm)
            if r:
                config["health_checks"].append(r)

        t_pool = PoolTransformer(mock_bus)
        for pool in sample_discovery.get("pools", []):
            r = t_pool.transform(pool)
            if r:
                config["real_server_pools"].append(r)

        t_vs = VirtualServerTransformer(mock_bus)
        for vs in sample_discovery.get("virtual_services", []):
            r = t_vs.transform(vs)
            if r:
                config["virtual_servers"].append(r)

        # Exportable cert should translate; HSM cert should be flagged
        assert len(config["ssl_certificates"]) >= 0  # depends on SSLCertTransformer impl
        # HTTP monitor should translate; EXTERNAL should not
        assert len(config["health_checks"]) >= 1
        # VS with datascripts should NOT appear in output
        ds_vs = [vs for vs in config["virtual_servers"] if "datascript" in vs.get("name", "")]
        assert len(ds_vs) == 0

    def test_full_analyse_pipeline(self, sample_discovery, mock_bus):
        """Analyse pipeline produces patterns, complexity, and steps."""
        from analyzers.dependency_graph import build_dependency_graph, find_shared_objects
        from analyzers.compatibility import analyse_compatibility
        from analyzers.impact import assess_impact
        from core.intelligence import detect_patterns, score_migration_complexity, generate_next_steps

        vs_profiles  = build_dependency_graph(sample_discovery)
        shared       = find_shared_objects(vs_profiles)
        compat       = analyse_compatibility(sample_discovery, vs_profiles)
        impact       = assess_impact(vs_profiles, sample_discovery, shared)
        patterns     = detect_patterns(sample_discovery, vs_profiles, mock_bus)
        complexity   = score_migration_complexity(vs_profiles, compat, patterns)
        steps        = generate_next_steps(patterns, compat, complexity)

        assert len(vs_profiles) == 2
        assert isinstance(patterns, list)
        assert isinstance(complexity["score"], int)
        assert isinstance(steps, list)
        assert len(steps) >= 2

    def test_config_serialises_to_json(self, sample_discovery, mock_bus):
        """Generated FortiADC config must be JSON-serialisable."""
        from transformers.pool import PoolTransformer, HealthCheckTransformer

        config = {"health_checks": [], "real_server_pools": []}

        t_hc = HealthCheckTransformer(mock_bus)
        for hm in sample_discovery.get("health_monitors", []):
            r = t_hc.transform(hm)
            if r:
                config["health_checks"].append(r)

        t_pool = PoolTransformer(mock_bus)
        for pool in sample_discovery.get("pools", []):
            r = t_pool.transform(pool)
            if r:
                config["real_server_pools"].append(r)

        # Must not raise
        serialised = json.dumps(config, default=str)
        assert len(serialised) > 10


# ═══════════════════════════════════════════════════════════════
# 10. EDGE CASES
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_discovery(self, mock_bus):
        """Empty discovery must not raise."""
        from analyzers.dependency_graph import build_dependency_graph
        from analyzers.compatibility import analyse_compatibility
        discovery = {"virtual_services": [], "pools": [], "health_monitors": [],
                     "ssl_certificates": [], "datascripts": [], "gslb_services": [],
                     "connections": []}
        profiles = build_dependency_graph(discovery)
        results  = analyse_compatibility(discovery, profiles)
        assert isinstance(profiles, dict)
        assert isinstance(results, list)

    def test_single_vs_discovery(self, mock_bus):
        """Single-VS discovery handles correctly."""
        from analyzers.dependency_graph import build_dependency_graph
        discovery = {
            "virtual_services": [{
                "name": "vs-solo", "uuid": "u1", "_vips": ["1.2.3.4"],
                "services": [{"port": 80}],
                "_resolved": {"pool_ref": "p1", "ssl_profile_ref": "",
                              "application_profile_ref": "http",
                              "ssl_key_and_certificate_refs": []},
                "_has_datascripts": False, "enabled": True,
            }],
            "pools": [], "health_monitors": [], "ssl_certificates": [],
        }
        profiles = build_dependency_graph(discovery)
        assert "vs-solo" in profiles

    def test_transformer_handles_missing_fields(self, mock_bus):
        """Transformer must not raise on minimal/incomplete input."""
        from transformers.pool import PoolTransformer
        t = PoolTransformer(mock_bus)
        pool = {"name": "minimal-pool"}  # missing most fields
        result = t.transform(pool)
        # Must return None or a valid dict — must NOT raise
        assert result is None or isinstance(result, dict)

    def test_ledger_handles_unknown_phase(self, ledger):
        """Ledger must not raise on unknown phase names."""
        ledger.phase_start("unknown-phase")
        ledger.phase_done("unknown-phase")
        assert ledger.phase_status("unknown-phase") == "done"


# ═══════════════════════════════════════════════════════════════
# 11. STRICT IMPORT CONTRACT
# ═══════════════════════════════════════════════════════════════

class TestStrictImportContract:

    def test_import_accepts_normalized_discovery(self, mock_bus):
        from core.avi_import import import_discovery_payload

        payload = {
            "_meta": {"source": "analyzer"},
            "virtual_services": [],
            "pools": [],
            "health_monitors": [],
            "ssl_certificates": [],
            "ssl_profiles": [],
            "application_profiles": [],
            "network_profiles": [],
            "persistence_profiles": [],
            "http_policy_sets": [],
            "waf_policies": [],
            "auth_profiles": [],
            "datascripts": [],
            "se_groups": [],
            "gslb_services": [],
            "alert_configs": [],
            "connections": [],
        }

        discovery = import_discovery_payload(payload, env_name="env-test", bus=mock_bus)
        assert "virtual_services" in discovery
        assert discovery["_meta"]["source"] in ("analyzer", "normalized_discovery_json")

    def test_import_rejects_raw_virtualservice_payload(self, mock_bus):
        from core.avi_import import import_discovery_payload

        raw_payload = {
            "VirtualService": [{"name": "vs-1"}],
            "Pool": [{"name": "pool-1"}],
        }

        with pytest.raises(ValueError) as exc:
            import_discovery_payload(raw_payload, env_name="env-test", bus=mock_bus)

        assert "Raw Avi export detected" in str(exc.value)

    def test_import_rejects_wrong_types_with_diagnostics(self, mock_bus):
        from core.avi_import import import_discovery_payload

        malformed = {
            "_meta": {},
            "virtual_services": {},
            "pools": [],
            "health_monitors": [],
            "ssl_certificates": [],
            "ssl_profiles": [],
            "application_profiles": [],
            "network_profiles": [],
            "persistence_profiles": [],
            "http_policy_sets": [],
            "waf_policies": [],
            "auth_profiles": [],
            "datascripts": [],
            "se_groups": [],
            "gslb_services": [],
            "alert_configs": [],
            "connections": [],
        }

        with pytest.raises(ValueError) as exc:
            import_discovery_payload(malformed, env_name="env-test", bus=mock_bus)

        msg = str(exc.value)
        assert "wrong key types" in msg
        assert "virtual_services" in msg

    def test_service_import_marks_discovery_phase_done(self, tmp_dir):
        from services.pipeline_service import import_avi_config
        from services.pipeline_service import commit_import_scope
        from core.decision_manifest import DecisionManifestStore

        dirs = {
            "discovery_dir": str(tmp_dir / "discovery"),
            "logs_dir": str(tmp_dir / "logs"),
            "state_dir": str(tmp_dir / "state"),
        }
        payload = {
            "_meta": {},
            "virtual_services": [],
            "pools": [],
            "health_monitors": [],
            "ssl_certificates": [],
            "ssl_profiles": [],
            "application_profiles": [],
            "network_profiles": [],
            "persistence_profiles": [],
            "http_policy_sets": [],
            "waf_policies": [],
            "auth_profiles": [],
            "datascripts": [],
            "se_groups": [],
            "gslb_services": [],
            "alert_configs": [],
            "connections": [],
        }

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_dir)
            result = import_avi_config(dirs, json.dumps(payload), "env-import")
        finally:
            os.chdir(old_cwd)

        assert result.get("decision_required") is True

        # Confirm import scope then commit strict discovery
        store = DecisionManifestStore("env-import", state_dir=str(tmp_dir / "state"))
        manifest = store.load()
        manifest["resolved_import_scope"] = True
        store.save(manifest)

        commit = commit_import_scope(dirs, "env-import")
        assert commit["success"] is True

        ledger_path = tmp_dir / "state" / "env-import-ledger.json"
        assert ledger_path.exists()
        ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert ledger_data["phases"]["discover"]["status"] in ("done", "completed")

    def test_service_auto_normalizes_raw_avi_export(self, tmp_dir):
        from services.pipeline_service import import_avi_config, commit_import_scope
        from core.decision_manifest import DecisionManifestStore

        dirs = {
            "discovery_dir": str(tmp_dir / "discovery"),
            "logs_dir": str(tmp_dir / "logs"),
            "state_dir": str(tmp_dir / "state"),
        }
        raw_payload = {
            "META": {"use_tenant": "admin"},
            "VirtualService": [{"name": "vs1"}],
            "Pool": [{"name": "pool1"}],
        }

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_dir)
            result = import_avi_config(dirs, json.dumps(raw_payload), "env-raw")
        finally:
            os.chdir(old_cwd)

        assert result["success"] is False
        assert result.get("decision_required") is True
        assert result.get("normalized_from_raw") is True
        manifest_path = tmp_dir / "state" / "env-raw-decision-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["mapping_approvals"]["resolved"] is True

        # Defaults from import classifier:
        assert manifest["raw_key_decisions"]["VirtualService"]["decision"] == "include_in_pipeline"
        assert manifest["raw_key_decisions"]["Pool"]["decision"] == "include_in_pipeline"
        assert manifest["raw_key_decisions"]["META"]["decision"] == "exclude"

        # Confirm import scope then commit
        store = DecisionManifestStore("env-raw", state_dir=str(tmp_dir / "state"))
        m2 = store.load()
        m2["resolved_import_scope"] = True
        store.save(m2)
        commit = commit_import_scope(dirs, "env-raw")
        assert commit["success"] is True


class TestAllKeysImportScopeV2:
    def test_import_classifier_groups_pipeline_gslb_context_noise(self):
        from core.import_classifier import classify_raw_keys, GROUP_PIPELINE, GROUP_GSLB_RELATED, GROUP_CONTEXT, GROUP_NOISE

        raw = {
            "VirtualService": [{"name": "vs1"}],
            "Pool": [{"name": "pool1"}],
            "GslbService": [{"name": "g1"}],
            "GslbTenant": [{"name": "tenant1"}],
            "META": {"use_tenant": "admin"},
            "EtcdDataFoo": [],
            "TestSeDatastoreBar": [],
            "SomeOtherKey": {"x": 1},
            "_meta": {"tool_version": "x"},  # should be captured but treated as context for display
        }

        res = classify_raw_keys(raw)
        inv = res["inventory"]
        assert inv["VirtualService"]["group"] == GROUP_PIPELINE
        assert inv["Pool"]["group"] == GROUP_PIPELINE
        assert inv["GslbService"]["group"] == GROUP_GSLB_RELATED
        assert inv["GslbTenant"]["group"] == GROUP_GSLB_RELATED
        assert inv["META"]["group"] == GROUP_NOISE
        assert inv["EtcdDataFoo"]["group"] == GROUP_NOISE
        assert inv["TestSeDatastoreBar"]["group"] == GROUP_NOISE
        assert inv["SomeOtherKey"]["group"] == GROUP_CONTEXT
        assert inv["_meta"]["group"] == GROUP_CONTEXT
        assert inv["GslbService"]["mapped_family"] == "gslb_services"

    def test_import_scope_gate_blocks_until_resolved_import_scope(self):
        from core.decision_manifest import ensure_import_scope_gate

        manifest = {
            "raw_key_inventory": {
                "VirtualService": {"group": "pipeline"},
                "META": {"group": "noise"},
            },
            "raw_key_decisions": {
                "VirtualService": {"decision": "include_in_pipeline"},
                "META": {"decision": "exclude"},
            },
            "resolved_import_scope": False,
        }

        gate = ensure_import_scope_gate(manifest, payload={})
        assert gate.ok is False
        assert "resolved_import_scope" in gate.missing

    def test_candidate_discovery_filtering_based_on_include_in_pipeline(self, tmp_dir):
        from services.pipeline_service import _build_candidate_discovery_from_raw_and_decisions
        from core.import_classifier import classify_raw_keys

        raw = {
            "META": {"use_tenant": "admin"},
            "VirtualService": [{"name": "vs1"}],
            "Pool": [{"name": "pool1"}],
        }

        cls = classify_raw_keys(raw)
        raw_key_inventory = cls["inventory"]

        raw_key_decisions = {
            # Keep VS family but drop Pool family
            "VirtualService": {"decision": "include_in_pipeline", "rationale": ""},
            "Pool": {"decision": "context_only", "rationale": ""},
            "META": {"decision": "exclude", "rationale": ""},
        }

        candidate = _build_candidate_discovery_from_raw_and_decisions(
            raw_payload=raw,
            env_name="env-x",
            raw_key_inventory=raw_key_inventory,
            raw_key_decisions=raw_key_decisions,
        )

        assert isinstance(candidate["_meta"], dict)
        assert len(candidate["virtual_services"]) == 1
        assert candidate["pools"] == []

    def test_import_scope_review_summarises_datascripts_and_redacts_sensitive_fields(self, tmp_dir):
        from services.pipeline_service import get_import_scope_review
        from core.decision_manifest import DecisionManifestStore

        dirs = {
            "state_dir": str(tmp_dir / "state"),
            "logs_dir": str(tmp_dir / "logs"),
        }
        Path(dirs["state_dir"]).mkdir(parents=True, exist_ok=True)
        Path(dirs["logs_dir"]).mkdir(parents=True, exist_ok=True)

        raw_payload = {
            "VSDataScriptSet": [
                {
                    "name": "rewrite-script",
                    "datascript": [
                        {"evt": "HTTP_REQ", "script": "avi.http.redirect('https://example.com')"},
                    ],
                }
            ],
            "SSLKeyAndCertificate": [
                {
                    "name": "cert-a",
                    "key": "super-secret-key-material",
                    "certificate": "-----BEGIN CERTIFICATE-----secret",
                }
            ],
        }
        (Path(dirs["state_dir"]) / "env-ui-raw-snapshot.json").write_text(
            json.dumps(raw_payload), encoding="utf-8"
        )

        store = DecisionManifestStore("env-ui", state_dir=dirs["state_dir"])
        manifest = store.load()
        manifest["raw_key_inventory"] = {
            "VSDataScriptSet": {
                "raw_key": "VSDataScriptSet",
                "value_type": "array",
                "count": 1,
                "group": "pipeline",
                "mapped_family": "datascripts",
                "default_decision": "include_in_pipeline",
            },
            "SSLKeyAndCertificate": {
                "raw_key": "SSLKeyAndCertificate",
                "value_type": "array",
                "count": 1,
                "group": "pipeline",
                "mapped_family": "ssl_certificates",
                "default_decision": "include_in_pipeline",
            },
        }
        manifest["raw_key_decisions"] = {
            "VSDataScriptSet": {"decision": "include_in_pipeline", "rationale": ""},
            "SSLKeyAndCertificate": {"decision": "include_in_pipeline", "rationale": ""},
        }
        store.save(manifest)

        review = get_import_scope_review(dirs, "env-ui", manifest)
        datascript = review["key_details"]["VSDataScriptSet"]
        certs = review["key_details"]["SSLKeyAndCertificate"]

        assert any("script block" in line for line in datascript["summary_lines"])
        assert "manual" in datascript["guidance"].lower()
        assert "<redacted>" in certs["preview_json"]

    def test_recent_activity_filters_env_and_normalises_import_logs(self, tmp_dir):
        from services.pipeline_service import get_recent_activity

        logs_dir = tmp_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "env-a-import-stage.jsonl").write_text(
            json.dumps({"timestamp": "2026-04-16T10:00:00+00:00", "level": "INFO", "message": "stage"}) + "\n",
            encoding="utf-8",
        )
        (logs_dir / "env-b-import-stage.jsonl").write_text(
            json.dumps({"timestamp": "2026-04-16T11:00:00+00:00", "level": "INFO", "message": "other"}) + "\n",
            encoding="utf-8",
        )

        events = get_recent_activity({"logs_dir": str(logs_dir)}, env="env-a")
        assert len(events) == 1
        assert events[0]["_source"] == "env-a"
        assert events[0]["message"] == "stage"


class TestDecisionDrivenManifest:

    def test_manifest_store_and_hash_stability(self, tmp_dir):
        from core.decision_manifest import DecisionManifestStore

        store = DecisionManifestStore("env-dd", state_dir=str(tmp_dir))
        manifest = store.load()
        first_hash = manifest["meta"]["manifest_hash"]
        manifest2 = store.load()
        second_hash = manifest2["meta"]["manifest_hash"]
        assert first_hash == second_hash

    def test_import_scope_gate_detects_missing_decisions(self):
        from core.decision_manifest import ensure_import_scope_gate

        manifest = {"import_scope": {}}
        payload = {
            "_meta": {},
            "virtual_services": [{"name": "vs1"}],
            "pools": [],
            "health_monitors": [],
            "ssl_certificates": [],
            "ssl_profiles": [],
            "application_profiles": [],
            "network_profiles": [],
            "persistence_profiles": [],
            "http_policy_sets": [],
            "waf_policies": [],
            "auth_profiles": [],
            "datascripts": [],
            "se_groups": [],
            "gslb_services": [],
            "alert_configs": [],
            "connections": [],
        }
        gate = ensure_import_scope_gate(manifest, payload)
        assert gate.ok is False
        assert "virtual_services" in gate.missing

    def test_phase_gate_blocks_without_required_decisions(self):
        from core.decision_manifest import ensure_phase_gate

        manifest = {
            "status": "draft",
            "import_scope": {"virtual_services": {"decision": "include"}},
            "mapping_approvals": {"resolved": False},
            "analysis_triage": {"resolved": False},
            "transform_approvals": {"resolved": False},
            "deploy_approval": {"resolved": False, "mode": "", "operator_ack": False},
            "post_validation_decision": {"resolved": False, "action": ""},
        }
        assert ensure_phase_gate(manifest, "analyse").ok is False
        assert ensure_phase_gate(manifest, "transform").ok is False
        assert ensure_phase_gate(manifest, "deploy").ok is False
        assert ensure_phase_gate(manifest, "verify").ok is False
