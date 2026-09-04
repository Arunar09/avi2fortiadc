"""
core/audit_export.py
Tamper-evident audit log export and SIEM integration.

Provides:
  1. Hash chain — each JSONL log entry includes SHA-256 of previous entry
     Making log tampering detectable (any change breaks the chain)
  2. SIEM export — converts JSONL log to CEF (Common Event Format) or
     plain JSONL for forwarding to Splunk, ELK, or any syslog-capable SIEM
  3. Chain verification — validates the hash chain integrity
  4. Log summary — aggregates events by level for reporting

Design:
  - Hash chaining is additive — existing logs can be upgraded
  - CEF format is widely accepted by enterprise SIEMs
  - No network calls — all output is local file or stdout
  - Air-gap compatible — output files transferred manually to SIEM

Usage:
    from core.audit_export import chain_log_file, export_cef, verify_chain

    # Add hash chain to existing log file
    chain_log_file("logs/prod-a-deploy.jsonl")

    # Export to CEF for SIEM
    export_cef("logs/prod-a-deploy.jsonl", "logs/prod-a-deploy.cef")

    # Verify integrity
    ok, report = verify_chain("logs/prod-a-deploy.jsonl")
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Hash chain ────────────────────────────────────────────────────────────────

def chain_log_file(log_path: str, output_path: Optional[str] = None) -> int:
    """
    Read a JSONL log file and rewrite it with hash chain.
    Each entry gains a 'prev_hash' field linking to the hash of the previous entry.
    First entry has prev_hash = '0' * 64 (genesis).

    Returns number of entries processed.
    Writes to output_path if specified, otherwise overwrites input.
    """
    path = Path(log_path)
    if not path.exists():
        return 0

    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    prev_hash = "0" * 64
    chained   = []
    for entry in entries:
        entry_clean = {k: v for k, v in entry.items() if k != "prev_hash"}
        entry_str   = json.dumps(entry_clean, sort_keys=True)
        entry_hash  = _sha256(entry_str)
        entry_clean["prev_hash"]   = prev_hash
        entry_clean["entry_hash"]  = entry_hash
        chained.append(entry_clean)
        prev_hash = entry_hash

    out_path = Path(output_path or log_path)
    out_path.write_text("\n".join(json.dumps(e) for e in chained) + "\n")
    return len(chained)


def verify_chain(log_path: str) -> tuple:
    """
    Verify the hash chain integrity of a JSONL log file.
    Returns (ok: bool, report: str).
    """
    path = Path(log_path)
    if not path.exists():
        return False, f"Log file not found: {log_path}"

    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if not entries:
        return True, "Empty log file — no entries to verify"

    first = entries[0]
    if "prev_hash" not in first:
        return False, "Log file has no hash chain (run chain_log_file first)"

    failures = []
    prev_hash = "0" * 64

    for i, entry in enumerate(entries):
        stored_prev  = entry.get("prev_hash", "")
        stored_hash  = entry.get("entry_hash", "")
        entry_clean  = {k: v for k, v in entry.items()
                        if k not in ("prev_hash", "entry_hash")}
        computed     = _sha256(json.dumps(entry_clean, sort_keys=True))

        if stored_prev != prev_hash:
            failures.append(
                f"Entry {i}: prev_hash mismatch — expected {prev_hash[:16]}..., "
                f"got {stored_prev[:16]}... (tampering detected)"
            )
        if stored_hash != computed:
            failures.append(
                f"Entry {i}: entry_hash mismatch — stored {stored_hash[:16]}..., "
                f"computed {computed[:16]}... (content modified)"
            )

        prev_hash = stored_hash or computed

    if failures:
        report = f"Chain INVALID — {len(failures)} integrity failure(s):\n"
        report += "\n".join(f"  • {f}" for f in failures)
        return False, report

    return True, f"Chain VALID — {len(entries)} entries verified, no tampering detected"


# ── CEF export ────────────────────────────────────────────────────────────────

_CEF_SEVERITY = {
    "INFO":     3,
    "WARN":     5,
    "ERROR":    7,
    "CRITICAL": 9,
    "MANUAL":   4,
}

_CEF_VENDOR  = "avi-fortiadc-migration"
_CEF_PRODUCT = "AVI-FortiADC-Migration"
_CEF_VERSION = "0.5.0"


def export_cef(log_path: str, output_path: Optional[str] = None) -> int:
    """
    Export JSONL log to CEF (Common Event Format) for SIEM ingestion.
    CEF is accepted natively by ArcSight, Splunk (with TA), and most SIEMs.

    Format: CEF:0|Vendor|Product|Version|EventID|Name|Severity|Extension

    Returns number of entries exported.
    """
    path = Path(log_path)
    if not path.exists():
        return 0

    cef_lines = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        level     = entry.get("level", "INFO")
        phase     = entry.get("phase", "unknown")
        message   = entry.get("message", "").replace("|", "/").replace("=", ":")
        timestamp = entry.get("timestamp", "")
        obj_type  = entry.get("object_type", "")
        obj_name  = entry.get("object_name", "")
        env       = entry.get("env", "")
        severity  = _CEF_SEVERITY.get(level, 3)
        event_id  = f"MIGRATION_{phase.upper()}_{level}"

        ext_parts = [
            f"rt={timestamp}",
            f"act={phase}",
            f"outcome={level}",
        ]
        if obj_type:
            ext_parts.append(f"cs1={obj_type}")
            ext_parts.append("cs1Label=objectType")
        if obj_name:
            ext_parts.append(f"cs2={obj_name}")
            ext_parts.append("cs2Label=objectName")
        if env:
            ext_parts.append(f"cs3={env}")
            ext_parts.append("cs3Label=environment")

        cef = (
            f"CEF:0|{_CEF_VENDOR}|{_CEF_PRODUCT}|{_CEF_VERSION}|"
            f"{event_id}|{message}|{severity}|"
            + " ".join(ext_parts)
        )
        cef_lines.append(cef)

    out_path = Path(output_path or log_path.replace(".jsonl", ".cef"))
    out_path.write_text("\n".join(cef_lines) + "\n")
    return len(cef_lines)


# ── Log summary ───────────────────────────────────────────────────────────────

def summarise_log(log_path: str) -> dict:
    """
    Aggregate a JSONL log by level and phase.
    Returns dict suitable for reporting.
    """
    path = Path(log_path)
    if not path.exists():
        return {}

    counts: dict = {}
    phases: dict = {}

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        level = entry.get("level", "UNKNOWN")
        phase = entry.get("phase", "unknown")
        counts[level] = counts.get(level, 0) + 1
        phases[phase] = phases.get(phase, 0) + 1

    return {
        "file":   str(log_path),
        "counts": counts,
        "phases": phases,
        "total":  sum(counts.values()),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Audit log tools")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("chain",  help="Add hash chain to JSONL log")
    p.add_argument("log")
    p.add_argument("--output")

    p = sub.add_parser("verify", help="Verify hash chain integrity")
    p.add_argument("log")

    p = sub.add_parser("cef",    help="Export to CEF format")
    p.add_argument("log")
    p.add_argument("--output")

    p = sub.add_parser("summary", help="Summarise log by level/phase")
    p.add_argument("log")

    args = parser.parse_args()

    if args.cmd == "chain":
        n = chain_log_file(args.log, args.output)
        print(f"Hash chain added to {n} entries")
    elif args.cmd == "verify":
        ok, report = verify_chain(args.log)
        print(report)
        sys.exit(0 if ok else 1)
    elif args.cmd == "cef":
        n = export_cef(args.log, args.output)
        print(f"Exported {n} entries to CEF format")
    elif args.cmd == "summary":
        s = summarise_log(args.log)
        print(json.dumps(s, indent=2))
    else:
        parser.print_help()
