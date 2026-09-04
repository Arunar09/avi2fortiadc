"""
core/state_ledger.py
Persistent state management for the migration pipeline.

Tracks:
  - Which phases completed per environment
  - Unsupported/untranslatable items per environment
  - Confidence scores per translated object
  - Full audit trail with timestamps and operator sign-off
  - Replay safety — same input always produces same output

Design: All state lives in state/<env>-ledger.json (human-readable).
The ledger is append-only for audit events. Phase state is mutable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operator() -> str:
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))


# ── Unsupported item ──────────────────────────────────────────────────────────

@dataclass
class UnsupportedItem:
    """Tracks every AVI config item that has no FortiADC equivalent."""
    object_type:  str
    object_name:  str
    reason:       str
    action:       str               # what the engineer must do manually
    severity:     str = "MANUAL"   # MANUAL | BLOCKED
    avi_value:    str = ""          # the raw AVI value that could not map
    forti_note:   str = ""          # FortiADC limitation note
    resolved:     bool = False
    resolved_by:  str = ""
    resolved_at:  str = ""
    resolved_note:str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "UnsupportedItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Translated object record ──────────────────────────────────────────────────

@dataclass
class TranslatedObject:
    """Record of one successfully translated AVI → FortiADC object."""
    object_type:    str
    avi_name:       str
    forti_name:     str
    confidence:     float           # 0.0–1.0
    approximations: list[str] = field(default_factory=list)  # fields that used closest-match
    translated_at:  str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TranslatedObject":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Audit event ───────────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """Immutable audit log entry."""
    timestamp:  str
    operator:   str
    phase:      str
    action:     str
    detail:     dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Phase state ───────────────────────────────────────────────────────────────

@dataclass
class PhaseState:
    name:        str
    status:      str = "pending"    # pending | running | done | failed
    started_at:  str = ""
    finished_at: str = ""
    operator:    str = ""
    notes:       str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PhaseState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Main ledger ────────────────────────────────────────────────────────────────

KNOWN_PHASES = [
    "discover", "analyse", "transform",
    "dry-run", "deploy", "parallel-run", "dns-cutover"
]


class StateLedger:
    """
    Per-environment state ledger.
    Persisted to state/<env>-ledger.json after every mutation.
    Thread-safe for single-process use (no concurrent writes expected).
    """

    def __init__(self, env: str, state_dir: str = "state"):
        self.env       = env
        self.state_dir = state_dir
        
        # 1. Resolve path (Subdirectory priority)
        from services.common import resolve_env_file, ensure_env_dir
        
        # We look for organized file first
        target_file = resolve_env_file(state_dir, env, "-ledger.json")
        
        if not target_file:
            # New environment: create subdirectory
            dir_path = ensure_env_dir(state_dir, env)
            self._path = dir_path / "ledger.json"
        else:
            # Existing: If it was a flat file, we migrade it to subdirectory now
            if target_file.parent == Path(state_dir):
                dir_path = ensure_env_dir(state_dir, env)
                new_path = dir_path / "ledger.json"
                try:
                    import shutil
                    shutil.move(str(target_file), str(new_path))
                    self._path = new_path
                except Exception:
                    self._path = target_file # fallback if move fails
            else:
                self._path = target_file
                
        self._data = self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "env":          self.env,
            "created_at":   _now(),
            "phases":       {p: PhaseState(name=p).to_dict() for p in KNOWN_PHASES},
            "unsupported":  [],
            "translated":   [],
            "audit":        [],
        }

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")

    # ── Phase management ─────────────────────────────────────────────────────

    def phase_start(self, phase: str) -> None:
        self._data["phases"].setdefault(phase, {})
        self._data["phases"][phase].update({
            "status":     "running",
            "started_at": _now(),
            "operator":   _operator(),
        })
        self._audit(phase, "phase_start")
        self._save()

    def phase_done(self, phase: str, notes: str = "") -> None:
        self._data["phases"].setdefault(phase, {})
        self._data["phases"][phase].update({
            "status":      "done",
            "finished_at": _now(),
            "notes":       notes,
        })
        self._audit(phase, "phase_done", {"notes": notes})
        self._save()

    def phase_failed(self, phase: str, reason: str = "") -> None:
        self._data["phases"].setdefault(phase, {})
        self._data["phases"][phase].update({
            "status":      "failed",
            "finished_at": _now(),
            "notes":       reason,
        })
        self._audit(phase, "phase_failed", {"reason": reason})
        self._save()

    def is_done(self, phase: str) -> bool:
        return self._data["phases"].get(phase, {}).get("status") == "done"

    def phase_status(self, phase: str) -> str:
        return self._data["phases"].get(phase, {}).get("status", "pending")

    # ── Unsupported tracker ──────────────────────────────────────────────────

    def add_unsupported(
        self,
        object_type: str,
        object_name: str,
        reason: str,
        action: str,
        severity: str = "MANUAL",
        avi_value: str = "",
        forti_note: str = "",
    ) -> None:
        """Record an AVI object that cannot be translated automatically."""
        # Deduplicate by type+name
        existing = [
            i for i in self._data["unsupported"]
            if i["object_type"] == object_type and i["object_name"] == object_name
        ]
        if existing:
            return  # already recorded

        item = UnsupportedItem(
            object_type=object_type,
            object_name=object_name,
            reason=reason,
            action=action,
            severity=severity,
            avi_value=avi_value,
            forti_note=forti_note,
        )
        self._data["unsupported"].append(item.to_dict())
        self._audit("transform", "unsupported_item", {
            "object_type": object_type,
            "object_name": object_name,
            "reason": reason,
            "severity": severity,
        })
        self._save()

        # Also write flat JSON file for easy consumption
        self._write_unsupported_flat()

    def resolve_unsupported(
        self, object_type: str, object_name: str, note: str
    ) -> bool:
        """Mark an unsupported item as resolved by the engineer."""
        for item in self._data["unsupported"]:
            if item["object_type"] == object_type and item["object_name"] == object_name:
                item["resolved"]      = True
                item["resolved_by"]   = _operator()
                item["resolved_at"]   = _now()
                item["resolved_note"] = note
                self._save()
                self._write_unsupported_flat()
                return True
        return False

    def unsupported_items(self, unresolved_only: bool = False) -> list[UnsupportedItem]:
        items = [UnsupportedItem.from_dict(i) for i in self._data["unsupported"]]
        if unresolved_only:
            items = [i for i in items if not i.resolved]
        return items

    def blocked_count(self) -> int:
        return sum(1 for i in self._data["unsupported"]
                   if i.get("severity") == "BLOCKED" and not i.get("resolved"))

    def manual_count(self) -> int:
        return sum(1 for i in self._data["unsupported"]
                   if i.get("severity") == "MANUAL" and not i.get("resolved"))

    def _write_unsupported_flat(self) -> None:
        out = self._path.parent / "unsupported.json"
        out.write_text(json.dumps(self._data["unsupported"], indent=2), encoding="utf-8")

    # ── Translated object tracker ────────────────────────────────────────────

    def add_translated(
        self,
        object_type: str,
        avi_name: str,
        forti_name: str,
        confidence: float = 1.0,
        approximations: list[str] | None = None,
    ) -> None:
        obj = TranslatedObject(
            object_type=object_type,
            avi_name=avi_name,
            forti_name=forti_name,
            confidence=confidence,
            approximations=approximations or [],
        )
        self._data["translated"].append(obj.to_dict())
        self._save()

    def translated_objects(self) -> list[TranslatedObject]:
        return [TranslatedObject.from_dict(o) for o in self._data["translated"]]

    def low_confidence_items(self, threshold: float = 0.8) -> list[TranslatedObject]:
        return [o for o in self.translated_objects() if o.confidence < threshold]

    # ── Audit ────────────────────────────────────────────────────────────────

    def _audit(self, phase: str, action: str, detail: dict | None = None) -> None:
        event = AuditEvent(
            timestamp=_now(),
            operator=_operator(),
            phase=phase,
            action=action,
            detail=detail or {},
        )
        self._data["audit"].append(event.to_dict())

    def audit_log(self) -> list[AuditEvent]:
        return [AuditEvent(**e) for e in self._data["audit"]]

    # ── Summary ──────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        translated = self.translated_objects()
        low_conf   = self.low_confidence_items()
        unsup      = self.unsupported_items()
        return {
            "env":              self.env,
            "phases":           {p: self.phase_status(p) for p in KNOWN_PHASES},
            "translated_count": len(translated),
            "low_confidence":   len(low_conf),
            "unsupported":      len(unsup),
            "blocked":          self.blocked_count(),
            "manual":           self.manual_count(),
            "audit_events":     len(self._data["audit"]),
        }

    def print_summary(self) -> None:
        s = self.summary()
        print(f"\n  State ledger: {self._path}")
        print(f"  ─────────────────────────────────")
        for phase in KNOWN_PHASES:
            status = s["phases"][phase]
            icon = {"done": "✔", "running": "⟳", "failed": "✖", "pending": "○"}.get(status, "?")
            print(f"  {icon}  {phase:<20} {status}")
        print(f"  ─────────────────────────────────")
        print(f"  Translated    : {s['translated_count']}")
        print(f"  Low confidence: {s['low_confidence']}")
        print(f"  Unsupported   : {s['unsupported']}")
        print(f"  Blocked       : {s['blocked']}")
        print(f"  Manual        : {s['manual']}")


# ── Convenience factory ───────────────────────────────────────────────────────

def get_ledger(env: str) -> StateLedger:
    return StateLedger(env)
