#!/usr/bin/env python3
"""
tests/mock_servers/mock_fortiadc_server.py
Minimal mock FortiADC REST API for local laptop testing.

Accepts all deploy requests, stores objects in-memory, and returns
realistic FortiADC API responses. Used to test the full deploy pipeline
without a real FortiADC appliance.

Usage:
    pip install flask
    python3 mock_fortiadc_server.py   # runs on :8443

Or via Docker:
    docker compose up mock-fortiadc
"""
import json
import os
from flask import Flask, jsonify, request

app = Flask(__name__)

# In-memory object store per VDOM
_STORE: dict = {}  # {vdom: {path: {name: payload}}}
_TOKEN = "mock-fortiadc-token-dev"


def _vdom():
    return request.args.get("vdom", "root")


def _store(vdom, path):
    return _STORE.setdefault(vdom, {}).setdefault(path, {})


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/api/user/login", methods=["POST"])
def login():
    return jsonify({"token": _TOKEN, "http_code": 200})


@app.route("/api/user/logout", methods=["POST", "GET"])
def logout():
    return jsonify({"http_code": 200})


# ── System status ─────────────────────────────────────────────────────────────

@app.route("/api/system/status")
def system_status():
    return jsonify({
        "results": {
            "Platform": "FortiADC-VM",
            "Version":  "7.4.0",
            "Serial":   "MOCK-000001",
        }
    })


@app.route("/api/system/vdom/<vdom_name>")
def vdom_exists(vdom_name):
    return jsonify({"results": {"mkey": vdom_name, "status": "enabled"}})


@app.route("/api/system/performance")
def performance():
    return jsonify({
        "results": {
            "current_connections":     1200,
            "current_ssl_connections": 400,
        }
    })


# ── Generic CRUD for load-balance objects ─────────────────────────────────────

@app.route("/api/<path:api_path>", methods=["GET"])
def get_object(api_path):
    vdom   = _vdom()
    store  = _store(vdom, api_path)
    # Check if requesting specific object
    name = request.args.get("mkey") or api_path.split("/")[-1]
    if name in store:
        return jsonify({"results": store[name], "http_code": 200})
    # List all
    return jsonify({"results": list(store.values()), "http_code": 200})


@app.route("/api/<path:api_path>", methods=["POST"])
def create_object(api_path):
    vdom    = _vdom()
    payload = request.get_json(force=True, silent=True) or {}
    name    = payload.get("mkey", payload.get("name", f"obj-{len(_store(vdom, api_path))+1}"))
    _store(vdom, api_path)[name] = payload
    return jsonify({"mkey": name, "http_code": 200})


@app.route("/api/<path:api_path>/<name>", methods=["PUT"])
def update_object(api_path, name):
    vdom    = _vdom()
    payload = request.get_json(force=True, silent=True) or {}
    _store(vdom, api_path)[name] = payload
    return jsonify({"mkey": name, "http_code": 200})


@app.route("/api/<path:api_path>/<name>", methods=["DELETE"])
def delete_object(api_path, name):
    vdom  = _vdom()
    store = _store(vdom, api_path)
    store.pop(name, None)
    return jsonify({"mkey": name, "http_code": 200})


# ── Object existence check ────────────────────────────────────────────────────

@app.route("/api/<path:api_path>/<name>", methods=["GET"])
def get_specific(api_path, name):
    vdom  = _vdom()
    store = _store(vdom, api_path)
    if name in store:
        return jsonify({"results": store[name], "http_code": 200})
    return jsonify({"http_code": 404, "error": "Object not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8443))
    print(f"Mock FortiADC server starting on :{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
