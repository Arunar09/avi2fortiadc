"""
reporters/decision_trace.py
Decision audit trace exporter.
"""
from __future__ import annotations

import json
from pathlib import Path


def export_decision_trace(env: str, state_dir: str = "state", output_path: str | None = None) -> str:
    manifest_path = Path(state_dir) / f"{env}-decision-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Decision manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    audit = manifest.get("audit", [])

    lines = [
        f"# Decision Trace Report — {env}",
        "",
        f"- Manifest status: `{manifest.get('status', 'draft')}`",
        f"- Manifest version: `{manifest.get('meta', {}).get('version', 'n/a')}`",
        f"- Snapshot hash: `{manifest.get('meta', {}).get('based_on_snapshot_hash', '')}`",
        f"- Manifest hash: `{manifest.get('meta', {}).get('manifest_hash', '')}`",
        "",
        "## Decision Audit Trail",
        "",
    ]
    if not audit:
        lines.append("- No decision entries recorded.")
    else:
        for i, entry in enumerate(audit, 1):
            lines.append(f"{i}. `{entry.get('timestamp','')}` `{entry.get('operator','')}` — **{entry.get('action','')}**")
            detail = entry.get("detail", {})
            if detail:
                lines.append(f"   - detail: `{json.dumps(detail, sort_keys=True)}`")

    out = Path(output_path or (Path("reports") / f"{env}-decision-trace.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)

