"""
reporters/html.py
Self-contained HTML migration report — no external CSS/JS dependencies.
Includes: summary dashboard, VS table, compatibility matrix, manual items,
certificate audit, connection map, technical pattern analysis, next steps.
"""
from __future__ import annotations
from datetime import datetime, timezone
from core.events import EventBus, Level
from core.intelligence import Pattern, score_migration_complexity


def generate_html(env_name: str, bus: EventBus, discovery: dict,
                  vs_profiles: dict, compat_results: list,
                  patterns: list[Pattern], complexity: dict,
                  next_steps: list[str]) -> str:

    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    auto    = sum(1 for p in vs_profiles.values() if p.can_auto_migrate)
    manual_vs = len(vs_profiles) - auto

    def badge(text, color):
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700">{text}</span>'

    def row(label, value, color="#e2e4ed"):
        return f'<tr><td style="padding:8px 12px;color:#8b8fa8">{label}</td><td style="padding:8px 12px;color:{color};font-weight:600">{value}</td></tr>'

    level_colors = {
        Level.INFO.value: "#2ecc71", Level.WARN.value: "#f39c12",
        Level.ERROR.value: "#e74c3c", Level.CRITICAL.value: "#9b59b6",
        Level.MANUAL.value: "#3498db",
    }
    from analyzers.compatibility import Compat
    status_counts = {status.value: 0 for status in Compat}
    for result in compat_results:
        # Resolve status value (handles both Enum and raw string from ledger)
        s_val = result.status.value if hasattr(result.status, "value") else str(result.status)
        status_counts[s_val] = status_counts.get(s_val, 0) + 1
    
    # Add Technical Patterns to status counts for comprehensive summary
    for p in patterns:
        if p.severity == "CRITICAL":
            status_counts["BLOCKED"] = status_counts.get("BLOCKED", 0) + 1
        elif p.severity == "WARN":
            status_counts["WARN"] = status_counts.get("WARN", 0) + 1

    # Build events table rows
    manual_rows = ""
    # Filter for items needing manual attention
    manual_items = [r for r in compat_results if (
        (hasattr(r.status, "value") and r.status in (Compat.MANUAL, Compat.BLOCKED, Compat.WARN)) or
        (isinstance(r.status, str) and r.status in ("MANUAL", "BLOCKED", "WARN"))
    )]
    
    for result in manual_items:
        s_val = result.status.value if hasattr(result.status, "value") else str(result.status)
        level = "CRITICAL" if s_val == "BLOCKED" else ("MANUAL" if s_val == "MANUAL" else "WARN")
        col = level_colors.get(level, "#888")
        action = "Review object mapping and decide manual remediation before transform."
        if s_val == "BLOCKED":
            action = "This object blocks automated migration. Resolve or explicitly defer before proceeding."
        elif s_val == "WARN":
            action = "Review this approximation and confirm the translated behavior is acceptable."
        manual_rows += (
            f'<tr style="border-bottom:1px solid #2e3147">'
            f'<td style="padding:8px">{badge(level, col)}</td>'
            f'<td style="padding:8px;font-family:monospace;font-size:13px">'
            f'{result.object_type}/{result.object_name}</td>'
            f'<td style="padding:8px">{result.reason}</td>'
            f'<td style="padding:8px;color:#8b8fa8;font-size:12px">{action[:120]}</td>'
            f'</tr>'
        )

    # VS table rows
    status_colors = {"AUTO":"#2ecc71","WARN":"#f39c12","MANUAL":"#3498db","BLOCKED":"#e74c3c"}
    vs_rows = ""
    for r in compat_results[:100]:
        s_val = r.status.value if hasattr(r.status, "value") else str(r.status)
        col = status_colors.get(s_val, "#888")
        assoc_html = ""
        if r.associations:
            assoc_html = f'<div style="margin-top:4px;font-size:11px;color:#abb2bf">Linked: {", ".join(r.associations)}</div>'
        
        vs_rows += (
            f'<tr style="border-bottom:1px solid #1a1d27">'
            f'<td style="padding:7px 10px;font-family:monospace;font-size:13px">{r.object_name}{assoc_html}</td>'
            f'<td style="padding:7px 10px">{badge(r.status.value, col)}</td>'
            f'<td style="padding:7px 10px;color:#8b8fa8;font-size:12px">{r.reason[:80]}</td>'
            f'</tr>'
        )

    # Patterns
    pat_rows = ""
    for p in patterns:
        sev_col = {"CRITICAL":"#e74c3c","WARN":"#f39c12","INFO":"#2ecc71"}.get(p.severity,"#888")
        pat_rows += (
            f'<div style="background:#1a1d27;border-left:4px solid {sev_col};'
            f'padding:14px;margin-bottom:10px;border-radius:0 6px 6px 0">'
            f'<div style="font-weight:700;margin-bottom:6px">'
            f'{badge(p.severity,sev_col)} &nbsp; [{p.id}] {p.name}</div>'
            f'<div style="color:#8b8fa8;font-size:13px;margin-bottom:8px">{p.explanation}</div>'
            f'<div style="font-size:13px;white-space:pre-line">'
            f'<strong>Recommendation:</strong> {p.recommendation}</div>'
            + (f'<div style="margin-top:8px;font-size:12px;color:#8b8fa8">'
               f'Affected: {", ".join(p.affected[:5])}{"..." if len(p.affected)>5 else ""}</div>'
               if p.affected else "") +
            f'</div>'
        )

    # Next steps list
    steps_html = "".join(
        f'<li style="margin-bottom:10px;padding:10px;background:#1a1d27;'
        f'border-radius:6px;font-size:13px">{step}</li>'
        for step in next_steps
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Avi→FortiADC Migration — {env_name}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f1117;color:#e2e4ed;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;line-height:1.6;padding:32px}}
h1{{font-size:22px;margin-bottom:4px}}
h2{{font-size:16px;font-weight:700;margin:28px 0 12px;color:#4f8ef7}}
.meta{{color:#8b8fa8;font-size:13px;margin-bottom:28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:28px}}
.card{{background:#1a1d27;border:1px solid #2e3147;border-radius:8px;padding:16px;text-align:center}}
.card .num{{font-size:32px;font-weight:800;margin-bottom:4px}}
.card .lbl{{color:#8b8fa8;font-size:12px}}
table{{width:100%;border-collapse:collapse;background:#1a1d27;border-radius:8px;overflow:hidden;margin-bottom:20px}}
th{{background:#252837;padding:10px 12px;text-align:left;color:#8b8fa8;font-size:12px;font-weight:600}}
ol{{padding-left:20px}}
</style></head>
<body>
<h1>Avi → FortiADC Migration Report</h1>
<div class="meta">Environment: <strong>{env_name}</strong> &nbsp;|&nbsp; Generated: {now} &nbsp;|&nbsp; Tool v0.1.0</div>


<h2>Summary</h2>
<div class="grid">
  <div class="card"><div class="num" style="color:#4f8ef7">{len(vs_profiles)}</div><div class="lbl">Virtual Services</div></div>
  <div class="card"><div class="num" style="color:#2ecc71">{auto}</div><div class="lbl">Auto-Migratable</div></div>
  <div class="card"><div class="num" style="color:#3498db">{manual_vs}</div><div class="lbl">Manual Required</div></div>
  <div class="card"><div class="num" style="color:#e74c3c">{status_counts.get("BLOCKED", 0)}</div><div class="lbl">Critical Blockers</div></div>
  <div class="card"><div class="num" style="color:#3498db">{status_counts.get("MANUAL", 0)}</div><div class="lbl">Manual Items</div></div>
  <div class="card"><div class="num" style="color:#f39c12">{status_counts.get("WARN", 0)}</div><div class="lbl">Warnings</div></div>
</div>

<h2>Technical Pattern Analysis ({len(patterns)} patterns detected)</h2>
{pat_rows if pat_rows else '<div style="color:#8b8fa8">No significant patterns detected.</div>'}

<h2>Ordered Next Steps</h2>
<ol style="list-style:none;padding:0">{steps_html}</ol>

<h2>Manual Action Items & Critical Blockers</h2>
<table>
<thead><tr><th>Level</th><th>Object</th><th>Issue</th><th>Action Required</th></tr></thead>
<tbody>{manual_rows}</tbody>
</table>

<h2>Object Compatibility ({len(compat_results)} objects)</h2>
<table>
<thead><tr><th>Object Name</th><th>Status</th><th>Detail</th></tr></thead>
<tbody>{vs_rows}</tbody>
</table>

<h2>External Connections Discovered</h2>
<table>
<thead><tr><th>Name</th><th>Type</th><th>Action</th></tr></thead>
<tbody>{''.join(f'<tr><td style="padding:8px">{c.get("name","?")}</td><td style="padding:8px;color:#8b8fa8">{c.get("type","?")}</td><td style="padding:8px;font-size:12px;color:#f39c12">Reconfigure after migration</td></tr>' for c in discovery.get('connections',[]))}</tbody>
</table>

<div style="margin-top:40px;color:#8b8fa8;font-size:12px;border-top:1px solid #2e3147;padding-top:16px">
Avi → FortiADC Migration Tool v0.1.0 &nbsp;|&nbsp;
For LLM assistance: <code>migrate.py llm-pack full --env {env_name}</code>
</div>
</body></html>"""
