"""
reporters/console.py
Rich console output with progress tracking and colour-coded summaries.
"""
from __future__ import annotations
import sys
from core.events import EventBus, Level
from analyzers.dependency_graph import VSProfile
from analyzers.compatibility import CompatibilityResult, Compat

_C = {
    "red":    "\033[31m", "green":  "\033[32m",
    "yellow": "\033[33m", "cyan":   "\033[36m",
    "bold":   "\033[1m",  "reset":  "\033[0m",
}
USE_COL = sys.stdout.isatty()


def c(colour: str, text: str) -> str:
    return f"{_C[colour]}{text}{_C['reset']}" if USE_COL else text


def print_discovery_summary(discovery: dict, bus: EventBus) -> None:
    counts = bus.summary_counts()
    print(f"\n{'═'*60}")
    print(c("bold", "  DISCOVERY COMPLETE"))
    print(f"{'═'*60}")
    print(f"  Virtual Services  : {len(discovery.get('virtual_services', []))}")
    print(f"  Pools             : {len(discovery.get('pools', []))}")
    print(f"  SSL Certificates  : {len(discovery.get('ssl_certificates', []))}")
    print(f"  DataScripts       : {len(discovery.get('datascripts', []))}")
    print(f"  Connections found : {len(discovery.get('connections', []))}")
    print()
    print(f"  {c('green',  f'INFO:     {counts[Level.INFO.value]}')}   "
          f"{c('yellow', f'WARN:     {counts[Level.WARN.value]}')}")
    print(f"  {c('red',    f'ERROR:    {counts[Level.ERROR.value]}')}   "
          f"{c('red',    f'CRITICAL: {counts[Level.CRITICAL.value]}')}")
    print(f"  {c('cyan',   f'MANUAL:   {counts[Level.MANUAL.value]}')}  "
          f"(require human action before migration)")
    if bus.has_blockers:
        print(f"\n  {c('red', '🚫 BLOCKING ISSUES FOUND — see report for details')}")
    print(f"{'═'*60}\n")


def print_compatibility_matrix(results: list[CompatibilityResult]) -> None:
    counts = {s.value: 0 for s in Compat}
    for r in results:
        counts[r.status.value] += 1

    print(c("bold", "\nCOMPATIBILITY MATRIX"))
    print(f"{'─'*50}")
    col_map = {"AUTO": "green", "WARN": "yellow",
               "MANUAL": "cyan", "BLOCKED": "red"}
    for status, count in counts.items():
        bar = "█" * min(count, 40)
        print(f"  {c(col_map[status], f'{status:8s}')} {count:4d}  {bar}")
    print()

    blocked = [r for r in results if r.status == Compat.BLOCKED]
    if blocked:
        print(c("red", f"  BLOCKED items ({len(blocked)}) — must resolve before migration:"))
        for r in blocked[:10]:
            print(f"    🚫 [{r.object_type}] {r.object_name}: {r.reason}")
        if len(blocked) > 10:
            print(f"    ... and {len(blocked)-10} more (see HTML report)")
    print()


def print_vs_summary(vs_profiles: dict[str, VSProfile]) -> None:
    auto   = sum(1 for p in vs_profiles.values() if p.can_auto_migrate)
    manual = len(vs_profiles) - auto

    print(c("bold", "VIRTUAL SERVICE MIGRATION READINESS"))
    print(f"{'─'*50}")
    print(f"  Auto-migratable   : {c('green', str(auto))}")
    print(f"  Require manual    : {c('cyan',  str(manual))}")
    print()

    if manual:
        print(c("cyan", "  Manual items:"))
        for vs_name, p in vs_profiles.items():
            if not p.can_auto_migrate:
                print(f"    ⚠  {vs_name}")
                for blocker in p.blockers[:3]:
                    print(f"       • {blocker[:80]}")
    print()
