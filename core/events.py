"""
core/events.py
Structured event system — every operation emits a MigrationEvent.
Provides: console output, JSONL log file, and sanitized LLM-safe output.

Design principle: nothing is silently dropped. Every INFO, WARN, ERROR,
CRITICAL, and MANUAL item is recorded and surfaced to the engineer.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class Level(str, Enum):
    INFO     = "INFO"
    WARN     = "WARN"
    ERROR    = "ERROR"
    CRITICAL = "CRITICAL"
    MANUAL   = "MANUAL"     # requires human action — cannot be automated


class Phase(str, Enum):
    CONNECT   = "CONNECT"
    COLLECT   = "COLLECT"
    ANALYSE   = "ANALYSE"
    TRANSFORM = "TRANSFORM"
    VALIDATE  = "VALIDATE"
    DEPLOY    = "DEPLOY"
    VERIFY    = "VERIFY"


# ANSI colours for console (auto-disabled if not a TTY)
_COLOURS = {
    Level.INFO:     "\033[32m",   # green
    Level.WARN:     "\033[33m",   # yellow
    Level.ERROR:    "\033[31m",   # red
    Level.CRITICAL: "\033[35m",   # magenta
    Level.MANUAL:   "\033[36m",   # cyan
}
_RESET = "\033[0m"
_BOLD  = "\033[1m"
_USE_COLOUR = sys.stdout.isatty()


# ── Sanitiser ────────────────────────────────────────────────────────────────
# Replace environment-specific values with generic placeholders.
# Sanitized output is intended for external analysis, but must still be reviewed before sharing.

_SANITISE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Private key / certificate blocks.
    (re.compile(r"-----BEGIN [^-\n]+-----.*?-----END [^-\n]+-----", re.IGNORECASE | re.DOTALL),
     lambda m, _: "[KEY_MATERIAL_REDACTED]"),

    # Credentials embedded in URLs.
    (re.compile(r"(https?://)([^:/\s]+):([^@\s]+)@", re.IGNORECASE),
     lambda m, _: f"{m.group(1)}[USER_REDACTED]:[PASSWORD_REDACTED]@"),

    # Common authentication headers and cookies.
    (re.compile(r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+"),
     lambda m, _: f"{m.group(1)}: [CREDENTIAL_REDACTED]"),

    # JWT-like bearer values.
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
     lambda m, _: "[JWT_REDACTED]"),

    # Common secret/token query parameters.
    (re.compile(r"([?&](?:token|access_token|api[_-]?key|secret|password|passwd|credential|auth)=)[^&#\s]+", re.IGNORECASE),
     lambda m, _: f"{m.group(1)}[REDACTED]"),

    # IPv6 addresses (with optional CIDR).
    (re.compile(r"(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?:/\d{1,3})?(?![A-Za-z0-9])"),
     lambda m, _c={}: f"[IP6_{_c.setdefault(m.group().lower(), len(_c) + 1)}]"),

    # IPv4 addresses (keep structure, replace octets)
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"),
     lambda m, _c={}: f"[IP_{_c.setdefault(m.group(), len(_c) + 1)}]"),

    # UUIDs
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                re.IGNORECASE),
     lambda m, _c={}: f"[UUID_{_c.setdefault(m.group().lower(), len(_c) + 1)}]"),

    # Hostnames / FQDNs (anything with dots that looks like a hostname)
    (re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.){2,}"
                r"[a-zA-Z]{2,}\b"),
     lambda m, _c={}: f"[HOSTNAME_{_c.setdefault(m.group().lower(), len(_c) + 1)}]"),

    # Passwords / tokens in JSON-like context, including common API-key names.
    (re.compile(r'"(?:password|passwd|token|access_token|api[_-]?key|secret|client_secret|private_key|credential)"\s*:\s*"[^"]*"',
                re.IGNORECASE),
     lambda m, _: f'{m.group().split(":")[0]}: "[REDACTED]"'),

    # Unquoted key/value forms commonly emitted in logs.
    (re.compile(r'(?i)\b(?:password|passwd|token|access_token|api[_-]?key|secret|client_secret|private_key|credential)\s*[:=]\s*[^\s,;]+'),
     lambda m, _: "[CREDENTIAL_REDACTED]"),

    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
     lambda m, _: "Bearer [TOKEN_REDACTED]"),
]


def sanitize(text: str) -> str:
    """
    Replace environment-specific values with generic placeholders.
    Counter-based so the same IP always gets the same [IP_N] placeholder
    — making the sanitized output coherent when pasted into an LLM.
    """
    # Each pattern gets its own counter dict (stateless per call)
    result = text
    for pattern, replacer in _SANITISE_PATTERNS:
        counter: dict = {}
        result = pattern.sub(lambda m: replacer(m, counter), result)
    return result


# ── Event ────────────────────────────────────────────────────────────────────

@dataclass
class MigrationEvent:
    level:           Level
    phase:           Phase
    message:         str
    object_type:     str = ""
    object_name:     str = ""
    object_uuid:     str = ""
    detail:          dict[str, Any] = field(default_factory=dict)
    action_required: bool = False
    blocking:        bool = False
    timestamp:       str  = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def sanitized(self) -> str:
        """Sanitized representation; review before external sharing."""
        parts = [
            f"[{self.level.value}] [{self.phase.value}]",
            f"Type: {self.object_type}" if self.object_type else "",
            f"Name: {sanitize(self.object_name)}" if self.object_name else "",
            f"Message: {sanitize(self.message)}",
        ]
        if self.detail:
            safe_detail = sanitize(json.dumps(self.detail, indent=2))
            parts.append(f"Detail:\n{safe_detail}")
        if self.action_required:
            parts.append("⚠ ACTION REQUIRED")
        if self.blocking:
            parts.append("🚫 BLOCKING — migration cannot proceed until resolved")
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sanitized"] = self.sanitized
        return d

    def to_sanitized_dict(self) -> dict:
        """Return an artifact-safe event record without raw secret-bearing fields."""
        d = asdict(self)
        d["message"] = sanitize(self.message)
        d["object_name"] = sanitize(self.object_name)
        d["object_uuid"] = sanitize(self.object_uuid)
        d["detail"] = json.loads(sanitize(json.dumps(self.detail, default=str))) if self.detail else {}
        d["sanitized"] = self.sanitized
        return d

    def console_line(self) -> str:
        colour = _COLOURS.get(self.level, "") if _USE_COLOUR else ""
        reset  = _RESET if _USE_COLOUR else ""
        bold   = _BOLD  if _USE_COLOUR else ""

        prefix = f"{colour}{bold}[{self.level.value:8s}]{reset}"
        loc    = f"[{self.phase.value}]"
        obj    = f" {self.object_type}/{self.object_name}" if self.object_name else ""
        flags  = ""
        if self.blocking:        flags += " 🚫BLOCKING"
        elif self.action_required: flags += " ⚠ACTION"

        return f"{prefix} {loc}{obj} — {self.message}{flags}"


# ── Event bus ────────────────────────────────────────────────────────────────

class EventBus:
    """
    Central event collector.
    - Prints structured console output immediately
    - Appends every event to a JSONL log file
    - Accumulates events for report generation
    """

    def __init__(self, log_path: Optional[Path] = None, verbose: bool = True):
        self._events:  list[MigrationEvent] = []
        self._log_path = log_path
        self._verbose  = verbose
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_path, "a", encoding="utf-8")
        else:
            self._log_file = None

    def emit(self, event: MigrationEvent, **kwargs) -> MigrationEvent:
        if kwargs and self._verbose:
            print(f"    [DEBUG] EventBus.emit received unexpected kwargs: {kwargs.keys()}")

        self._events.append(event)
        if self._verbose:
            print(event.console_line())
            # For CRITICAL/MANUAL: also print detail block
            if event.level in (Level.CRITICAL, Level.MANUAL, Level.ERROR) and event.detail:
                detail_str = json.dumps(event.detail, indent=4)
                for line in detail_str.splitlines():
                    print(f"    {line}")
        if self._log_file:
            self._log_file.write(json.dumps(event.to_sanitized_dict()) + "\n")
            self._log_file.flush()
        return event

    # ── Convenience emitters ─────────────────────────────────────────────────

    def info(self, phase: Phase, message: str, **kwargs) -> MigrationEvent:
        return self.emit(MigrationEvent(Level.INFO, phase, message, **kwargs))

    def warn(self, phase: Phase, message: str, action_required: bool = True,
             **kwargs) -> MigrationEvent:
        return self.emit(MigrationEvent(Level.WARN, phase, message,
                                        action_required=action_required, **kwargs))

    def error(self, phase: Phase, message: str, **kwargs) -> MigrationEvent:
        return self.emit(MigrationEvent(Level.ERROR, phase, message,
                                        action_required=True, **kwargs))

    def critical(self, phase: Phase, message: str, **kwargs) -> MigrationEvent:
        return self.emit(MigrationEvent(Level.CRITICAL, phase, message,
                                        action_required=True, blocking=True, **kwargs))

    def manual(self, phase: Phase, message: str, **kwargs) -> MigrationEvent:
        return self.emit(MigrationEvent(Level.MANUAL, phase, message,
                                        action_required=True, blocking=True, **kwargs))

    # ── Queries ──────────────────────────────────────────────────────────────

    def by_level(self, level: Level) -> list[MigrationEvent]:
        return [e for e in self._events if e.level == level]

    @property
    def blocking_count(self) -> int:
        return sum(1 for e in self._events if e.blocking)

    @property
    def has_blockers(self) -> bool:
        return self.blocking_count > 0

    @property
    def all_events(self) -> list[MigrationEvent]:
        return list(self._events)

    def summary_counts(self) -> dict[str, int]:
        return {lvl.value: sum(1 for e in self._events if e.level == lvl)
                for lvl in Level}

    def sanitized_block(self, phase: Optional[Phase] = None) -> str:
        """
        Returns a sanitized block of all events (or events for one phase)
        ready to paste into an LLM. No IPs, hostnames, UUIDs, or credentials.
        """
        events = self._events if phase is None else [e for e in self._events if e.phase == phase]
        header = (
            "=== AVI → FORTIADC MIGRATION ANALYSIS ===\n"
            "All environment-specific values have been replaced with placeholders.\n"
            "Safe to paste into an LLM.\n\n"
        )
        body = "\n\n".join(e.sanitized for e in events)
        counts = self.summary_counts()
        footer = (
            f"\n\n=== SUMMARY ===\n"
            + "\n".join(f"  {k}: {v}" for k, v in counts.items() if v > 0)
        )
        return header + body + footer

    def close(self) -> None:
        if self._log_file:
            self._log_file.close()
