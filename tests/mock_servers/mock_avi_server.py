#!/usr/bin/env python3
"""
tests/mock_servers/mock_avi_server.py
Minimal mock AVI controller REST API for local laptop testing.

Serves fixture responses so the migration tool can run the full
discover → analyse → transform pipeline without a real AVI controller.

Usage:
    pip install flask
    python3 mock_avi_server.py        # runs on :8080

Or via Docker (see docker-compose.yml in tests/):
    docker compose up mock-avi
"""
import json
import os
from pathlib import Path
from flask import Flask, jsonify, request, make_response

app = Flask(__name__)

# Load fixture data
FIXTURE = Path(__file__).parent.parent / "fixtures" / "dev_b_discovery.json"
DISCOVERY = json.loads(FIXTURE.read_text()) if FIXTURE.exists() else {}

# AVI API version header expected
AVI_VERSION = os.environ.get("AVI_VERSION", "22.1.5")


def paginate(items: list, page_size: int = 200, page: int = 1) -> dict:
    start = (page - 1) * page_size
    end   = start + page_size
    return {
        "count":   len(items),
        "results": items[start:end],
    }


@app.before_request
def check_version():
    ver = request.headers.get("X-Avi-Version", "")
    # Accept any version for local testing
    pass


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.route("/login", methods=["POST"])
def login():
    return jsonify({"status": "success", "version": AVI_VERSION}), 200, {
        "Set-Cookie": "csrftoken=mock-csrf-token; Path=/; SameSite=Lax"
    }


@app.route("/logout", methods=["GET", "POST"])
def logout():
    return jsonify({"status": "success"})


# ── Core AVI API endpoints ───────────────────────────────────────────────────

@app.route("/api/cluster/version")
def cluster_version():
    return jsonify({"Version": AVI_VERSION, "version": AVI_VERSION})


@app.route("/api/initial-data")
def initial_data():
    return jsonify({"version": {"Version": AVI_VERSION}})


@app.route("/api/virtualservice")
def virtualservices():
    items = DISCOVERY.get("virtual_services", [])
    page  = int(request.args.get("page", 1))
    return jsonify(paginate(items, page=page))


@app.route("/api/pool")
def pools():
    return jsonify(paginate(DISCOVERY.get("pools", [])))


@app.route("/api/healthmonitor")
def health_monitors():
    return jsonify(paginate(DISCOVERY.get("health_monitors", [])))


@app.route("/api/sslkeyandcertificate")
def ssl_certificates():
    return jsonify(paginate(DISCOVERY.get("ssl_certificates", [])))


@app.route("/api/sslprofile")
def ssl_profiles():
    return jsonify(paginate(DISCOVERY.get("ssl_profiles", [])))


@app.route("/api/applicationpersistenceprofile")
def persistence_profiles():
    return jsonify(paginate(DISCOVERY.get("persistence_profiles", [])))


@app.route("/api/applicationprofile")
def application_profiles():
    return jsonify(paginate(DISCOVERY.get("application_profiles", [])))


@app.route("/api/networkprofile")
def network_profiles():
    return jsonify(paginate(DISCOVERY.get("network_profiles", [])))


@app.route("/api/httppolicyset")
def http_policy_sets():
    return jsonify(paginate(DISCOVERY.get("http_policy_sets", [])))


@app.route("/api/wafpolicy")
def waf_policies():
    return jsonify(paginate(DISCOVERY.get("waf_policies", [])))


@app.route("/api/authprofile")
def auth_profiles():
    return jsonify(paginate(DISCOVERY.get("auth_profiles", [])))


@app.route("/api/vsdatascriptset")
def datascripts():
    return jsonify(paginate(DISCOVERY.get("datascripts", [])))


@app.route("/api/serviceenginegroup")
def se_groups():
    return jsonify(paginate(DISCOVERY.get("se_groups", [])))


@app.route("/api/gslbservice")
def gslb_services():
    return jsonify(paginate(DISCOVERY.get("gslb_services", [])))


@app.route("/api/alertconfig")
def alert_configs():
    return jsonify(paginate(DISCOVERY.get("alert_configs", [])))


@app.route("/api/ipamdnsproviderprofile")
def ipam_dns():
    return jsonify(paginate([{"name": "Infoblox-IPAM", "type": "IPAMDNS_TYPE_INFOBLOX"}]))


@app.route("/api/cloud")
def clouds():
    return jsonify(paginate([{
        "name": "OpenStack-Cloud",
        "vtype": "CLOUD_OPENSTACK",
        "openstack_configuration": {"keystone_host": "keystone.internal"}
    }]))


@app.route("/api/tenant")
def tenants():
    return jsonify(paginate([{
        "name": "Tenant-Dev-B",
        "uuid": "tenant-dev-b",
        "local": True
    }]))


# ── Runtime endpoints (health state) ─────────────────────────────────────────

@app.route("/api/virtualservice/<uuid>/runtime")
def vs_runtime(uuid):
    return jsonify({
        "oper_status": {"state": "OPER_UP"},
        "num_se_assigned": 1,
    })


@app.route("/api/pool/<uuid>/runtime")
def pool_runtime(uuid):
    return jsonify({
        "oper_status": {"state": "OPER_UP"},
        "num_servers_up": 2,
        "num_servers": 2,
    })


# ── Catch-all for unmapped endpoints ─────────────────────────────────────────

@app.route("/api/<path:path>")
def catch_all(path):
    return jsonify({"count": 0, "results": []}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Mock AVI server starting on :{port}")
    print(f"Serving fixture: {FIXTURE}")
    app.run(host="0.0.0.0", port=port, debug=False)
