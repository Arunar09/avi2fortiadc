"""
services/report_service.py
Lists generated migration reports and FortiADC config artifacts.
Read-only. No generation, no deletion.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from services.common import canonical_env_name, load_known_envs


def get_known_envs(dirs: dict) -> list[str]:
    """Return sorted list of all real environment names visible to the tool."""
    return load_known_envs(dirs)


def get_all_reports(dirs: dict) -> list[dict]:
    """
    Return all report files from the reports/ directory.
    Each entry: { env, filename, extension, size_kb, mtime, url_hint }
    """
    reports_dir = Path(dirs.get("reports_dir", "reports"))
    if not reports_dir.exists():
        return []

    reports = []
    for f in sorted(reports_dir.iterdir()):
        if f.suffix not in (".html", ".md", ".txt", ".json"):
            continue
        try:
            stat = f.stat()
            # Infer env from filename (e.g. dev-b-analysis.html → dev-b)
            env = canonical_env_name(f.name)
            reports.append({
                "env":       env,
                "filename":  f.name,
                "extension": f.suffix.lstrip("."),
                "size_kb":   round(stat.st_size / 1024, 1),
                "mtime":     stat.st_mtime,
                "mtime_display": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "path":      str(f),
                "type":      _report_type(f.name),
            })
        except Exception:
            pass

    return sorted(reports, key=lambda r: r["mtime"], reverse=True)


def _report_type(filename: str) -> str:
    if "transform-review.html" in filename:
        return "Transform Review"
    if "transform-review.md" in filename:
        return "Transform Review"
    if "analysis.html" in filename:
        return "HTML Analysis"
    if "analysis.md" in filename:
        return "Markdown Report"
    if "manual-migration-runbook" in filename:
        return "Manual Runbook"
    if "sanitized" in filename:
        return "LLM Pack"
    if "llm-pack" in filename:
        return "LLM Pack"
    return "Report"


def get_artifacts(dirs: dict, env: str = "") -> list[dict]:
    """
    Return FortiADC config JSON artifacts.
    If env specified, filter to that environment.
    """
    fadc_dir = Path(dirs.get("fortiadc_dir", "fortiadc"))
    if not fadc_dir.exists():
        return []

    artifacts = []
    for f in sorted(fadc_dir.glob("*.json")):
        env_name = f.stem.replace("-config", "")
        if env and env_name != env:
            continue
        try:
            stat  = f.stat()
            data  = json.loads(f.read_text(encoding="utf-8"))
            vs    = len(data.get("virtual_servers",   []))
            pools = len(data.get("real_server_pools", []))
            certs = len(data.get("ssl_certificates",  []))
            artifacts.append({
                "env":      env_name,
                "filename": f.name,
                "size_kb":  round(stat.st_size / 1024, 1),
                "mtime":    stat.st_mtime,
                "mtime_display": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "vs_count":    vs,
                "pool_count":  pools,
                "cert_count":  certs,
            })
        except Exception:
            pass

    return sorted(artifacts, key=lambda a: a["mtime"], reverse=True)
