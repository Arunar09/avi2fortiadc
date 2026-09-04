"""
services/common.py
Shared helpers for deterministic UI services.
"""
from __future__ import annotations

import json
from pathlib import Path


def canonical_env_name(name: str) -> str:
    """Normalize artifact-derived names back to the logical environment name."""
    env = Path(name).stem
    for suffix in (
        "-ledger",
        "-unsupported",
        "-analysis",
        "-transform-review",
        "-sanitized",
        "-import-stage",
        "-import-commit",
        "-import",
        "-ops-check",
        "-candidate-discovery",
        "-raw-snapshot",
        "-decision-manifest",
        "-decisions",
        "-transform",
        "_discovery",
        "-discovery",
    ):
        if env.endswith(suffix):
            env = env[: -len(suffix)]
    return env.replace("_", "-")


def load_known_envs(dirs: dict) -> list[str]:
    """
    Return known environments from real local sources only.
    Priority:
      1. config.yaml environments (Source of Truth)
      2. state/, discovery/ artifacts (Auto-Discovery)
    """
    envs: set[str] = set()
    blacklist = {"raw-snapshot", "scratch", "tmp", "test", "sample", "reports", "fortiadc", "discovery", "logs"}

    # 1. Source of Truth: config.yaml
    config_path = Path(dirs.get("config_path", "config.yaml"))
    if config_path.exists():
        try:
            import yaml
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            for item in data.get("environments", []):
                name = str(item.get("name", "")).strip()
                if name and name not in blacklist:
                    envs.add(name)
        except Exception:
            pass

    # 2. Auto-Discovery from filesystem
    for key, pattern in (
        ("state_dir", "*"),
        ("discovery_dir", "*.json"),
    ):
        base = Path(dirs.get(key, ""))
        if not base.exists():
            continue
        
        # Check subdirectories (organized structure)
        for sub in base.iterdir():
            if sub.is_dir() and not sub.name.startswith(".") and sub.name not in blacklist:
                if key == "state_dir":
                    # An env in state/ is 'actual' only if it has a discovery signature
                    # ledger.json alone is just a trace of a command run.
                    discovery_sigs = {"candidate-discovery.json", "raw-snapshot.json"}
                    if not any((sub / name).exists() for name in discovery_sigs):
                        continue
                envs.add(sub.name)

        # Check flat files for backward compatibility
        for path in base.glob(pattern):
            if not path.is_file():
                continue
            
            # Skip technical/system files
            if path.name == "rag_index.db" or path.name.startswith(".") or any(b in path.name.lower() for b in blacklist):
                continue
            if path.name.endswith("-decision-manifest.json") or path.name.endswith("-decisions.jsonl"):
                continue
            if path.suffix in {".py", ".pyc", ".log", ".db"}:
                continue

            env = canonical_env_name(path.name)
            if env and env not in blacklist:
                envs.add(env)

    return sorted(envs)


def get_tenant_mapping(dirs: dict) -> dict[str, str]:
    """Return a mapping of env_name -> tenant_name from config.yaml."""
    mapping: dict[str, str] = {}
    config_path = Path(dirs.get("config_path", "config.yaml"))
    if config_path.exists():
        try:
            import yaml
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            for item in data.get("environments", []):
                name = str(item.get("name", "")).strip()
                tenant = str(item.get("avi_tenant", "")).strip()
                if name and tenant:
                    mapping[name] = tenant
        except Exception:
            pass
    return mapping


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def flatten_avi_snapshot(data: dict) -> dict:
    """
    Generically merges nested configuration envelopes (AviConfig, raw_config) 
    into the root key-space to ensure all siblings (like GslbService) are preserved.
    """
    if not isinstance(data, dict):
        return {}
    
    result = data.copy()
    # Known envelopes that might contain the bulk of the configuration
    ENVELOPES = ["AviConfig", "raw_config", "config"]
    
    for env in ENVELOPES:
        if env in result and isinstance(result[env], dict):
            inner = result.pop(env)
            # Merge inner keys into result if they don't collide or if we prioritize inner
            for k, v in inner.items():
                if k not in result or (isinstance(v, list) and not result[k]):
                    result[k] = v
    return result


def ensure_env_dir(base_dir: str | Path, env: str) -> Path:
    """Ensure a subdirectory exists for the environment in the given base directory."""
    path = Path(base_dir) / env
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_env_file(base_dir: str | Path, env: str, suffix: str) -> Path | None:
    """
    Find the real file for an environment. 
    Prioritizes <base>/<env>/<file> then falls back to <base>/<env>-<file>.
    """
    base = Path(base_dir)
    if not base.exists():
        return None

    # Step 1: New Grouped structure (e.g. state/d2/ledger.json)
    # The suffix passed might be "-ledger.json", so we split to get the filename
    clean_suffix = suffix.lstrip("-")
    grouped = base / env / clean_suffix
    if grouped.exists():
        return grouped

    # Step 2: Flat structure (e.g. state/d2-ledger.json)
    direct = base / f"{env}{suffix}"
    if direct.exists():
        return direct

    # Step 3: Heuristic scan for mismatched names
    for path in base.glob(f"*{suffix}"):
        if path.is_file() and canonical_env_name(path.name) == env:
            return path
    
    # Step 4: Recursive scan for grouped files with env prefix still in name
    for path in base.glob(f"**/*{suffix}"):
        if path.is_file() and canonical_env_name(path.name) == env:
            return path

    return None
