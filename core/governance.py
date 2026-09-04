"""
core/governance.py
Enterprise governance controls for the migration pipeline.

Enforces:
  1. Change Request (CR) — every deploy phase requires an approved CR ID
  2. Segregation of Duties (SoD) — operator cannot also be approver
  3. Approval gating — deploy requires recorded approval before execution
  4. Maintenance window recording — start/end timestamps stored in governance record

Design:
  - Governance checks enforced in migrate.py before cmd_deploy() runs
  - Wizard surfaces checks interactively
  - All approvals written to state/<env>-governance.json (append-only)
  - Can be bypassed with --override-governance flag + mandatory justification text
  - CI/automation: reads from environment variables

Air-gap note:
  CR validation is format-only — tool cannot call ServiceNow or Jira.
  CR ID is recorded and verified against approval record by human approver
  who signs the ENTERPRISE-VALIDATION-CHECKLIST.md.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operator() -> str:
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))


ROLES = {
    "operator":  "Runs the tool — discovery, transform, dry-run, deploy",
    "reviewer":  "Reviews analysis report, signs off on migration plan",
    "approver":  "Authorises live deployment — must differ from operator",
    "auditor":   "Read-only — can inspect state ledger and logs",
}


@dataclass
class GovernanceRecord:
    phase:           str
    cr_id:           str
    operator:        str
    approver:        str
    approved_at:     str
    env:             str
    justification:   str = ""
    override:        bool = False
    window_start:    str = ""
    window_end:      str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class GovernanceStore:
    def __init__(self, env: str, state_dir: str = "state"):
        self._path = Path(state_dir) / f"{env}-governance.json"
        self._path.parent.mkdir(exist_ok=True)
        self._env  = env

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"env": self._env, "records": []}

    def save_record(self, record: GovernanceRecord) -> None:
        data = self._load()
        data["records"].append(record.to_dict())
        self._path.write_text(json.dumps(data, indent=2))

    def get_records(self, phase: Optional[str] = None) -> list:
        data = self._load()
        records = data.get("records", [])
        if phase:
            records = [r for r in records if r.get("phase") == phase]
        return records

    def latest_cr_for_phase(self, phase: str) -> Optional[str]:
        records = self.get_records(phase)
        return records[-1].get("cr_id") if records else None

    def has_approval_for_cr(self, phase: str, cr_id: str) -> bool:
        return any(r.get("cr_id") == cr_id for r in self.get_records(phase))


def validate_cr_id(cr_id: str) -> bool:
    """Format-validates a change request ID. Adjust patterns to match your ITSM."""
    patterns = [
        r"^CHG\d{7}$",
        r"^INC\d{7}$",
        r"^CR-\d{4}-\d{3,6}$",
        r"^RFC-\d{4,8}$",
        r"^[A-Z]+-\d{3,8}$",
        r"^CHANGE-\d{4,8}$",
    ]
    return any(re.match(p, cr_id.strip().upper()) for p in patterns)


def validate_sod(operator: str, approver: str) -> tuple:
    if not operator or not approver:
        return False, "Both operator and approver must be identified"
    if operator.lower().strip() == approver.lower().strip():
        return False, (
            f"Segregation of Duties violation: operator and approver are the same "
            f"person ({operator}). A different engineer must authorise the deployment."
        )
    return True, "SoD check passed"


@dataclass
class GovernanceCheckResult:
    passed:    bool
    cr_id:     str
    operator:  str
    approver:  str
    failures:  list
    record:    Optional[GovernanceRecord] = None


def run_governance_gate(
    env: str,
    phase: str,
    require_cr: bool = True,
    require_sod: bool = True,
    interactive: bool = True,
    override: bool = False,
    state_dir: str = "state",
) -> GovernanceCheckResult:
    """
    Run all governance checks before a deployment phase.

    Environment variables (for CI/non-interactive):
      MIGRATION_CR_ID              — change request ID
      MIGRATION_APPROVER           — approver username
      MIGRATION_WINDOW_START       — maintenance window start (ISO)
      MIGRATION_WINDOW_END         — maintenance window end (ISO)
      MIGRATION_OVERRIDE_JUSTIFICATION — justification when --override-governance used
    """
    operator = _operator()
    failures = []
    store    = GovernanceStore(env, state_dir)

    cr_id = os.environ.get("MIGRATION_CR_ID", "").strip()
    if not cr_id and interactive:
        cr_id = _prompt(f"\n  Change Request ID for '{env}' {phase}", hint="e.g. CHG0012345")

    if require_cr:
        if not cr_id:
            failures.append("Change Request ID is required but was not provided")
        elif not validate_cr_id(cr_id):
            failures.append(
                f"CR ID '{cr_id}' does not match expected format. "
                "Accepted: CHG0012345, INC0012345, CR-2026-001, INFRA-1234"
            )

    approver = os.environ.get("MIGRATION_APPROVER", "").strip()
    if not approver and interactive:
        approver = _prompt(
            f"  Approver username (must differ from operator '{operator}')",
            hint="e.g. jane.smith"
        )

    if require_sod and phase in ("deploy", "dns-cutover"):
        sod_ok, sod_reason = validate_sod(operator, approver)
        if not sod_ok:
            failures.append(sod_reason)

    window_start = os.environ.get("MIGRATION_WINDOW_START", "").strip()
    window_end   = os.environ.get("MIGRATION_WINDOW_END", "").strip()
    if not window_start and interactive and phase in ("deploy", "dns-cutover"):
        window_start = _prompt("  Maintenance window START (ISO or blank)", hint="2026-04-13T22:00Z")
        window_end   = _prompt("  Maintenance window END (ISO or blank)", hint="2026-04-14T00:00Z")

    if failures and override:
        justification = (
            _prompt("  OVERRIDE — provide written justification", required=True)
            if interactive
            else os.environ.get("MIGRATION_OVERRIDE_JUSTIFICATION", "no justification")
        )
        record = GovernanceRecord(
            phase=phase, cr_id=cr_id or "OVERRIDE", operator=operator,
            approver=approver or "OVERRIDE", approved_at=_now(), env=env,
            justification=justification, override=True,
            window_start=window_start, window_end=window_end,
        )
        store.save_record(record)
        print(f"\033[33m  ⚠  Governance override used. Justification recorded.\033[0m")
        return GovernanceCheckResult(True, cr_id, operator, approver, failures, record)

    if failures:
        return GovernanceCheckResult(False, cr_id, operator, approver, failures)

    record = GovernanceRecord(
        phase=phase, cr_id=cr_id, operator=operator, approver=approver,
        approved_at=_now(), env=env, window_start=window_start, window_end=window_end,
    )
    store.save_record(record)
    return GovernanceCheckResult(True, cr_id, operator, approver, [], record)


def _prompt(label: str, hint: str = "", required: bool = False) -> str:
    suffix = f" ({hint})" if hint else ""
    while True:
        try:
            raw = input(f"{label}{suffix}: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)
        if raw or not required:
            return raw
        print("  This field is required.")


def print_governance_summary(result: GovernanceCheckResult) -> None:
    G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; E = "\033[0m"; B = "\033[1m"
    print()
    if result.passed:
        print(f"  {G}✔{E}  Governance checks passed")
        print(f"     CR ID    : {result.cr_id}")
        print(f"     Operator : {result.operator}")
        print(f"     Approver : {result.approver}")
        if result.record and result.record.override:
            print(f"     {R}{B}OVERRIDE ACTIVE{E} — justification recorded")
    else:
        print(f"  {R}✖  Governance checks FAILED — deployment blocked{E}")
        for f in result.failures:
            print(f"     • {f}")
        print(f"\n  {Y}To proceed:{E}")
        print("  1. Raise a Change Request if you haven't already")
        print("  2. Ensure approver is a different person from the operator")
        print("  3. Re-run the deploy command")
