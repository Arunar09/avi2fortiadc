"""reporters/rollback.py — Rollback script generator."""
from __future__ import annotations
from datetime import datetime, timezone


def generate_rollback_script(env_name: str, fortiadc_config: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vs_names = [obj["name"] for obj in fortiadc_config.get("virtual_servers", [])]

    lines = [
        "#!/usr/bin/env bash",
        f"# Avi → FortiADC Rollback Script",
        f"# Environment: {env_name}",
        f"# Generated: {now}",
        f"# USAGE: bash rollback.sh [--execute]",
        f"# Default is dry-run. Pass --execute to actually roll back.",
        "set -euo pipefail",
        "",
        "DRY_RUN=true",
        '[[ "${1:-}" == "--execute" ]] && DRY_RUN=false',
        "",
        'echo "Avi → FortiADC Rollback"',
        'echo "Environment: ' + env_name + '"',
        '$DRY_RUN && echo "[DRY-RUN] No changes will be made. Pass --execute to proceed."',
        "",
        "# Step 1: Disable FortiADC virtual servers (stop serving traffic)",
        'echo "Step 1: Disabling FortiADC virtual servers..."',
    ]

    for vs in vs_names[:10]:
        lines.append(f'# Disable VS: {vs}')
        lines.append(f'$DRY_RUN || fortiadc_cli "config load-balance virtual-server\\n'
                     f'  edit {vs}\\n  set status disable\\n  end"')
        lines.append(f'$DRY_RUN && echo "[DRY-RUN] Would disable FortiADC VS: {vs}"')

    lines += [
        "",
        "# Step 2: Point DNS back to Avi VIPs",
        'echo "Step 2: Revert DNS to Avi VIPs..."',
        'echo "  ACTION: Update DNS records to point back to Avi VIPs."',
        '  echo "  This step is manual — update DNS/Infoblox records."',
        '  echo "  Avi VIPs are preserved in discovery/<env>.json"',
        "",
        "# Step 3: Verify Avi is still responsive",
        'echo "Step 3: Verifying Avi health..."',
        'echo "  Run: contrail_status on Avi controllers"',
        "",
        '$DRY_RUN && echo "[DRY-RUN] Rollback simulation complete."',
        '$DRY_RUN || echo "Rollback complete. Verify Avi is handling traffic."',
    ]

    return "\n".join(lines)
