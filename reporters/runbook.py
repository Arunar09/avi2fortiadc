"""
reporters/runbook.py
Generates a detailed, GUI-driven manual migration runbook for FortiADC.
Reads from StateLedger (unsupported items) and the discovery JSON snapshot
to produce environment-specific, checklist-ready guidance.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.state_ledger import StateLedger, UnsupportedItem


# ── Severity & Effort tables ──────────────────────────────────────────────────

_SEVERITY_MAP: dict[str, tuple[str, str]] = {
    # object_type keyword → (severity_label, effort)
    "ssl_certificate":    ("High",   "20–30 mins per cert"),
    "sslkeyandcert":      ("High",   "20–30 mins per cert"),
    "vsdatascriptset":    ("High",   "30–60 mins per script set"),
    "gslb":               ("High",   "1–2 hours"),
    "ipaddrgroup":        ("Medium", "10 mins"),
    "networkprofile":     ("Medium", "15 mins"),
    "analyticsprofile":   ("Low",    "10 mins"),
    "applicationprofile": ("Medium", "20 mins"),
    "persistence":        ("Medium", "15 mins"),
    "waf":                ("High",   "1 hour"),
    "pkiprofile":         ("High",   "30 mins"),
    "sslprofile":         ("Medium", "20 mins"),
    "default":            ("Medium", "15 mins"),
}

_SEVERITY_ICON = {"High": "🔴", "Medium": "🟠", "Low": "🟡"}


def _classify(object_type: str) -> tuple[str, str]:
    """Return (severity_label, estimated_effort) for a given object type."""
    key = object_type.lower()
    for k, v in _SEVERITY_MAP.items():
        if k in key:
            return v
    return _SEVERITY_MAP["default"]


# ── Category-specific guidance blocks ────────────────────────────────────────

def _cert_block(item: UnsupportedItem, discovery: dict) -> list[str]:
    """Rich certificate guidance with Avi API export + OpenSSL conversion."""
    name = item.object_name
    cert_data = next(
        (c for c in discovery.get("ssl_certificates", [])
         if c.get("name") == name),
        {}
    )
    issuer  = cert_data.get("certificate", {}).get("issuer_distinguished_name",  "N/A")
    subject = cert_data.get("certificate", {}).get("subject_distinguished_name", "N/A")
    expiry  = cert_data.get("certificate", {}).get("not_after", "N/A")

    return [
        "**Certificate Details (from Avi discovery)**",
        f"| Field | Value |",
        f"|---|---|",
        f"| Issuer | `{issuer}` |",
        f"| Subject | `{subject}` |",
        f"| Expires | `{expiry}` |",
        "",
        "**Step-by-step export + import procedure**",
        "",
        "**Step 1 — Export certificate & key from Avi Controller:**",
        "```bash",
        f"# Export via Avi API (requires admin + 'include_key' permission)",
        f"curl -X GET 'https://{{AVI_CONTROLLER}}/api/sslkeyandcertificate?name={name}&include_key=true' \\",
        f"     -H 'X-Avi-Tenant: {{Tenant}}' -H 'X-Avi-Token: {{Token}}' \\",
        f"     -o {name}.json",
        "```",
        "",
        "**Step 2 — Extract PEM files from the JSON response:**",
        "```bash",
        f"python3 -c \"import json,sys; d=json.load(open('{name}.json')); open('{name}.crt','w').write(d['certificate']['certificate']); open('{name}.key','w').write(d['key']['private_key'])\"",
        "```",
        "",
        "**Step 3 — Convert PEM to PKCS12 for FortiADC:**",
        "```bash",
        f"openssl pkcs12 -export -out {name}.p12 -inkey {name}.key -in {name}.crt",
        "# Enter an export password when prompted — remember it for the import step",
        "```",
        "",
        "**Step 4 — Import into FortiADC:**",
        "Navigate to: `System > Certificate > Local Certificate > Import`",
        "- **Type:** Local Certificate",
        "- **Import Method:** PKCS12",
        f"- **Certificate Name:** `{name}`",
        f"- **Upload:** `{name}.p12`",
        "- **Password:** (the export password set in Step 3)",
        "",
    ]


def _datascript_block(item: UnsupportedItem, discovery: dict) -> list[str]:
    """DataScript guidance with event-specific FortiADC mapping."""
    name = item.object_name
    ds_set = next(
        (ds for ds in discovery.get("datascripts", [])
         if ds.get("name") == name),
        {}
    )
    scripts = ds_set.get("datascript", []) if isinstance(ds_set, dict) else []

    lines = []
    if scripts:
        lines += [
            "**Script Inventory (from Avi discovery)**",
            "",
            "| Event | Lines | Recommendation |",
            "|---|---|---|",
        ]
        for s in scripts:
            evt   = s.get("evt", "UNKNOWN")
            code  = str(s.get("script", ""))
            lcount = len(code.splitlines())
            # Recommend based on event type
            if evt in ("HTTP_REQ", "HTTP_RESP"):
                rec = "HTTP Content Rewriting Rule (`Server LB > Virtual Server > Rule`)"
            elif evt in ("HTTP_CONN", "HTTP_RESP_HDR"):
                rec = "HTTP Header Manipulation Profile"
            else:
                rec = "Lua Script (`Server LB > Scripting > Script`)"
            lines.append(f"| `{evt}` | {lcount} | {rec} |")

        lines += [
            "",
            "**FortiADC Implementation Paths:**",
            "- **HTTP_REQ / HTTP_RESP events** → `Server Load Balance > Virtual Server > (Edit) > Rule`",
            "  - Use Action Type: `Content Rewriting` for header/URL manipulation",
            "- **Complex logic (state machines, counters)** → `Server Load Balance > Scripting`",
            "  - Create a Lua script, then Reference it in the VS Rule",
            "",
        ]
    else:
        lines += [
            "> No script body available in discovery. Review the Avi DataScriptSet manually.",
            "- Navigate to: `Server Load Balance > Scripting` to create equivalent logic.",
            "",
        ]
    return lines


def _persistence_block(item: UnsupportedItem, discovery: dict) -> list[str]:
    name = item.object_name
    profile = next(
        (p for p in discovery.get("persistence_profiles", [])
         if p.get("name") == name),
        {}
    )
    ptype = profile.get("persistence_type", "UNKNOWN").replace("PERSISTENCE_TYPE_", "")
    return [
        f"**Avi Persistence Type:** `{ptype}`",
        "",
        "| Avi Type | FortiADC Equivalent | GUI Path |",
        "|---|---|---|",
        "| HTTP_COOKIE | Cookie Persistence | `Server LB > Persistence > Type: Cookie` |",
        "| APP_COOKIE (JSESSIONID) | HTTP Cookie Persistence | `Server LB > Persistence > Type: HTTP Cookie` |",
        "| CLIENT_IP | Source IP Persistence | `Server LB > Persistence > Type: Source IP` |",
        "| SSL_SESSION_ID | SSL Session ID Persistence | `Server LB > Persistence > Type: SSL Session ID` |",
        "",
        "**Steps:**",
        f"1. Go to `Server Load Balance > Persistence`, click **Create**.",
        f"2. Set **Name:** `{name}`, **Type:** (see table above for your Avi type `{ptype}`).",
        f"3. Assign this persistence profile to the corresponding VS under `Virtual Server > (Edit) > Persistence`.",
        "",
    ]


def _gslb_block(item: UnsupportedItem, discovery: dict) -> list[str]:
    return [
        "**FortiADC GSLB Configuration Path:** `Global Load Balance > Zone`",
        "",
        "**Required Steps:**",
        "1. **DNS Zone Setup:** `Global Load Balance > Zone > Create`",
        "   - Set the zone name to match your authoritative DNS domain.",
        "2. **Data Centers:** `Global Load Balance > Data Center > Create`",
        "   - One entry per physical/cloud site.",
        "3. **Servers:** `Global Load Balance > Servers > Create`",
        "   - Register each FortiADC that will serve requests.",
        "4. **Virtual Server Pool:** `Global Load Balance > Virtual Server Pool > Create`",
        "   - Add the FortiADC VS members you migrated automatically.",
        "5. **Host Record:** `Global Load Balance > Host > Create`",
        "   - Bind your VS Pool to the DNS record, set LB method (e.g., Round Robin).",
        "",
    ]


def _default_block(item: UnsupportedItem) -> list[str]:
    return [
        f"> **Action:** {item.action}",
        "",
        "> Review this item in the FortiADC documentation and configure via the relevant menu.",
        "",
    ]


def _item_checklist(
    idx: int,
    item: UnsupportedItem,
    discovery: dict,
) -> list[str]:
    """Render a full checklist block for a single unsupported item."""
    sev_label, effort = _classify(item.object_type)
    sev_icon = _SEVERITY_ICON[sev_label]
    key = item.object_type.lower()

    block = [
        f"### {idx}. `{item.object_name}`",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| **Object Type** | `{item.object_type}` |",
        f"| **Severity** | {sev_icon} {sev_label} |",
        f"| **Estimated Effort** | {effort} |",
        f"| **Reason Skipped** | {item.reason} |",
        f"| **Status** | {'✅ Resolved' if item.resolved else '⬜ Pending'} |",
        "",
    ]

    # Inject rich, category-specific content
    if "cert" in key:
        block += _cert_block(item, discovery)
    elif "script" in key:
        block += _datascript_block(item, discovery)
    elif "persist" in key:
        block += _persistence_block(item, discovery)
    elif "gslb" in key or ("dns" in key and "profile" not in key):
        block += _gslb_block(item, discovery)
    else:
        block += _default_block(item)

    # Per-item checklist
    block += [
        "**Completion Checklist:**",
        "- [ ] Reviewed item in Avi",
        "- [ ] Configured equivalent in FortiADC",
        "- [ ] Tested and validated",
        "- [ ] Marked resolved in ledger (`python migrate.py ledger resolve ...`)",
        "",
        "---",
        "",
    ]
    return block


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_manual_runbook(env_name: str) -> Path:
    """
    Build and write the manual migration runbook for *env_name*.
    Returns the path of the written file.
    """
    ledger = StateLedger(env_name)
    items  = ledger.unsupported_items(unresolved_only=True)

    # Load discovery JSON for enriched data (best-effort)
    discovery: dict[str, Any] = {}
    disc_path = Path("discovery") / f"{env_name}.json"
    if disc_path.exists():
        try:
            discovery = json.loads(disc_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not items:
        content = (
            f"# FortiADC Manual Migration Runbook — {env_name}\n\n"
            f"**Generated:** {now}\n\n"
            "## ✅ No Manual Actions Required\n\n"
            "All pipeline objects were successfully auto-translated into the FortiADC "
            "configuration. No GUI interventions are needed.\n"
        )
    else:
        # Statistics
        high_count   = sum(1 for i in items if _classify(i.object_type)[0] == "High")
        medium_count = sum(1 for i in items if _classify(i.object_type)[0] == "Medium")
        low_count    = sum(1 for i in items if _classify(i.object_type)[0] == "Low")

        # Group for top-level summary table
        grouped: dict[str, list[UnsupportedItem]] = defaultdict(list)
        for item in items:
            grouped[item.object_type].append(item)

        md: list[str] = [
            f"# FortiADC Manual Migration Runbook — {env_name}",
            "",
            f"**Environment:** `{env_name}`  ",
            f"**Generated:** {now}  ",
            f"**Tool:** Avi → FortiADC Migration Pipeline  ",
            "",
            "---",
            "",
            "## 📊 Executive Summary",
            "",
            "The automated pipeline translated all Virtual Services, Pools, Health Monitors, "
            "and standard Profiles that had deterministic FortiADC equivalents. "
            "The items below **could not be auto-migrated** due to platform architectural "
            "differences. This runbook provides step-by-step GUI navigation for each.",
            "",
            "| Metric | Count |",
            "|---|---|",
            f"| Total manual items | **{len(items)}** |",
            f"| 🔴 High severity | {high_count} |",
            f"| 🟠 Medium severity | {medium_count} |",
            f"| 🟡 Low severity | {low_count} |",
            "",
            "### Items by Category",
            "",
            "| Category | Count | Severity |",
            "|---|---|---|",
        ]

        for obj_type, obj_list in sorted(grouped.items()):
            sev_label, _ = _classify(obj_type)
            sev_icon = _SEVERITY_ICON[sev_label]
            md.append(f"| `{obj_type}` | {len(obj_list)} | {sev_icon} {sev_label} |")

        md += [
            "",
            "> **Prerequisite:** Complete all 🔴 High severity items before testing FortiADC traffic.",
            "",
            "---",
            "",
            "## 📋 Manual Action Items",
            "",
        ]

        # Render each item in severity order (High → Medium → Low)
        order = {"High": 0, "Medium": 1, "Low": 2}
        sorted_items = sorted(items, key=lambda i: order.get(_classify(i.object_type)[0], 9))

        for idx, item in enumerate(sorted_items, start=1):
            md += _item_checklist(idx, item, discovery)

        content = "\n".join(md)

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    out_path = reports_dir / f"{env_name}-manual-migration-runbook.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path
