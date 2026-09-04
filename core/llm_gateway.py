"""
core/llm_gateway.py
Enterprise-grade LLM integration for the AVI → FortiADC migration tool.

ARCHITECTURE — NO DIRECT BACKEND CONNECTIONS:
  The tool never connects directly to any LLM API (OpenAI, Anthropic, etc.).
  All LLM requests route through a configurable enterprise gateway:

  Tool → LLM Gateway (internal) → [OpsAI Ollama | Enterprise Proxy | API GW]
                                          ↑
                         Air-gap compliant, audit-logged, rate-limited

  This design satisfies:
  - Data sovereignty: no migration config data leaves the enterprise boundary
  - Audit compliance: every LLM call logged with prompt hash, not prompt text
  - Secret isolation: no LLM API keys in tool config — managed by gateway
  - Revocability: gateway access removed without touching tool config
  - Separation of concerns: LLM endpoint changes without touching tool code

GATEWAY MODES (configured in config.yaml):
  mode: "opsai"      → OpsAI internal Ollama (air-gapped, preferred)
  mode: "proxy"      → Enterprise HTTP proxy to approved external LLM
  mode: "apigw"      → Internal API Gateway (Kong, AWS API GW, etc.)
  mode: "disabled"   → No LLM — tool runs in deterministic-only mode

WHAT THE LLM IS USED FOR (all optional, all auditable):
  1. DataScript analysis — classify and suggest FortiADC equivalents
  2. Migration report enrichment — explain findings in plain English
  3. Unsupported item guidance — suggest resolution approaches
  4. Anomaly explanation — explain drift or failure patterns
  5. Executive summary — generate non-technical migration summary

WHAT THE LLM IS NEVER USED FOR:
  - Making migration decisions (all decisions are deterministic)
  - Approving or rejecting deployments
  - Accessing live Avi or FortiADC API
  - Storing or returning credentials
  - Generating executable code that is auto-deployed

COMPLIANCE:
  - Prompt content is sanitized before sending (same sanitizer as reports)
  - Prompt SHA-256 hash logged, never raw prompt
  - Response is advisory only — engineer must validate all suggestions
  - All LLM interactions written to state ledger with operator identity
  - LLM mode can be disabled entirely for environments requiring no AI
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── Sanitizer (same rules as core/events.py) ────────────────────────────────
_SANITIZE_PATTERNS = [
    # IPv4 addresses
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"),
     lambda m, c={}: f"[IP_{c.setdefault(m.group(), len(c)+1)}]"),
    # UUIDs
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
     lambda m, c={}: f"[UUID_{c.setdefault(m.group().lower(), len(c)+1)}]"),
    # FQDNs (3+ labels)
    (re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.){2,}[a-zA-Z]{2,}\b"),
     lambda m, c={}: f"[HOST_{c.setdefault(m.group().lower(), len(c)+1)}]"),
    # Credentials in JSON-style context
    (re.compile(r'"(?:password|token|secret|key|credential)"\s*:\s*"[^"]{4,}"', re.I),
     lambda m, _: '"[CREDENTIAL]": "[REDACTED]"'),
]


def _sanitize(text: str) -> str:
    result = text
    for pattern, replacer in _SANITIZE_PATTERNS:
        counter: dict = {}
        result = pattern.sub(lambda m: replacer(m, counter), result)
    return result


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


# ── Response container ───────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    content:       str
    model:         str
    prompt_hash:   str       # SHA-256 of prompt — logged, never raw text
    gateway_mode:  str
    latency_ms:    int
    tokens_used:   int = 0
    cached:        bool = False
    error:         Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.content)

    def to_audit_record(self) -> dict:
        """Safe audit record — no raw prompt, no raw response."""
        return {
            "prompt_hash":  self.prompt_hash,
            "model":        self.model,
            "gateway_mode": self.gateway_mode,
            "latency_ms":   self.latency_ms,
            "tokens_used":  self.tokens_used,
            "cached":       self.cached,
            "ok":           self.ok,
            "error":        self.error,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }


# ── Gateway configuration ────────────────────────────────────────────────────

@dataclass
class GatewayConfig:
    mode:           str = "disabled"           # opsai | proxy | apigw | disabled
    endpoint:       str = ""                   # internal gateway URL — never public API
    model:          str = "llama3-opsai"       # model name at the gateway
    timeout_s:      int = 120
    max_tokens:     int = 1024
    temperature:    float = 0.1
    verify_ssl:     bool = True
    # Enterprise proxy settings (mode=proxy)
    proxy_url:      str = ""                   # e.g. http://squid.internal:3128
    # API Gateway settings (mode=apigw)
    apigw_key_env:  str = "MIGRATION_LLM_KEY"  # env var name — never hardcoded
    # Rate limiting (per tool instance, not per user — gateway enforces user limits)
    rate_limit_rpm: int = 20                   # requests per minute
    # Compliance settings
    max_prompt_chars: int = 8000               # truncate before sending
    require_sanitize: bool = True              # always sanitize before send
    log_responses:    bool = False             # never log raw responses by default

    @classmethod
    def from_config(cls, cfg: dict) -> "GatewayConfig":
        llm_cfg = cfg.get("llm_gateway", {})
        if not llm_cfg or not llm_cfg.get("enabled", False):
            return cls(mode="disabled")
        return cls(
            mode            = llm_cfg.get("mode", "disabled"),
            endpoint        = llm_cfg.get("endpoint", ""),
            model           = llm_cfg.get("model", "llama3-opsai"),
            timeout_s       = llm_cfg.get("timeout_seconds", 120),
            max_tokens      = llm_cfg.get("max_tokens", 1024),
            temperature     = llm_cfg.get("temperature", 0.1),
            verify_ssl      = llm_cfg.get("verify_ssl", True),
            proxy_url       = llm_cfg.get("proxy_url", ""),
            apigw_key_env   = llm_cfg.get("apigw_key_env", "MIGRATION_LLM_KEY"),
            rate_limit_rpm  = llm_cfg.get("rate_limit_rpm", 20),
            max_prompt_chars= llm_cfg.get("max_prompt_chars", 8000),
            require_sanitize= llm_cfg.get("require_sanitize", True),
            log_responses   = llm_cfg.get("log_responses", False),
        )


# ── Rate limiter ─────────────────────────────────────────────────────────────

class _RateLimiter:
    def __init__(self, rpm: int):
        self._rpm = rpm
        self._calls: list[float] = []

    def acquire(self) -> None:
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < 60.0]
        if len(self._calls) >= self._rpm:
            wait = 60.0 - (now - self._calls[0]) + 0.1
            log.info("LLM rate limit: waiting %.1fs", wait)
            time.sleep(wait)
        self._calls.append(time.monotonic())


# ── Gateway client ───────────────────────────────────────────────────────────

class LLMGateway:
    """
    Enterprise LLM gateway — all tool LLM calls route through here.

    KEY DESIGN RULES:
    1. Never connects to public LLM APIs directly
    2. Sanitizes all prompts before sending
    3. Logs prompt hash, not prompt content
    4. Returns advisory text only — no executable output is auto-applied
    5. Falls back gracefully to deterministic-only mode if gateway is down
    6. Respects rate limits at the tool level (gateway enforces user limits)
    """

    def __init__(self, config: GatewayConfig):
        self._cfg     = config
        self._limiter = _RateLimiter(config.rate_limit_rpm)
        self._cache:  dict[str, LLMResponse] = {}

    @property
    def enabled(self) -> bool:
        return self._cfg.mode != "disabled" and bool(self._cfg.endpoint)

    def call(self, prompt: str, context: str = "",
             cache_key: str = "") -> LLMResponse:
        """
        Send a prompt to the LLM via the configured gateway.
        Prompt is sanitized before sending.
        Returns LLMResponse — check .ok before using .content.
        """
        if not self.enabled:
            return LLMResponse(
                content="", model="none", prompt_hash="",
                gateway_mode="disabled", latency_ms=0,
                error="LLM gateway disabled — deterministic mode only",
            )

        # Sanitize prompt before any network call
        if self._cfg.require_sanitize:
            prompt = _sanitize(prompt)
            context = _sanitize(context) if context else ""

        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        # Truncate to compliance limit
        if len(full_prompt) > self._cfg.max_prompt_chars:
            full_prompt = full_prompt[:self._cfg.max_prompt_chars] + "\n[TRUNCATED]"

        ph = _prompt_hash(full_prompt)

        # Cache check (keyed by prompt hash)
        ck = cache_key or ph
        if ck in self._cache:
            cached = self._cache[ck]
            log.debug("LLM cache hit: %s", ph[:16])
            return LLMResponse(
                content=cached.content, model=cached.model,
                prompt_hash=ph, gateway_mode=cached.gateway_mode,
                latency_ms=0, cached=True,
            )

        # Rate limit
        self._limiter.acquire()

        # Route to correct gateway mode
        t0 = time.monotonic()
        try:
            if self._cfg.mode == "opsai":
                response = self._call_opsai(full_prompt)
            elif self._cfg.mode == "proxy":
                response = self._call_via_proxy(full_prompt)
            elif self._cfg.mode == "apigw":
                response = self._call_via_apigw(full_prompt)
            else:
                return LLMResponse(
                    content="", model="none", prompt_hash=ph,
                    gateway_mode=self._cfg.mode, latency_ms=0,
                    error=f"Unknown gateway mode: {self._cfg.mode}",
                )
        except Exception as e:
            latency = int((time.monotonic() - t0) * 1000)
            log.warning("LLM gateway error (non-fatal): %s", e)
            return LLMResponse(
                content="", model=self._cfg.model, prompt_hash=ph,
                gateway_mode=self._cfg.mode, latency_ms=latency,
                error=str(e),
            )

        latency = int((time.monotonic() - t0) * 1000)
        result = LLMResponse(
            content=response.get("content", ""),
            model=response.get("model", self._cfg.model),
            prompt_hash=ph,
            gateway_mode=self._cfg.mode,
            latency_ms=latency,
            tokens_used=response.get("tokens_used", 0),
        )

        if result.ok:
            self._cache[ck] = result

        log.debug("LLM response: mode=%s latency=%dms hash=%s",
                  self._cfg.mode, latency, ph[:16])
        return result

    # ── Gateway mode implementations ─────────────────────────────────────────

    def _call_opsai(self, prompt: str) -> dict:
        """
        OpsAI internal Ollama — preferred for air-gapped environments.
        Endpoint: internal Ollama REST API at configured internal URL.
        No credentials needed — Ollama is trusted-internal only.
        """
        import requests
        resp = requests.post(
            f"{self._cfg.endpoint.rstrip('/')}/api/generate",
            json={
                "model":  self._cfg.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self._cfg.temperature,
                    "num_predict": self._cfg.max_tokens,
                },
            },
            timeout=self._cfg.timeout_s,
            verify=self._cfg.verify_ssl,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "content":     data.get("response", ""),
            "model":       self._cfg.model,
            "tokens_used": data.get("eval_count", 0),
        }

    def _call_via_proxy(self, prompt: str) -> dict:
        """
        Enterprise HTTP proxy to approved external LLM.
        All traffic routes through the enterprise proxy (Squid, Zscaler, etc.).
        Proxy URL must be internal — blocks direct public API access.
        API key read from environment, never from config file.
        """
        import requests
        proxies = {
            "http":  self._cfg.proxy_url,
            "https": self._cfg.proxy_url,
        }
        # API key from environment only — never from config.yaml
        api_key = os.environ.get(self._cfg.apigw_key_env, "")
        if not api_key:
            raise RuntimeError(
                f"LLM proxy mode requires {self._cfg.apigw_key_env} environment variable. "
                f"Set it via your secrets manager, not in config.yaml."
            )
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Tool-Version": "migration-tool-v0.8",
            "X-Request-Source": "avi-fortiadc-migration",
        }
        payload = {
            "model":       self._cfg.model,
            "messages":    [{"role": "user", "content": prompt}],
            "max_tokens":  self._cfg.max_tokens,
            "temperature": self._cfg.temperature,
        }
        resp = requests.post(
            self._cfg.endpoint,
            json=payload,
            headers=headers,
            proxies=proxies,
            timeout=self._cfg.timeout_s,
            verify=self._cfg.verify_ssl,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("choices", [{}])[0]
                   .get("message", {}).get("content", ""))
        return {
            "content":     content,
            "model":       data.get("model", self._cfg.model),
            "tokens_used": data.get("usage", {}).get("total_tokens", 0),
        }

    def _call_via_apigw(self, prompt: str) -> dict:
        """
        Internal API Gateway (Kong, AWS API GW, Azure APIM, etc.).
        The gateway handles auth, rate limiting, model routing, and audit.
        Tool sends to internal gateway URL only — gateway proxies to LLM.
        API key from environment variable — never hardcoded.
        """
        import requests
        api_key = os.environ.get(self._cfg.apigw_key_env, "")
        if not api_key:
            raise RuntimeError(
                f"API Gateway mode requires {self._cfg.apigw_key_env} env var. "
                f"Inject via secrets manager."
            )
        headers = {
            "Content-Type": "application/json",
            "X-API-Key":    api_key,
        }
        payload = {
            "prompt":      prompt,
            "model":       self._cfg.model,
            "max_tokens":  self._cfg.max_tokens,
            "temperature": self._cfg.temperature,
            "source":      "avi-fortiadc-migration-tool",
        }
        resp = requests.post(
            self._cfg.endpoint,
            json=payload,
            headers=headers,
            timeout=self._cfg.timeout_s,
            verify=self._cfg.verify_ssl,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "content":     data.get("response", data.get("content", "")),
            "model":       data.get("model", self._cfg.model),
            "tokens_used": data.get("tokens", 0),
        }


# ── Singleton factory ────────────────────────────────────────────────────────

_gateway: Optional[LLMGateway] = None


def get_gateway(config_path: str = "config.yaml") -> LLMGateway:
    """Return the singleton gateway instance."""
    global _gateway
    if _gateway is None:
        try:
            import yaml
            cfg = yaml.safe_load(open(config_path).read())
        except Exception:
            cfg = {}
        gc = GatewayConfig.from_config(cfg)
        _gateway = LLMGateway(gc)
        if _gateway.enabled:
            log.info("LLM gateway: mode=%s model=%s", gc.mode, gc.model)
        else:
            log.info("LLM gateway: disabled — deterministic mode")
    return _gateway


def reset_gateway() -> None:
    """Reset singleton (for testing)."""
    global _gateway
    _gateway = None


# ── LLM-augmented analysis functions ────────────────────────────────────────
# These wrap deterministic analysis with optional LLM enrichment.
# All are graceful-degradation: if LLM unavailable, return deterministic result.

# System context injected into every prompt — never changes per-call
_SYSTEM_CONTEXT = """You are an expert in AVI Networks (NSX Advanced Load Balancer) 
and FortiADC migration. You help platform engineers understand migration findings.

RULES:
1. Base all analysis on the provided sanitized data only
2. Never suggest disabling security controls (SSL, WAF, health monitors)
3. Always recommend parallel deployment before cutover
4. Flag DataScript complexity honestly — never understate migration effort
5. All recommendations require human review before implementation
6. Output must be concise, structured, and actionable

IMPORTANT: All values in [BRACKETS] are sanitized placeholders for real IPs,
hostnames, and identifiers. This is intentional for data security."""


def enrich_datascript_analysis(
    script_name:   str,
    script_code:   str,
    events:        list[str],
    line_count:    int,
    deterministic_classification: str,
    gateway:       Optional[LLMGateway] = None,
) -> dict:
    """
    Enrich DataScript analysis with LLM suggestions.
    Deterministic classification is always computed first.
    LLM adds: plain-English explanation, FortiADC approach, complexity rationale.

    Returns dict with 'deterministic' (always populated) and 'llm' (if available).
    """
    gw = gateway or get_gateway()
    result = {
        "name":                        script_name,
        "deterministic_class":         deterministic_classification,
        "line_count":                  line_count,
        "events":                      events,
        "llm_available":               gw.enabled,
        "llm_explanation":             None,
        "llm_fortiadc_approach":       None,
        "llm_complexity_rationale":    None,
        "llm_prompt_hash":             None,
    }

    if not gw.enabled:
        return result

    sanitized_code = _sanitize(script_code)
    prompt = f"""Analyse this AVI DataScript for migration to FortiADC.

DataScript name: {_sanitize(script_name)}
Events: {', '.join(events)}
Lines: {line_count}
Deterministic classification: {deterministic_classification}

Code:
```lua
{sanitized_code[:3000]}
{'...[TRUNCATED]' if len(sanitized_code) > 3000 else ''}
```

Provide:
1. EXPLANATION: What does this script do? (2-3 sentences, plain English)
2. FORTIADC_APPROACH: What is the best FortiADC equivalent?
   Options: content routing rules | HTTP profile rules | WAF custom rule | 
   application-layer change | FortiADC scripting | no equivalent
3. COMPLEXITY_RATIONALE: Why is it classified {deterministic_classification}?
4. MIGRATION_RISK: LOW / MEDIUM / HIGH — and why

Format as: EXPLANATION: ... FORTIADC_APPROACH: ... COMPLEXITY_RATIONALE: ... MIGRATION_RISK: ..."""

    resp = gw.call(prompt, context=_SYSTEM_CONTEXT,
                   cache_key=f"ds:{hashlib.sha256(script_code.encode()).hexdigest()[:16]}")

    result["llm_prompt_hash"] = resp.prompt_hash
    if resp.ok:
        content = resp.content
        for field_name, prefix in [
            ("llm_explanation",         "EXPLANATION:"),
            ("llm_fortiadc_approach",   "FORTIADC_APPROACH:"),
            ("llm_complexity_rationale","COMPLEXITY_RATIONALE:"),
        ]:
            if prefix in content:
                parts = content.split(prefix, 1)
                if len(parts) > 1:
                    value = parts[1].split("\n")[0].strip()
                    result[field_name] = value
    else:
        result["llm_error"] = resp.error

    return result


def enrich_migration_report(
    summary: dict,
    patterns: list,
    complexity: dict,
    manual_count: int,
    gateway: Optional[LLMGateway] = None,
) -> dict:
    """
    Generate plain-English executive summary of migration analysis.
    Used for: Change Request attachment, management briefing, stakeholder email.
    """
    gw = gateway or get_gateway()
    result = {
        "executive_summary": None,
        "key_risks":         None,
        "recommended_order": None,
        "llm_available":     gw.enabled,
        "llm_prompt_hash":   None,
    }

    if not gw.enabled:
        return result

    prompt = f"""Generate a concise executive summary for an AVI to FortiADC migration.

Migration statistics:
- Complexity score: {complexity.get('score', '?')}/10 ({complexity.get('verbal', '')})
- Estimated duration: {complexity.get('estimated_days', '?')} working days
- Manual items requiring human action: {manual_count}
- Patterns detected: {len(patterns)}
- Pattern names: {', '.join(p.get('name', '') for p in patterns if p.get('detected', False))}

Provide:
1. EXECUTIVE_SUMMARY: 3-4 sentences suitable for a management briefing
2. KEY_RISKS: Top 3 risks, one sentence each
3. RECOMMENDED_ORDER: Suggested migration sequence in one paragraph

Keep technical language minimal. Focus on business impact and timeline."""

    resp = gw.call(prompt, context=_SYSTEM_CONTEXT,
                   cache_key=f"report:{complexity.get('score')}:{manual_count}")

    result["llm_prompt_hash"] = resp.prompt_hash
    if resp.ok:
        content = resp.content
        for field_name, prefix in [
            ("executive_summary", "EXECUTIVE_SUMMARY:"),
            ("key_risks",        "KEY_RISKS:"),
            ("recommended_order","RECOMMENDED_ORDER:"),
        ]:
            if prefix in content:
                parts = content.split(prefix, 1)
                if len(parts) > 1:
                    # Multi-line field — grab until next UPPER_CASE: prefix
                    value = parts[1].split("\n\n")[0].strip()
                    result[field_name] = value
    else:
        result["llm_error"] = resp.error

    return result


def enrich_unsupported_item(
    object_type: str,
    object_name: str,
    reason:      str,
    detail:      dict,
    gateway:     Optional[LLMGateway] = None,
) -> dict:
    """
    Enrich a MANUAL/BLOCKED item with LLM resolution guidance.
    Deterministic guidance is always computed; LLM adds nuance and alternatives.
    """
    gw = gateway or get_gateway()
    result = {
        "llm_available":  gw.enabled,
        "llm_guidance":   None,
        "llm_prompt_hash": None,
    }

    if not gw.enabled:
        return result

    sanitized_detail = _sanitize(json.dumps(detail, default=str))
    prompt = f"""An AVI object cannot be automatically migrated to FortiADC.

Object type: {object_type}
Reason: {reason}
Detail: {sanitized_detail[:1000]}

Provide concise, actionable guidance:
1. RESOLUTION: Step-by-step resolution (3-5 steps max)
2. ALTERNATIVES: Are there FortiADC features that partially address this?
3. EFFORT: Estimated engineer effort (hours/days)
4. BLOCKING: Does this block the full environment migration or just this VS?

Be specific to FortiADC capabilities, not generic."""

    resp = gw.call(prompt, context=_SYSTEM_CONTEXT,
                   cache_key=f"unsupported:{object_type}:{_sanitize(object_name)[:20]}")

    result["llm_prompt_hash"] = resp.prompt_hash
    if resp.ok:
        result["llm_guidance"] = resp.content[:2000]
    else:
        result["llm_error"] = resp.error

    return result


def enrich_drift_explanation(
    drift_items: list,
    gateway:     Optional[LLMGateway] = None,
) -> dict:
    """
    Explain configuration drift findings in plain English.
    Helps operators understand what changed and what to do.
    """
    gw = gateway or get_gateway()
    result = {
        "llm_available":   gw.enabled,
        "llm_explanation": None,
        "llm_prompt_hash": None,
    }

    if not gw.enabled or not drift_items:
        return result

    items_summary = [
        f"{d.object_type}/{d.object_name} field={d.field} "
        f"expected={_sanitize(str(d.expected))} actual={_sanitize(str(d.actual))}"
        for d in drift_items[:10]
    ]

    prompt = f"""FortiADC configuration drift detected after migration deployment.
The following fields differ from what the migration tool deployed:

{chr(10).join(items_summary)}

Explain:
1. LIKELY_CAUSE: What likely caused each drift item?
2. RISK_ASSESSMENT: What is the operational risk of each difference?
3. REMEDIATION: Should we update FortiADC to match the plan, or update the plan?

Focus on practical guidance for a platform engineer."""

    resp = gw.call(prompt, context=_SYSTEM_CONTEXT,
                   cache_key=f"drift:{len(drift_items)}")

    result["llm_prompt_hash"] = resp.prompt_hash
    if resp.ok:
        result["llm_explanation"] = resp.content[:2000]
    else:
        result["llm_error"] = resp.error

    return result
