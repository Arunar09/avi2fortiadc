#!/usr/bin/env python3
"""
migrate.py — Avi → FortiADC Migration Tool CLI

Usage:
  python3 migrate.py discover    --env <env> [--output <file>]
  python3 migrate.py import-avi-json --input <discovery.json> --env <env> [--output <file>]
  python3 migrate.py analyse     --input <discovery.json> [--report <dir>]
  python3 migrate.py transform   --input <discovery.json> [--output <file>]
  python3 migrate.py deploy      --fortiadc-config <fortiadc.json> --env <env> [--dry-run|--execute]
  python3 migrate.py llm-pack    --type <full|datascript|next_steps|manual_item> --input <discovery.json> [--name <obj>]
  python3 migrate.py audit       <chain|verify|cef|summary> --log <file> [--output <file>]
  python3 migrate.py rag         <index|search|ask|stats> [--query ...] [--env ...] [--source ...]

No LLM required. Built-in intelligence via pattern detection and heuristics.
LLM packs are sanitized blocks for use with any EXTERNAL LLM.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# ── Windows UTF-8 output fix ──────────────────────────────────────────────────
# PowerShell / cmd windows default to cp1252; reconfigure to UTF-8 so that
# any Unicode content in RAG results (arrows, special chars) prints cleanly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _load_config(config_path: str = "config.yaml") -> dict:
    try:
        import yaml
        return yaml.safe_load(Path(config_path).read_text())
    except FileNotFoundError:
        print(f"Config file '{config_path}' not found. "
              f"Create from config.example.yaml")
        sys.exit(1)
    except ImportError:
        print("PyYAML not installed. Run: pip install pyyaml")
        sys.exit(1)


def _get_avi_client(cfg: dict, env: str):
    from core.avi_client import AviClient
    envs = {e["name"]: e for e in (cfg.get("environments") or [])}
    if env not in envs:
        print(f"Environment '{env}' not found in config. "
              f"Available: {list(envs.keys())}")
        sys.exit(1)
    env_cfg = envs[env]
    return AviClient(
        controller  = cfg["avi"]["controller"],
        username    = cfg["avi"]["username"],
        password    = cfg["avi"]["password"],
        tenant      = env_cfg.get("avi_tenant", cfg["avi"].get("tenant", "admin")),
        api_version = cfg["avi"].get("api_version", "22.1.5"),
        verify_ssl  = cfg["avi"].get("verify_ssl", True),
    )


def _get_fortiadc_client(cfg: dict, env: str, dry_run: bool = True):
    from core.fortiadc_client import FortiADCClient
    envs = {e["name"]: e for e in (cfg.get("environments") or [])}
    env_cfg = envs.get(env, {})
    return FortiADCClient(
        host       = cfg["fortiadc"]["host"],
        username   = cfg["fortiadc"]["username"],
        password   = cfg["fortiadc"]["password"],
        vdom       = env_cfg.get("fortiadc_vdom", cfg["fortiadc"].get("vdom", "root")),
        verify_ssl = cfg["fortiadc"].get("verify_ssl", True),
        dry_run    = dry_run,
    )


def _make_bus(env: str, phase: str, verbose: bool = True):
    from core.events import EventBus
    from services.common import ensure_env_dir
    log_dir = ensure_env_dir("logs", env)
    return EventBus(log_path=log_dir / f"{phase}.jsonl", verbose=verbose)


def _make_ledger(env: str):
    from core.state_ledger import StateLedger
    return StateLedger(env)


def _decision_store(env: str):
    from core.decision_manifest import DecisionManifestStore
    return DecisionManifestStore(env)


def _require_phase_gate(env: str, phase: str) -> None:
    from core.decision_manifest import ensure_phase_gate
    store = _decision_store(env)
    manifest = store.load()
    gate = ensure_phase_gate(manifest, phase)
    if not gate.ok:
        print(f"Decision gate blocked for phase '{phase}': {gate.message}")
        if gate.missing:
            print("Missing decisions:")
            for m in gate.missing:
                print(f"  - {m}")
        print(f"Resolve decisions in UI: /decisions/{env}")
        sys.exit(1)


def _get_dirs(config_path: str = "config.yaml") -> dict:
    """Build the standard 'dirs' dict used by services and RAG."""
    root = Path(".").resolve()
    return {
        "tool_root":    str(root),
        "config_path":  str(root / config_path),
        "state_dir":    str(root / "state"),
        "discovery_dir":str(root / "discovery"),
        "fortiadc_dir": str(root / "fortiadc"),
        "reports_dir":  str(root / "reports"),
        "logs_dir":     str(root / "logs"),
    }


def _record_unsupported_from_bus(bus, ledger) -> None:
    for event in bus.all_events:
        if event.level.value not in ("MANUAL", "CRITICAL"):
            continue
        if not event.object_name:
            continue
        ledger.add_unsupported(
            object_type=event.object_type or "System Context",
            object_name=event.object_name or "Global Settings",
            reason=event.detail.get("reason", event.message),
            action=event.detail.get("action", event.detail.get("suggestion", "Review and resolve manually")),
            severity="BLOCKED" if event.blocking else "MANUAL",
        )


def _record_translated_config(fortiadc_config: dict, ledger) -> None:
    section_map = {
        "ssl_certificates": "ssl_certificate",
        "ssl_profiles": "ssl_profile",
        "health_checks": "healthmonitor",
        "real_server_pools": "pool",
        "real_servers": "real_server",
        "pool_members": "pool_member",
        "persistence_profiles": "persistence",
        "virtual_servers": "virtualservice",
    }
    for section, object_type in section_map.items():
        for obj in fortiadc_config.get(section, []):
            name = obj.get("name", "")
            if not name:
                continue
            ledger.add_translated(
                object_type=object_type,
                avi_name=name,
                forti_name=name,
                confidence=1.0,
            )


def _hydrate_vs_vips(discovery: dict) -> dict:
    """Backfill VS VIPs from top-level VSVIP inventory before transform."""
    vsvip_index = {
        item.get("name", ""): item
        for item in discovery.get("vs_vips", [])
        if isinstance(item, dict) and item.get("name")
    }
    for vs in discovery.get("virtual_services", []):
        if not isinstance(vs, dict):
            continue
        if vs.get("_vips"):
            continue
        resolved = vs.setdefault("_resolved", {})
        vsvip_name = resolved.get("vsvip_ref") or ""
        vsvip_obj = vsvip_index.get(vsvip_name)
        if not vsvip_obj:
            continue
        resolved["_vsvip_obj"] = vsvip_obj
        vip_entries = vsvip_obj.get("vip", [])
        vs["_vips"] = [
            vip.get("ip_address", {}).get("addr", "")
            for vip in vip_entries
            if isinstance(vip, dict) and vip.get("ip_address", {}).get("addr")
        ]
    return discovery


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_discover(args):
    """Collect all Avi config into a discovery JSON snapshot."""
    cfg = _load_config(args.config)
    bus = _make_bus(args.env, "discover")
    ledger = _make_ledger(args.env)
    client = _get_avi_client(cfg, args.env)
    ledger.phase_start("discover")

    try:
        print(f"\nConnecting to Avi controller...")
        ok, version = client.ping()
        if not ok:
            bus.critical(__import__("core.events",fromlist=["Phase"]).Phase.CONNECT,
                         f"Cannot connect to Avi: {version}")
            ledger.phase_failed("discover", f"Cannot connect to Avi: {version}")
            sys.exit(1)
        print(f"Connected. Avi version: {version}\n")

        from registry import run_all_collectors
        discovery = run_all_collectors(client, bus)

        out_path = Path(args.output or f"discovery/{args.env}.json")
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(json.dumps(discovery, indent=2, default=str))

        from reporters.console import print_discovery_summary
        print_discovery_summary(discovery, bus)
        _record_unsupported_from_bus(bus, ledger)
        ledger.phase_done("discover", notes=f"snapshot={out_path}")

        print(f"Discovery saved to: {out_path}")
        print(f"Log saved to:       logs/{args.env}-discover.jsonl")
    except SystemExit:
        raise
    except Exception as e:
        ledger.phase_failed("discover", str(e))
        raise
    finally:
        client.close()
        bus.close()


def cmd_import_avi_json(args):
    """Import normalized discovery JSON into discovery schema."""
    bus = _make_bus(args.env, "import")
    try:
        from core.avi_import import import_avi_json

        if args.payload_type != "normalized_discovery":
            print(
                "Only 'normalized_discovery' payloads are supported. "
                "Normalize raw Avi exports in the analyzer first."
            )
            sys.exit(1)

        print(f"\nImporting normalized discovery JSON: {args.input}")
        discovery = import_avi_json(args.input, args.env, bus)

        out_path = Path(args.output or f"discovery/{args.env}.json")
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(json.dumps(discovery, indent=2, default=str), encoding="utf-8")

        print(f"Imported discovery saved to: {out_path}")
        print(f"Log saved to:                logs/{args.env}-import.jsonl")
    finally:
        bus.close()


def cmd_analyse(args):
    """Build dependency graph, run compatibility and intelligence analysis."""
    discovery = json.loads(Path(args.input).read_text())
    env_name  = Path(args.input).stem
    _require_phase_gate(env_name, "analyse")
    bus       = _make_bus(env_name, "analyse")
    ledger    = _make_ledger(env_name)
    ledger.phase_start("analyse")

    from analyzers.dependency_graph import build_dependency_graph, find_shared_objects
    from analyzers.compatibility import analyse_compatibility, summary
    from analyzers.impact import assess_impact
    from core.intelligence import detect_patterns, score_migration_complexity, generate_next_steps
    from reporters.console import print_compatibility_matrix, print_vs_summary

    print("\nBuilding dependency graph...")
    vs_profiles   = build_dependency_graph(discovery)
    shared        = find_shared_objects(vs_profiles)

    print("Running compatibility analysis...")
    compat_results = analyse_compatibility(discovery, vs_profiles, bus=bus)

    print("Assessing traffic impact...")
    impact_list = assess_impact(vs_profiles, discovery, shared)

    print("Running intelligence pattern detection...")
    patterns   = detect_patterns(discovery, vs_profiles, bus)
    complexity = score_migration_complexity(vs_profiles, compat_results, patterns)
    next_steps = generate_next_steps(patterns, compat_results, complexity)

    print_vs_summary(vs_profiles)
    print_compatibility_matrix(compat_results)

    # Print complexity score
    score = complexity["score"]
    verbal = complexity["verbal"]
    print(f"Migration Complexity: {score}/10 — {verbal}")
    print(f"Estimated duration:   {complexity['estimated_days']} working days\n")

    # Print next steps
    print("ORDERED NEXT STEPS:")
    for i, step in enumerate(next_steps, 1):
        print(f"  {i:2d}. {step[:100]}")
    print()

    # Write reports
    report_dir = Path(args.report or f"reports")
    report_dir.mkdir(exist_ok=True)

    from reporters.html import generate_html
    html = generate_html(env_name, bus, discovery, vs_profiles,
                         compat_results, patterns, complexity, next_steps)
    (report_dir / f"{env_name}-analysis.html").write_text(html, encoding="utf-8")

    from reporters.markdown import generate_markdown
    md = generate_markdown(env_name, bus, vs_profiles, compat_results,
                           impact_list, discovery)
    (report_dir / f"{env_name}-analysis.md").write_text(md, encoding="utf-8")

    from reporters.sanitized import generate_sanitized_block
    san = generate_sanitized_block(bus, vs_profiles, compat_results)
    (report_dir / f"{env_name}-sanitized.txt").write_text(san, encoding="utf-8")
    _record_unsupported_from_bus(bus, ledger)
    ledger.phase_done("analyse", notes=f"reports={report_dir}")

    print(f"Reports written to: {report_dir}/")
    print(f"  {env_name}-analysis.html  — Open in browser")
    print(f"  {env_name}-analysis.md    — Attach to Change Request")
    print(f"  {env_name}-sanitized.txt  — Paste into external LLM")
    bus.close()


def cmd_transform(args):
    """Transform discovery JSON to FortiADC API payloads."""
    discovery = json.loads(Path(args.input).read_text())
    discovery = _hydrate_vs_vips(discovery)
    env_name  = Path(args.input).stem
    _require_phase_gate(env_name, "transform")
    bus       = _make_bus(env_name, "transform")
    ledger    = _make_ledger(env_name)
    ledger.phase_start("transform")

    from analyzers.dependency_graph import build_dependency_graph
    from transformers.pool import PoolTransformer, HealthCheckTransformer, \
        SSLCertTransformer, VirtualServerTransformer

    vs_profiles = build_dependency_graph(discovery)

    # Triage Decision Awareness: Filter items based on operator decisions
    store = _decision_store(env_name)
    manifest = store.load()
    triage_decisions = manifest.get("analysis_triage", {}).get("items", {})

    def is_deferred(otype: str, oname: str) -> bool:
        # Check for individual item decision first
        decision = triage_decisions.get(f"{otype}:{oname}", {}).get("decision")
        return decision == "defer"

    fortiadc_config: dict = {
        "ssl_certificates":   [],
        "ssl_profiles":       [],
        "health_checks":      [],
        "real_server_pools":  [],
        "real_servers":       [],
        "pool_members":       [],
        "persistence_profiles": [],
        "virtual_servers":    [],
    }

    # ── Infrastructure Orchestration: VDOM Mapping ──────────────────────────
    from core.resolver import ConfigurationGraph
    graph = ConfigurationGraph(discovery)
    vdom_mappings = manifest.get("vdom_mapping", {}).get("mappings", {})

    t_cert = SSLCertTransformer(bus)
    for cert in discovery.get("ssl_certificates", []):
        if is_deferred("ssl_certificate", cert.get("name")): continue
        result = t_cert.transform(cert)
        if result:
            # Stamp VDOM based on tenant mapping
            t_name = graph.get_tenant(cert).upper()
            if t_name == "ADMIN": t_name = "Global / Shared"
            result["vdom"] = vdom_mappings.get(t_name, {}).get("target") or (t_name if t_name != "Global / Shared" else "root")
            fortiadc_config["ssl_certificates"].append(result)

    t_hc = HealthCheckTransformer(bus)
    for hm in discovery.get("health_monitors", []):
        if is_deferred("healthmonitor", hm.get("name")): continue
        result = t_hc.transform(hm)
        if result:
            t_name = graph.get_tenant(hm).upper()
            if t_name == "ADMIN": t_name = "Global / Shared"
            result["vdom"] = vdom_mappings.get(t_name, {}).get("target") or (t_name if t_name != "Global / Shared" else "root")
            fortiadc_config["health_checks"].append(result)

    t_pool = PoolTransformer(bus)
    for pool in discovery.get("pools", []):
        if is_deferred("pool", pool.get("name")): continue
        result = t_pool.transform(pool)
        if result:
            t_name = graph.get_tenant(pool).upper()
            if t_name == "ADMIN": t_name = "Global / Shared"
            vdom = vdom_mappings.get(t_name, {}).get("target") or (t_name if t_name != "Global / Shared" else "root")
            result["vdom"] = vdom
            # Also stamp child objects
            for rs in result.get("real_servers", []): rs["vdom"] = vdom
            for pm in result.get("pool_members", []): pm["vdom"] = vdom
            
            fortiadc_config["real_server_pools"].append(result)
            fortiadc_config["real_servers"].extend(result.get("real_servers", []))
            fortiadc_config["pool_members"].extend(result.get("pool_members", []))

    # SSL profiles — proper transformer via dedicated class
    from transformers.profiles import ApplicationProfileTransformer, PersistenceProfileTransformer, SSLProfileTransformer
    t_ssl_prof = SSLProfileTransformer(bus)
    for sp in discovery.get("ssl_profiles", []):
        if is_deferred("ssl_profile", sp.get("name")): continue
        result = t_ssl_prof.transform(sp)
        if result:
            t_name = graph.get_tenant(sp).upper()
            if t_name == "ADMIN": t_name = "Global / Shared"
            result["vdom"] = vdom_mappings.get(t_name, {}).get("target") or (t_name if t_name != "Global / Shared" else "root")
            fortiadc_config["ssl_profiles"].append(result)

    # Persistence profiles
    t_persist = PersistenceProfileTransformer(bus)
    for pp in discovery.get("persistence_profiles", []):
        if is_deferred("persistence", pp.get("name")): continue
        result = t_persist.transform(pp)
        if result:
            t_name = graph.get_tenant(pp).upper()
            if t_name == "ADMIN": t_name = "Global / Shared"
            result["vdom"] = vdom_mappings.get(t_name, {}).get("target") or (t_name if t_name != "Global / Shared" else "root")
            fortiadc_config["persistence_profiles"].append(result)

    t_vs = VirtualServerTransformer(bus)
    for vs in discovery.get("virtual_services", []):
        if is_deferred("virtualservice", vs.get("name")): continue
        result = t_vs.transform(vs)
        if result:
            t_name = graph.get_tenant(vs).upper()
            if t_name == "ADMIN": t_name = "Global / Shared"
            result["vdom"] = vdom_mappings.get(t_name, {}).get("target") or (t_name if t_name != "Global / Shared" else "root")
            fortiadc_config["virtual_servers"].append(result)


    # ── Apply operator manual overrides ──────────────────────────────────────
    store = _decision_store(env_name)
    manifest = store.load()
    overrides = manifest.get("manual_overrides", {})
    override_count = 0
    if overrides:
        for section, objs in fortiadc_config.items():
            if not isinstance(objs, list):
                continue
            for i, obj in enumerate(objs):
                path = obj.get("fortiadc_path", "")
                mkey = obj.get("name", "") or obj.get("payload", {}).get("mkey", "")
                key = f"{path}::{mkey}"
                if key in overrides:
                    fortiadc_config[section][i] = {
                        **obj,
                        "payload": overrides[key]["payload"],
                        "_operator_override": True,
                        "_override_note": overrides[key].get("note", ""),
                        "_override_by": overrides[key].get("saved_by", ""),
                        "_override_at": overrides[key].get("saved_at", ""),
                    }
                    override_count += 1
    
    # ── Coexistence & Target Adaptation Logic ────────────────────────────────
    target_config = {}
    existing_path = Path(f"fortiadc/{env_name}-existing.json")
    if existing_path.exists():
        try:
            target_config = json.loads(existing_path.read_text())
        except Exception:
            pass
            
    from core.conflict_resolver import ConflictResolver
    resolver = ConflictResolver(target_config)
    fortiadc_config, coexistence_events = resolver.resolve(fortiadc_config)
    
    for evt in coexistence_events:
        if evt["level"] == "WARNING":
            bus.warn(Phase.TRANSFORM, evt["message"], object_type=evt["object_type"], object_name=evt["object_name"])
        else:
            bus.info(Phase.TRANSFORM, evt["message"], object_type=evt["object_type"], object_name=evt["object_name"])
    # ──────────────────────────────────────────────────────────────────────────

    out_path = Path(args.output or f"fortiadc/{env_name}-config.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(fortiadc_config, indent=2, default=str))

    _record_unsupported_from_bus(bus, ledger)
    _record_translated_config(fortiadc_config, ledger)
    ledger.phase_done("transform", notes=f"config={out_path}")

    # Generate Manual GUI Runbook
    from reporters.runbook import generate_manual_runbook
    runbook_path = generate_manual_runbook(env_name)

    manual_count = sum(1 for e in bus.all_events
                       if e.level.value == "MANUAL")
    auto_count   = sum(len(v) for v in fortiadc_config.values())
    print(f"\nTransform complete:")
    print(f"  Auto-translated objects  : {auto_count}")
    print(f"  Operator overrides applied: {override_count}")
    print(f"  Manual items (skipped)   : {manual_count}")
    print(f"  FortiADC config          : {out_path.resolve()}")
    print(f"")
    print(f"  📋 Manual runbook generated:")
    print(f"     {runbook_path.resolve()}")
    print(f"     Open this file and complete each ⬜ checklist item before go-live.")
    bus.close()


def cmd_deploy(args):
    """Deploy transformed FortiADC config. Default: dry-run."""
    from core.governance import run_governance_gate, print_governance_summary
    from core.lock import PhaseLock, LockConflict
    _require_phase_gate(args.env, "deploy")

    cfg            = _load_config(args.config_file)
    fortiadc_config = json.loads(Path(args.fortiadc_config).read_text())
    dry_run        = not args.execute
    ledger         = _make_ledger(args.env)

    # ── Governance gate (execute mode only — dry-run skips CR requirement) ──
    if args.execute:
        override = getattr(args, "override_governance", False)
        gov = run_governance_gate(
            env=args.env, phase="deploy",
            require_cr=True, require_sod=True,
            interactive=True, override=override,
        )
        print_governance_summary(gov)
        if not gov.passed:
            print("\nDeployment blocked by governance controls.")
            sys.exit(1)

    # ── Concurrency lock ────────────────────────────────────────────────────
    try:
        lock = PhaseLock(args.env, "deploy")
        lock.acquire()
    except LockConflict as e:
        print(f"\n[LOCK CONFLICT]\n{e}")
        sys.exit(1)

    try:
        bus    = _make_bus(args.env, "deploy")
        client = _get_fortiadc_client(cfg, args.env, dry_run=dry_run)
        ledger.phase_start("deploy")

        from validators.pre_migration import run_pre_migration_checks
        from deployers.fortiadc_deployer import FortiADCDeployer

        print(f"\nPre-migration validation...")
        pre_results = run_pre_migration_checks(client, fortiadc_config, bus)
        failed_pre  = [r for r in pre_results if not r.passed and r.blocking]
        if failed_pre:
            print(f"\nPre-migration validation FAILED — {len(failed_pre)} blocking issue(s).")
            ledger.phase_failed("deploy", f"pre_migration_failed={len(failed_pre)}")
            print("Fix all blocking issues before deploying.")
            sys.exit(1)

        deployer = FortiADCDeployer(client, bus)
        results  = deployer.deploy_all(fortiadc_config)

        passed = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        print(f"\nDeploy: {passed} succeeded, {failed} failed")
        if dry_run:
            print(f"This was a DRY-RUN. Pass --execute to make real changes.")

        # ── Hash-chain the log for tamper-evidence ───────────────────────────
        from core.audit_export import chain_log_file
        log_path = f"logs/{args.env}-deploy.jsonl"
        if Path(log_path).exists():
            chain_log_file(log_path)

        _record_unsupported_from_bus(bus, ledger)
        mode = "dry-run" if dry_run else "execute"
        ledger.phase_done("deploy", notes=f"mode={mode}; passed={passed}; failed={failed}")
        bus.close()
        client.close()
    finally:
        lock.release()


def cmd_llm_pack(args):
    """Generate a sanitized LLM assistance pack."""
    discovery = json.loads(Path(args.input).read_text()) if args.input else {}
    env_name  = Path(args.input).stem if args.input else "unknown"
    bus       = _make_bus(env_name, "llm-pack", verbose=False)

    from core.intelligence import build_llm_pack
    from analyzers.dependency_graph import build_dependency_graph

    vs_profiles = build_dependency_graph(discovery) if discovery else {}

    pack = build_llm_pack(
        pack_type   = args.type,
        discovery   = discovery,
        bus         = bus,
        target_name = getattr(args, "name", ""),
    )

    out_file = f"reports/{env_name}-llm-pack-{args.type}.txt"
    Path("reports").mkdir(exist_ok=True)
    Path(out_file).write_text(pack)
    print(pack)
    print(f"\n--- Saved to: {out_file} ---")
    bus.close()



def cmd_audit(args):
    """Audit log tools: chain, verify, export-cef, summary."""
    from core.audit_export import chain_log_file, verify_chain, export_cef, summarise_log

    if args.audit_cmd == "chain":
        n = chain_log_file(args.log, args.output)
        print(f"Hash chain added to {n} entries in {args.log}")
    elif args.audit_cmd == "verify":
        ok, report = verify_chain(args.log)
        print(report)
        sys.exit(0 if ok else 1)
    elif args.audit_cmd == "cef":
        out = args.output or args.log.replace(".jsonl", ".cef")
        n = export_cef(args.log, out)
        print(f"Exported {n} events to CEF: {out}")
    elif args.audit_cmd == "summary":
        s = summarise_log(args.log)
        import json as _json
        print(_json.dumps(s, indent=2))
    else:
        print("Unknown audit command. Use: chain | verify | cef | summary")


def cmd_ops_check(args):
    """Run all operational automation checks: health, capacity, pool sync, drift, datascripts."""
    from core.ops_automation import run_ops_check
    import json as _json
    _require_phase_gate(args.env, "verify")

    print(f"\nOperational automation check — environment: {args.env}")
    print("Connecting to FortiADC (read-only)...\n")

    report = run_ops_check(
        env=args.env,
        config_path=args.config,
        state_dir="state",
    )

    if "error" in report:
        print(f"Error: {report['error']}")
        return

    summary = report.get("summary", {})

    # Health signals
    health = report.get("health", [])
    if health:
        print("── Health ───────────────────────────────────────")
        for s in health:
            icon = {"GREEN": "✔", "AMBER": "⚠", "RED": "✖"}.get(s["risk"], "?")
            print(f"  {icon} {s['vs_name']:<40} {s['members_up']}/{s['members_total']} up — {s['risk']}")
            if s["risk"] != "GREEN":
                print(f"     → {s['recommendation']}")

    # Capacity
    cap = report.get("capacity", {})
    if cap:
        print(f"\n── Capacity ─────────────────────────────────────")
        cap_icon = {"GREEN": "✔", "AMBER": "⚠", "RED": "✖"}.get(cap.get("risk"), "?")
        print(f"  {cap_icon} VDOM {cap.get('vdom')}: {cap.get('vs_count')} VS  "
              f"conn={cap.get('connection_pct', 0):.0%}  ssl={cap.get('ssl_pct', 0):.0%}")
        if cap.get("risk") != "GREEN":
            print(f"     → {cap.get('recommendation')}")

    # Pool sync
    pool_drifts = report.get("pool_sync", [])
    if pool_drifts:
        print(f"\n── Pool sync ({len(pool_drifts)} drift(s)) ─────────────────────")
        for d in pool_drifts:
            print(f"  ⚠  {d['pool_name']}: {d['action']}")

    # Config drift
    drift = report.get("config_drift", {})
    if drift:
        total = drift.get("total_diffs", 0)
        icon  = "✔" if drift.get("clean") else "⚠"
        print(f"\n── Config drift ─────────────────────────────────")
        print(f"  {icon} {total} difference(s) vs last transform output")
        if not drift.get("clean"):
            for item in drift.get("drift_items", []):
                print(f"     DRIFT: {item.get('object_name')}.{item.get('field')}: "
                      f"{item.get('expected')} → {item.get('actual')}")

    # DataScript summary
    ds = report.get("datascripts", [])
    if ds:
        print(f"\n── DataScript assessment ({len(ds)} script(s)) ─────────────")
        for a in ds:
            icon = {"HIGH": "✖", "MEDIUM": "⚠", "LOW": "✔"}.get(a["manual_effort"], "?")
            print(f"  {icon} {a['name']:<40} {a['complexity']:<8} effort={a['manual_effort']}")
            if a["manual_effort"] != "LOW":
                print(f"     → {a['forti_approach'][:100]}")

    # P1/P2 alerts
    alerts = [a for a in report.get("alerts", []) if a["severity"] in ("P1", "P2")]
    if alerts:
        print(f"\n── Active alerts ────────────────────────────────")
        for a in alerts:
            print(f"  {a['severity']} {a['summary']}")
            print(f"     → {a['action']}")

    print(f"\n── Summary ──────────────────────────────────────")
    print(f"  P1 alerts : {summary.get('p1_alerts', 0)}")
    print(f"  P2 alerts : {summary.get('p2_alerts', 0)}")
    print(f"  Pool drifts: {summary.get('pool_drifts', 0)}")
    print(f"  Config drifts: {summary.get('config_drifts', 0)}")
    if summary.get("action_required"):
        print("\n  ⚠  Action required — review items above before next maintenance window.")
    else:
        print("\n  ✔  No immediate action required.")
    print(f"\n  Full report: state/{args.env}-ops-check.json")


# ── Argument parser ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Avi → FortiADC Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default="config.yaml",
                        help="Config file (default: config.yaml)")
    sub = parser.add_subparsers(dest="command")

    # discover
    p = sub.add_parser("discover", help="Query Avi API and collect config")
    p.add_argument("--env",    required=True, help="Environment name from config")
    p.add_argument("--output", help="Output JSON file (default: discovery/<env>.json)")

    # import-avi-json
    p = sub.add_parser("import-avi-json",
                       help="Import normalized discovery JSON (strict contract)")
    p.add_argument("--input", required=True, help="Normalized discovery JSON file")
    p.add_argument("--env", required=True, help="Logical environment name for output naming")
    p.add_argument("--output", help="Output JSON file (default: discovery/<env>.json)")
    p.add_argument(
        "--payload-type",
        default="normalized_discovery",
        choices=["normalized_discovery"],
        help="Import contract payload type (default: normalized_discovery)",
    )

    # analyse
    p = sub.add_parser("analyse", help="Analyse discovery JSON")
    p.add_argument("--input",  required=True, help="Discovery JSON file")
    p.add_argument("--report", help="Report output dir (default: reports/)")

    # transform
    p = sub.add_parser("transform", help="Transform Avi config to FortiADC payloads")
    p.add_argument("--input",  required=True, help="Discovery JSON file")
    p.add_argument("--output", help="Output JSON (default: fortiadc/<env>-config.json)")

    # deploy
    p = sub.add_parser("deploy", help="Deploy to FortiADC")
    p.add_argument("--fortiadc-config", required=True, dest="fortiadc_config")
    p.add_argument("--env",     required=True)
    p.add_argument("--config-file", default="config.yaml", dest="config_file")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--dry-run",  action="store_true", default=True)
    grp.add_argument("--execute",  action="store_true", default=False)
    p.add_argument("--override-governance", action="store_true", default=False,
                   help="Bypass governance gate — requires written justification, always logged")
    p.add_argument("--skip-drift-check", action="store_true", default=False,
                   help="Skip pre-deploy drift detection")
    p.add_argument("--force-on-drift", action="store_true", default=False,
                   help="Deploy even if drift detected (logged)")

    # llm-pack
    p = sub.add_parser("llm-pack", help="Generate sanitized LLM assistance pack")
    p.add_argument("--type",  required=True,
                   choices=["full", "datascript", "next_steps", "manual_item"])
    p.add_argument("--input", help="Discovery JSON file")
    p.add_argument("--name",  help="Object name (for datascript/manual_item types)")

    # ops-check
    p = sub.add_parser("ops-check", help="Run operational health and drift checks")
    p.add_argument("--env", required=True, help="Environment name from config/state")

    # commit-import-scope (v2)
    p = sub.add_parser(
        "commit-import-scope",
        help="Commit staged all-keys import scope after resolved_import_scope approval",
    )
    p.add_argument("--env", required=True, help="Environment name from config/state")

    # audit
    p = sub.add_parser("audit", help="Audit log tools")
    audit_sub = p.add_subparsers(dest="audit_cmd")

    p_chain = audit_sub.add_parser("chain", help="Add hash chain to JSONL log")
    p_chain.add_argument("--log", required=True)
    p_chain.add_argument("--output")

    p_verify = audit_sub.add_parser("verify", help="Verify hash chain integrity")
    p_verify.add_argument("--log", required=True)

    p_cef = audit_sub.add_parser("cef", help="Export JSONL log to CEF")
    p_cef.add_argument("--log", required=True)
    p_cef.add_argument("--output")

    p_summary = audit_sub.add_parser("summary", help="Summarise JSONL log")
    p_summary.add_argument("--log", required=True)

    # rag
    p = sub.add_parser("rag", help="RAG knowledge base — index, search, or ask")
    rag_sub = p.add_subparsers(dest="rag_cmd")

    p_idx = rag_sub.add_parser("index",
        help="Build/rebuild the RAG knowledge base index")
    p_idx.add_argument("--source", dest="sources", action="append",
        metavar="SOURCE",
        help="Source types to index (docs|ledger|discovery|mappings|builtin). "
             "Repeat for multiple. Default: all.")
    p_idx.add_argument("--no-rebuild", dest="rebuild", action="store_false",
        default=True,
        help="Incremental update — do not delete existing chunks first")

    p_search = rag_sub.add_parser("search",
        help="Search the knowledge base without LLM")
    p_search.add_argument("--query", required=True, help="Search query")
    p_search.add_argument("--top-k", type=int, default=5, dest="top_k")
    p_search.add_argument("--source", dest="source_filter", default="",
        metavar="SOURCE",
        help="Limit to source type (docs|ledger|discovery|mappings|builtin)")

    p_ask = rag_sub.add_parser("ask",
        help="Ask a question using RAG (retrieval + optional LLM)")
    p_ask.add_argument("--query", required=True, help="Question to ask")
    p_ask.add_argument("--env",   default="",   help="Scope to a specific environment")
    p_ask.add_argument("--top-k", type=int, default=6, dest="top_k")
    p_ask.add_argument("--no-llm", dest="use_llm", action="store_false", default=True,
        help="Retrieve only — do not call LLM gateway")

    rag_sub.add_parser("stats", help="Show RAG index statistics")

    # decisions
    p = sub.add_parser("decisions", help="Decision manifest operations")
    decision_sub = p.add_subparsers(dest="decision_cmd")

    p_show = decision_sub.add_parser("show", help="Show decision manifest for env")
    p_show.add_argument("--env", required=True, help="Environment name")

    # v2 raw-key import scope helpers
    p_raw_show = decision_sub.add_parser("raw-show", help="Show raw_key_inventory")
    p_raw_show.add_argument("--env", required=True, help="Environment name")

    p_raw_set = decision_sub.add_parser("raw-set", help="Bulk set raw-key decisions by group")
    p_raw_set.add_argument("--env", required=True, help="Environment name")
    p_raw_set.add_argument(
        "--group",
        required=True,
        choices=["pipeline", "gslb_related", "context", "noise"],
        help="Raw-key group to update",
    )
    p_raw_set.add_argument(
        "--decision",
        required=True,
        choices=["include_in_pipeline", "context_only", "exclude"],
        help="Decision value applied to all raw keys in the selected group",
    )

    p_raw_confirm = decision_sub.add_parser("raw-confirm", help="Mark resolved_import_scope=true")
    p_raw_confirm.add_argument("--env", required=True, help="Environment name")

    p_set = decision_sub.add_parser("set", help="Set a decision value")
    p_set.add_argument("--env", required=True, help="Environment name")
    p_set.add_argument("--phase", required=True, choices=[
        "import_scope", "mapping_approvals", "analysis_triage",
        "transform_approvals", "deploy_approval", "post_validation_decision",
    ])
    p_set.add_argument("--key", required=True, help="Decision key or family")
    p_set.add_argument("--value", required=True, help="Decision value")
    p_set.add_argument("--rationale", default="", help="Decision rationale")
    p_set.add_argument("--resolve", action="store_true", default=False, help="Mark phase section resolved")

    p_lock = decision_sub.add_parser("lock", help="Lock manifest for env")
    p_lock.add_argument("--env", required=True, help="Environment name")

    p_trace = decision_sub.add_parser("trace", help="Export decision trace report")
    p_trace.add_argument("--env", required=True, help="Environment name")
    p_trace.add_argument("--output", help="Output markdown path")

    args = parser.parse_args()

    if args.command == "discover":       cmd_discover(args)
    elif args.command == "import-avi-json": cmd_import_avi_json(args)
    elif args.command == "analyse":      cmd_analyse(args)
    elif args.command == "transform":    cmd_transform(args)
    elif args.command == "deploy":       cmd_deploy(args)
    elif args.command == "llm-pack":     cmd_llm_pack(args)
    elif args.command == "ops-check":    cmd_ops_check(args)
    elif args.command == "commit-import-scope": cmd_commit_import_scope(args)
    elif args.command == "audit":        cmd_audit(args)
    elif args.command == "rag":          cmd_rag(args)
    elif args.command == "decisions":    cmd_decisions(args)
    else:
        parser.print_help()


# ── RAG command ────────────────────────────────────────────────────────────────

def cmd_rag(args):
    """RAG knowledge base commands: index / search / ask / stats."""
    dirs = _get_dirs(getattr(args, "config", "config.yaml"))

    rag_cmd = getattr(args, "rag_cmd", None)

    if rag_cmd == "index" or rag_cmd is None:
        _cmd_rag_index(args, dirs)
    elif rag_cmd == "search":
        _cmd_rag_search(args, dirs)
    elif rag_cmd == "ask":
        _cmd_rag_ask(args, dirs)
    elif rag_cmd == "stats":
        _cmd_rag_stats(dirs)
    else:
        print("Usage: migrate.py rag <index|search|ask|stats>")


def _cmd_rag_index(args, dirs: dict):
    from services.rag_service import rebuild_index
    sources = getattr(args, "sources", None)
    rebuild = getattr(args, "rebuild", True)
    print(f"Building RAG index{'  (rebuild=True)' if rebuild else ' (incremental)'}...")
    if sources:
        print(f"Sources: {', '.join(sources)}")
    else:
        print("Sources: all (docs, ledger, discovery, mappings, builtin)")

    result = rebuild_index(dirs, sources=sources, rebuild=rebuild)

    print(f"\n[OK] Indexed {result['indexed']} chunks in {result['duration_ms']}ms")
    print(f"  DB: {result['db_path']}")
    for src, n in sorted(result.get("by_source", {}).items()):
        print(f"    {src:<12} {n:>4} chunks")
    if result.get("errors"):
        print("\nWarnings:")
        for e in result["errors"]:
            print(f"  [!] {e}")


def _cmd_rag_search(args, dirs: dict):
    from services.rag_service import search
    result = search(
        dirs,
        query=args.query,
        top_k=args.top_k,
        source_filter=getattr(args, "source_filter", ""),
    )
    if result.get("error") and not result["results"]:
        print(f"Error: {result['error']}")
        return

    print(f"\nRAG search: '{args.query}'")
    print(f"Found {result['total']} result(s):\n")
    for i, r in enumerate(result["results"], 1):
        print(f"[{i}] {r['title']}")
        print(f"    Source: {r['source_type']} | Score: {r['score']}")
        if r.get("highlights"):
            for h in r["highlights"][:1]:
                print(f"    >> {h[:120]}")
        print()


def _cmd_rag_ask(args, dirs: dict):
    from services.rag_service import ask
    print(f"\nRAG query: '{args.query}'")
    if getattr(args, "env", ""):
        print(f"Scoped to env: {args.env}")
    print("Searching knowledge base...")

    result = ask(
        dirs,
        query=args.query,
        env=getattr(args, "env", ""),
        top_k=args.top_k,
        use_llm=getattr(args, "use_llm", True),
    )

    print(f"\nMode: {result['mode']} | Retrieved: {result['retrieved']} chunks "
          f"| Latency: {result['latency_ms']}ms\n")
    print("=" * 60)
    print(result["answer"])
    print("=" * 60)

    if result.get("sources"):
        print("\nSources:")
        for s in result["sources"]:
            print(f"  - [{s['source_type']}] {s['title']} (score={s['score']})")


def _cmd_rag_stats(dirs: dict):
    from services.rag_service import get_index_stats
    stats = get_index_stats(dirs)

    print("\nRAG Index Statistics")
    print("=" * 40)
    print(f"  Total chunks : {stats.get('total_chunks', 0)}")
    print(f"  Vocabulary   : {stats.get('idf_terms', 0)} terms")
    print(f"  Last indexed : {stats.get('last_indexed', 'never')}")
    if stats.get("age_hours") is not None:
        print(f"  Index age    : {stats['age_hours']} hours")
    print(f"  DB path      : {stats.get('db_path', 'n/a')}")
    by_src = stats.get("by_source", {})
    if by_src:
        print("  By source:")
        for src, n in sorted(by_src.items()):
            print(f"    {src:<12} {n:>4} chunks")
    ready = stats.get("ready", False)
    print(f"\n  Status: {'[OK] Ready' if ready else '[--] Not built -- run: migrate.py rag index'}")


def cmd_decisions(args):
    from core.decision_manifest import VALID_IMPORT_ACTIONS, VALID_POST_ACTIONS, VALID_TRIAGE_ACTIONS
    store = _decision_store(args.env)
    manifest = store.load()

    if args.decision_cmd == "raw-show":
        raw_inv = manifest.get("raw_key_inventory", {})
        print(json.dumps(raw_inv, indent=2))
        return

    if args.decision_cmd == "raw-set":
        raw_inv = manifest.get("raw_key_inventory", {}) or {}
        raw_decisions = manifest.setdefault("raw_key_decisions", {})
        group = args.group
        decision = args.decision

        changed = 0
        for raw_key, inv in raw_inv.items():
            if (inv or {}).get("group") != group:
                continue
            raw_decisions.setdefault(raw_key, {})
            raw_decisions[raw_key]["decision"] = decision
            raw_decisions[raw_key]["rationale"] = f"CLI bulk set for group={group}"
            changed += 1

        manifest["resolved_import_scope"] = False
        store.append_audit(
            manifest,
            "cli_raw_import_scope_bulk_set",
            {"group": group, "decision": decision, "changed_keys": changed},
        )
        print(f"Updated {changed} raw key(s) in group '{group}' to '{decision}'.")
        return

    if args.decision_cmd == "raw-confirm":
        manifest["resolved_import_scope"] = True
        store.append_audit(manifest, "cli_raw_import_scope_confirm", {})
        print("resolved_import_scope=true")
        return

    if args.decision_cmd == "show":
        print(json.dumps(manifest, indent=2))
        return

    if args.decision_cmd == "set":
        phase = args.phase
        key = args.key
        value = args.value
        rationale = args.rationale

        if phase == "import_scope":
            if value not in VALID_IMPORT_ACTIONS:
                print(f"Invalid import scope value: {value}")
                sys.exit(1)
            manifest.setdefault("import_scope", {})
            manifest["import_scope"].setdefault(key, {})
            manifest["import_scope"][key]["decision"] = value
            manifest["import_scope"][key]["rationale"] = rationale
            store.append_audit(manifest, "cli_import_scope_set", {"key": key, "value": value, "rationale": rationale})
            print(f"Updated import_scope.{key}={value}")
            return

        if phase == "mapping_approvals":
            mapping = manifest.setdefault("mapping_approvals", {"resolved": False, "items": {}})
            mapping.setdefault("items", {})
            mapping["items"][key] = {"decision": value, "rationale": rationale}
            if args.resolve:
                mapping["resolved"] = True
            store.append_audit(manifest, "cli_mapping_set", {"key": key, "value": value, "rationale": rationale, "resolve": args.resolve})
            print(f"Updated mapping_approvals.{key}={value}")
            return

        if phase == "analysis_triage":
            if value not in VALID_TRIAGE_ACTIONS:
                print(f"Invalid analysis triage value: {value}")
                sys.exit(1)
            triage = manifest.setdefault("analysis_triage", {"resolved": False, "items": {}})
            triage.setdefault("items", {})
            triage["items"][key] = {"decision": value, "rationale": rationale}
            if args.resolve:
                triage["resolved"] = True
            store.append_audit(manifest, "cli_analysis_triage_set", {"key": key, "value": value, "rationale": rationale, "resolve": args.resolve})
            print(f"Updated analysis_triage.{key}={value}")
            return

        if phase == "transform_approvals":
            transform = manifest.setdefault("transform_approvals", {"resolved": False, "groups": {}})
            transform.setdefault("groups", {})
            transform["groups"][key] = {"decision": value, "rationale": rationale}
            if args.resolve:
                transform["resolved"] = True
            store.append_audit(manifest, "cli_transform_approval_set", {"key": key, "value": value, "rationale": rationale, "resolve": args.resolve})
            print(f"Updated transform_approvals.{key}={value}")
            return

        if phase == "deploy_approval":
            deploy = manifest.setdefault("deploy_approval", {})
            deploy[key] = value
            if args.resolve:
                deploy["resolved"] = True
            if key == "mode" and value not in {"dry_run", "execute"}:
                print("deploy_approval.mode must be dry_run or execute")
                sys.exit(1)
            store.append_audit(manifest, "cli_deploy_approval_set", {"key": key, "value": value, "rationale": rationale, "resolve": args.resolve})
            print(f"Updated deploy_approval.{key}={value}")
            return

        if phase == "post_validation_decision":
            if key == "action" and value not in VALID_POST_ACTIONS:
                print(f"Invalid post_validation action: {value}")
                sys.exit(1)
            post = manifest.setdefault("post_validation_decision", {})
            post[key] = value
            if rationale:
                post["notes"] = rationale
            if args.resolve:
                post["resolved"] = True
            store.append_audit(manifest, "cli_post_validation_set", {"key": key, "value": value, "rationale": rationale, "resolve": args.resolve})
            print(f"Updated post_validation_decision.{key}={value}")
            return

    if args.decision_cmd == "lock":
        store.set_status(manifest, "locked")
        print(f"Manifest locked: {store.path}")
        return

    if args.decision_cmd == "trace":
        from reporters.decision_trace import export_decision_trace
        out = export_decision_trace(args.env, output_path=getattr(args, "output", None))
        print(f"Decision trace exported: {out}")
        return

    print("Usage: migrate.py decisions <show|set|lock> ...")


def cmd_commit_import_scope(args):
    """Commit staged candidate discovery after user approval in manifest."""
    dirs = _get_dirs(getattr(args, "config", "config.yaml"))
    from services.pipeline_service import commit_import_scope

    res = commit_import_scope(dirs, args.env.lower())
    if not res.get("success"):
        print(f"Commit failed: {res.get('error', 'unknown error')}")
        sys.exit(1)
    print(f"Committed discovery for env '{res.get('env')}'")
    print(f"Discovery path: {res.get('path')}")


if __name__ == "__main__":
    main()
