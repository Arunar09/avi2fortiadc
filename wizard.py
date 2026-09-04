#!/usr/bin/env python3
"""
wizard.py — Guided step-by-step migration wizard for Avi → FortiADC.

Replaces the need to know CLI flags. The wizard walks the engineer through
every phase, explains what is about to happen, asks for confirmation,
shows results, and decides what the next step is.

Usage:
    python3 wizard.py

No arguments needed. The wizard handles everything.
"""
from __future__ import annotations

import json
import os
import sys
import time
import textwrap
from pathlib import Path
from typing import Optional

# ── Terminal colours ───────────────────────────────────────────────────────────
USE_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text

def bold(t):    return _c("1", t)
def green(t):   return _c("32", t)
def yellow(t):  return _c("33", t)
def red(t):     return _c("31", t)
def cyan(t):    return _c("36", t)
def magenta(t): return _c("35", t)
def dim(t):     return _c("2", t)


# ── UI primitives ──────────────────────────────────────────────────────────────

def banner(title: str, subtitle: str = "") -> None:
    width = 64
    print()
    print("═" * width)
    print(f"  {bold(title)}")
    if subtitle:
        print(f"  {dim(subtitle)}")
    print("═" * width)


def section(title: str) -> None:
    print(f"\n{bold('▸ ' + title)}")
    print("─" * 56)


def info(msg: str) -> None:
    print(f"  {cyan('ℹ')}  {msg}")


def warn(msg: str) -> None:
    print(f"  {yellow('⚠')}  {yellow(msg)}")


def ok(msg: str) -> None:
    print(f"  {green('✔')}  {msg}")


def err(msg: str) -> None:
    print(f"  {red('✖')}  {red(msg)}")


def explain(text: str) -> None:
    """Print an explanation block — wrapped, indented."""
    print()
    for line in textwrap.wrap(text, width=60):
        print(f"  {dim(line)}")
    print()


def bullet_list(title: str, items: list[str]) -> None:
    if not items:
        return
    section(title)
    for item in items:
        print(f"  - {item}")


def command_preview(label: str, args: list[str]) -> None:
    section(label)
    print(f"  {cyan(sys.executable + ' migrate.py ' + ' '.join(args))}")


def artifact_list(title: str, items: list[str]) -> None:
    if not items:
        return
    section(title)
    for item in items:
        print(f"  {green('•')} {item}")


def env_summary(cfg: dict, env: str) -> None:
    env_cfg = next((e for e in cfg.get("environments", []) if e["name"] == env), {})
    section("Environment mapping")
    info(f"Environment    : {env}")
    info(f"AVI tenant     : {env_cfg.get('avi_tenant', 'admin')}")
    info(f"FortiADC VDOM  : {env_cfg.get('fortiadc_vdom', cfg.get('fortiadc', {}).get('vdom', 'root'))}")
    info(f"AVI version    : {cfg.get('avi', {}).get('api_version', 'unknown')}")
    info(f"AVI controller : {cfg.get('avi', {}).get('controller', '?')}")
    info(f"FortiADC host  : {cfg.get('fortiadc', {}).get('host', '?')}")


def readiness_check(title: str, checks: list[str]) -> None:
    section(title)
    for check in checks:
        print(f"  {yellow('□')} {check}")


def step_header(step_num: int, total: int, title: str, description: str) -> None:
    """Print a prominent step header with full context."""
    print()
    print("╔" + "═" * 62 + "╗")
    print(f"║  {bold(f'STEP {step_num} of {total}  —  {title}'):<62}║")
    print("╠" + "═" * 62 + "╣")
    for line in textwrap.wrap(description, width=58):
        print(f"║  {line:<60}║")
    print("╚" + "═" * 62 + "╝")


def ask(prompt: str, choices: list[str] | None = None,
        default: str | None = None) -> str:
    """Prompt user for input with optional choices and default."""
    if choices:
        choice_str = "/".join(
            bold(c) if c == default else c for c in choices
        )
        full_prompt = f"\n  {bold('?')} {prompt} [{choice_str}]: "
    else:
        default_hint = f" (default: {bold(default)})" if default else ""
        full_prompt = f"\n  {bold('?')} {prompt}{default_hint}: "

    while True:
        try:
            raw = input(full_prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nAborted by user.")
            sys.exit(0)

        if not raw and default is not None:
            return default
        if choices and raw.lower() not in [c.lower() for c in choices]:
            print(f"  {red('Please enter one of:')} {', '.join(choices)}")
            continue
        if raw:
            return raw.lower() if choices else raw
        print(f"  {yellow('Required — please enter a value.')}")


def confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no confirmation."""
    d = "Y" if default else "N"
    ans = ask(prompt, choices=["y", "n"], default=d.lower())
    return ans == "y"


def pause(msg: str = "Press ENTER to continue...") -> None:
    try:
        input(f"\n  {dim(msg)}")
    except (KeyboardInterrupt, EOFError):
        print("\n\nAborted.")
        sys.exit(0)


def spinner(msg: str, seconds: float = 0.8) -> None:
    """Brief visual pause to indicate background work."""
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    end = time.time() + seconds
    i = 0
    while time.time() < end:
        print(f"\r  {cyan(frames[i % len(frames)])}  {msg}", end="", flush=True)
        time.sleep(0.08)
        i += 1
    print(f"\r  {green('✔')}  {msg}            ")


# ── State management ──────────────────────────────────────────────────────────

STATE_FILE = Path("state/wizard_state.json")

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def mark_step_done(state: dict, env: str, step: str) -> None:
    state.setdefault("completed", {}).setdefault(env, [])
    if step not in state["completed"][env]:
        state["completed"][env].append(step)
    save_state(state)


def is_done(state: dict, env: str, step: str) -> bool:
    return step in state.get("completed", {}).get(env, [])


# ── Config helpers ─────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    try:
        import yaml
        return yaml.safe_load(Path(path).read_text())
    except FileNotFoundError:
        return {}
    except ImportError:
        err("PyYAML not installed. Run: pip install --no-index "
            "--find-links vendor/ pyyaml")
        sys.exit(1)


def available_envs(cfg: dict) -> list[dict]:
    return sorted(cfg.get("environments", []),
                  key=lambda e: e.get("priority", 99))


# ── Phase runners ──────────────────────────────────────────────────────────────

def run_phase(cmd: list[str]) -> int:
    """Run a migrate.py command and return exit code."""
    import subprocess
    print()
    result = subprocess.run(
        [sys.executable, "migrate.py"] + cmd,
        cwd=Path(__file__).parent,
    )
    return result.returncode


def phase_setup(state: dict) -> dict:
    """Initial setup — config check, environment selection."""
    banner(
        "AVI → FortiADC Migration Wizard",
        "Guided, step-by-step migration tool  |  v0.2"
    )

    explain(
        "Welcome. This wizard will guide you through every step of the "
        "migration from VMware AVI to FortiADC. You will be shown exactly "
        "what is about to happen before anything runs, and asked to confirm "
        "at each stage. Nothing touches production unless you explicitly "
        "approve it."
    )

    # Check config file
    section("Configuration check")
    cfg_path = "config.yaml"
    if not Path(cfg_path).exists():
        warn("config.yaml not found.")
        explain(
            "You need to create config.yaml from the example file. "
            "Open config.example.yaml, fill in your AVI controller address, "
            "FortiADC address, credentials, and environment names, then save "
            "as config.yaml in this directory."
        )
        if confirm("Open config.example.yaml content now for reference?"):
            example = Path("config.example.yaml").read_text()
            print("\n" + dim(example))
        print()
        err("Cannot continue without config.yaml. Create it and run wizard again.")
        sys.exit(1)

    ok("config.yaml found")
    cfg = load_config(cfg_path)

    envs = available_envs(cfg)
    if not envs:
        err("No environments defined in config.yaml.")
        sys.exit(1)

    ok(f"{len(envs)} environment(s) configured")

    # Show environment list
    section("Available environments")
    for i, env in enumerate(envs, 1):
        status_parts = []
        env_name = env["name"]
        done_steps = state.get("completed", {}).get(env_name, [])
        if done_steps:
            status_parts.append(green(f"✔ {', '.join(done_steps)}"))
        else:
            status_parts.append(dim("not started"))
        print(f"  {i}. {bold(env_name):<20} "
              f"tenant: {env.get('avi_tenant','?'):<20} "
              f"{' | '.join(status_parts)}")

    print()
    explain(
        "Always migrate dev/sandpit environments first and validate for "
        "at least 7 days before proceeding to production. The priority "
        "order shown above reflects the recommended sequence."
    )

    # Select environment
    env_names = [e["name"] for e in envs]
    chosen_name = ask(
        f"Which environment to work on?",
        choices=env_names,
        default=env_names[0]
    )
    chosen = next(e for e in envs if e["name"] == chosen_name)

    state["current_env"] = chosen_name
    save_state(state)

    ok(f"Working on environment: {bold(chosen_name)}")
    env_summary(cfg, chosen_name)
    readiness_check("Before you start", [
        "Confirm AVI read-only access works for the selected tenant.",
        "Confirm the selected AVI tenant maps to the correct FortiADC VDOM.",
        "Confirm FortiADC credentials are available for dry-run and deploy.",
        "Use environment-scoped runs unless partial migration has been engineered and approved.",
    ])
    return cfg, chosen_name


def phase_discover(cfg: dict, env: str, state: dict) -> bool:
    total_steps = 7
    step_header(
        1, total_steps,
        "DISCOVER",
        "Connect to AVI and collect a complete snapshot of all load "
        "balancer configuration: virtual services, pools, health monitors, "
        "SSL profiles, certificates, WAF policies, and DataScripts. "
        "This is READ-ONLY — nothing is changed in AVI."
    )

    out_path = f"discovery/{env}.json"

    if is_done(state, env, "discover") and Path(out_path).exists():
        warn(f"Discovery already completed for '{env}'.")
        explain(
            f"Found existing discovery file at {out_path}. "
            "You can re-run discovery to refresh the snapshot, or skip "
            "to the next step if nothing has changed in AVI."
        )
        if not confirm("Re-run discovery (overwrites existing snapshot)?", default=False):
            ok("Skipping discovery — using existing snapshot.")
            return True

    explain(
        "The tool will now connect to your AVI controller and retrieve all "
        "configuration objects. This typically takes 30–120 seconds depending "
        "on the number of virtual services. The controller is accessed read-only "
        "using the credentials in config.yaml."
    )

    info(f"AVI controller : {cfg.get('avi', {}).get('controller', '?')}")
    info(f"Tenant         : {next((e.get('avi_tenant','admin') for e in cfg.get('environments',[]) if e['name']==env), 'admin')}")
    info(f"Output file    : {out_path}")
    command_preview("CLI command this step will run", ["discover", "--env", env, "--output", out_path])
    artifact_list("Expected outputs", [
        out_path,
        f"logs/{env}-discover.jsonl",
    ])

    if not confirm("Proceed with discovery?"):
        info("Discovery skipped. Run wizard again when ready.")
        return False

    rc = run_phase(["discover", "--env", env, "--output", out_path])
    if rc != 0:
        err("Discovery failed. Check the error above.")
        explain(
            "Common causes: wrong controller IP, credentials incorrect, "
            "SSL verification failure (set verify_ssl: false in config.yaml "
            "if using self-signed cert), or network connectivity issue from "
            "this host to the AVI controller."
        )
        return False

    mark_step_done(state, env, "discover")
    ok(f"Discovery complete. Snapshot saved to: {out_path}")
    return True


def phase_analyse(cfg: dict, env: str, state: dict) -> bool:
    total_steps = 7
    step_header(
        2, total_steps,
        "ANALYSE",
        "Build a complete dependency graph of all AVI objects. Run "
        "compatibility checks against FortiADC capabilities. Detect "
        "migration patterns and risks. Score overall complexity. "
        "Produce HTML, Markdown, and sanitized reports."
    )

    in_path = f"discovery/{env}.json"
    if not Path(in_path).exists():
        err(f"Discovery file not found: {in_path}")
        err("Run Step 1 (Discover) first.")
        return False

    explain(
        "The analyser reads the discovery snapshot and checks every object "
        "against the AVI-to-FortiADC mapping tables. It flags anything that "
        "cannot be auto-migrated (BLOCKED or MANUAL items) and produces a "
        "prioritised list of actions you need to take before migration can proceed. "
        "No network calls are made during analysis — it works entirely from the "
        "saved snapshot."
    )

    info(f"Input          : {in_path}")
    info(f"Reports output : reports/")
    command_preview("CLI command this step will run", ["analyse", "--input", in_path, "--report", "reports"])
    artifact_list("Expected outputs", [
        f"reports/{env}-analysis.html",
        f"reports/{env}-analysis.md",
        f"reports/{env}-sanitized.txt",
    ])

    if not confirm("Run analysis?"):
        return False

    rc = run_phase(["analyse", "--input", in_path, "--report", "reports"])
    if rc != 0:
        err("Analysis failed. Check error above.")
        return False

    mark_step_done(state, env, "analyse")

    # Show report locations
    section("Analysis reports generated")
    ok(f"  reports/{env}-analysis.html   — Open in browser for full visual report")
    ok(f"  reports/{env}-analysis.md     — Attach to your change request")
    ok(f"  reports/{env}-sanitized.txt   — Paste into LLM for assistance on manual items")

    # Check for blockers
    report_md = Path(f"reports/{env}-analysis.md")
    if report_md.exists():
        content = report_md.read_text()
        blocked_count = content.count("BLOCKED")
        manual_count  = content.count("MANUAL")
        if blocked_count > 0:
            warn(f"{blocked_count} BLOCKED item(s) found — migration cannot proceed until resolved.")
            explain(
                "BLOCKED items are hard blockers. Examples: HSM certificates whose "
                "private keys cannot be exported, DataScripts with no FortiADC equivalent, "
                "unsupported health monitor types. Each must be resolved manually before "
                "this tool can continue."
            )
        if manual_count > 0:
            info(f"{manual_count} MANUAL item(s) found — require human action but do not block the tool.")

    explain(
        "Review the HTML report carefully before proceeding. Pay particular attention "
        "to BLOCKED items (red) and MANUAL items (cyan). Document your plan for each "
        "in your change request before requesting the maintenance window."
    )

    pause("Review the analysis report, then press ENTER to continue to Transform.")
    return True


def phase_transform(cfg: dict, env: str, state: dict) -> bool:
    total_steps = 7
    step_header(
        3, total_steps,
        "TRANSFORM",
        "Translate every AVI object into its FortiADC equivalent. "
        "Generates a complete FortiADC configuration file with all "
        "virtual servers, pools, health checks, and SSL profiles "
        "ready to deploy. MANUAL items are skipped and flagged."
    )

    in_path  = f"discovery/{env}.json"
    out_path = f"fortiadc/{env}-config.json"

    if not Path(in_path).exists():
        err("Discovery file not found. Run Step 1 first.")
        return False

    explain(
        "The transformer converts AVI configuration objects to FortiADC "
        "REST API payloads using the explicit mapping tables in "
        "transformers/mappings.py. Every mapping decision is recorded in the "
        "event log. Items that cannot be automatically translated are "
        "marked MANUAL and skipped — they will need to be configured "
        "in FortiADC by hand after deployment."
    )

    info(f"Input          : {in_path}")
    info(f"Output         : {out_path}")
    readiness_check("What to verify before transform", [
        "Analysis has been reviewed for BLOCKED and MANUAL items.",
        "The selected environment and tenant are still correct.",
        "You are generating an environment payload, not an ad hoc VS subset.",
    ])
    command_preview("CLI command this step will run", ["transform", "--input", in_path, "--output", out_path])
    artifact_list("Expected outputs", [
        out_path,
        f"logs/{env}-transform.jsonl",
    ])

    if not confirm("Run transformation?"):
        return False

    rc = run_phase(["transform", "--input", in_path, "--output", out_path])
    if rc != 0:
        err("Transformation failed.")
        return False

    mark_step_done(state, env, "transform")

    if Path(out_path).exists():
        config = json.loads(Path(out_path).read_text())
        section("Transformation summary")
        ok(f"  SSL certificates   : {len(config.get('ssl_certificates', []))}")
        ok(f"  Health checks      : {len(config.get('health_checks', []))}")
        ok(f"  Real server pools  : {len(config.get('real_server_pools', []))}")
        ok(f"  Virtual servers    : {len(config.get('virtual_servers', []))}")

        # Unsupported tracker
        _show_unsupported(env)

    explain(
        "Review the generated config file before deploying. The file at "
        f"{out_path} contains all objects that will be created in FortiADC. "
        "You can edit this file before deployment to adjust any settings, "
        "add MANUAL items, or fix any translation you disagree with."
    )
    warn("Avoid manually trimming this file for one-by-one VS migration unless all dependencies and shared objects have been reviewed explicitly.")

    if confirm(f"Open {out_path} path for manual review before continuing?", default=False):
        print(f"\n  Location: {Path(out_path).absolute()}")
        pause("Make any edits needed, save the file, then press ENTER.")

    return True


def _show_unsupported(env: str) -> None:
    """Show unsupported items from the state ledger."""
    unsupported_path = Path(f"state/{env}-unsupported.json")
    if not unsupported_path.exists():
        return
    try:
        items = json.loads(unsupported_path.read_text())
        if items:
            section("Unsupported items — manual action required")
            for item in items:
                print(f"  {red('✖')} {item['object_type']}: {bold(item['object_name'])}")
                print(f"     Reason : {item['reason']}")
                print(f"     Action : {yellow(item['action'])}")
                print()
    except Exception:
        pass


def phase_dry_run(cfg: dict, env: str, state: dict) -> bool:
    total_steps = 7
    step_header(
        4, total_steps,
        "DRY-RUN DEPLOY",
        "Simulate deployment to FortiADC without making any real changes. "
        "The tool connects to FortiADC, runs all pre-flight checks, and "
        "shows exactly what API calls would be made — but does not execute them. "
        "Use this to validate the config before the maintenance window."
    )

    fortiadc_cfg = f"fortiadc/{env}-config.json"
    if not Path(fortiadc_cfg).exists():
        err("FortiADC config not found. Run Step 3 (Transform) first.")
        return False

    explain(
        "The dry-run connects to FortiADC and checks: API reachability, "
        "VDOM existence, no name conflicts with existing objects, and "
        "that all required interfaces and routes exist. "
        "No objects are created or modified."
    )

    info(f"FortiADC host  : {cfg.get('fortiadc', {}).get('host', '?')}")
    info(f"Config file    : {fortiadc_cfg}")
    info(f"Mode           : DRY-RUN (no changes)")
    readiness_check("Dry-run checklist", [
        "FortiADC API should be reachable from this host.",
        "The target VDOM should already exist.",
        "Dry-run validates readiness, not full application behavior.",
    ])
    command_preview("CLI command this step will run", [
        "deploy",
        "--fortiadc-config", fortiadc_cfg,
        "--env", env,
        "--dry-run",
    ])
    artifact_list("Expected outputs", [
        f"logs/{env}-deploy.jsonl",
    ])

    if not confirm("Run dry-run validation?"):
        return False

    rc = run_phase([
        "deploy",
        "--fortiadc-config", fortiadc_cfg,
        "--env", env,
        "--dry-run"
    ])

    if rc != 0:
        err("Dry-run found issues. Review errors above before proceeding.")
        explain(
            "Fix all blocking issues shown above. Common problems: VDOM does not exist, "
            "FortiADC interface names don't match config.yaml network_map, "
            "or VIP addresses conflict with existing FortiADC objects."
        )
        return False

    mark_step_done(state, env, "dry-run")
    ok("Dry-run passed — FortiADC is ready for deployment.")
    explain(
        "The dry-run has validated that your FortiADC is ready and the "
        "generated configuration is compatible. You are now ready to request "
        "a maintenance window and perform the live deployment."
    )
    return True


def phase_deploy(cfg: dict, env: str, state: dict) -> bool:
    total_steps = 7
    step_header(
        5, total_steps,
        "DEPLOY  ⚡ LIVE",
        "Deploy the FortiADC configuration for real. This creates all "
        "objects in FortiADC: virtual servers, pools, health checks, "
        "SSL profiles. AVI remains live — traffic is NOT switched yet. "
        "This step requires an approved change request and maintenance window."
    )

    fortiadc_cfg = f"fortiadc/{env}-config.json"
    if not Path(fortiadc_cfg).exists():
        err("FortiADC config not found. Run Step 3 first.")
        return False

    if not is_done(state, env, "dry-run"):
        warn("Dry-run has not been completed for this environment.")
        explain(
            "It is strongly recommended to run a dry-run (Step 4) before "
            "live deployment. The dry-run validates FortiADC readiness and "
            "catches configuration errors without risk."
        )
        if not confirm("Skip dry-run and deploy anyway? (NOT recommended)", default=False):
            return False

    warn("THIS WILL MAKE REAL CHANGES TO FORTIADC.")
    explain(
        "Deployment will create objects in FortiADC. AVI will remain live "
        "and continue to serve traffic — this is a parallel deployment, not "
        "a cutover. Traffic switch happens in Step 6 (DNS Cutover). "
        "You can roll back at any time using: bash scripts/rollback.sh"
    )

    info(f"FortiADC host  : {cfg.get('fortiadc', {}).get('host', '?')}")
    info(f"Environment    : {env}")
    info(f"Config file    : {fortiadc_cfg}")
    readiness_check("Live deploy checklist", [
        "Approved change request is available.",
        "Segregation-of-duties approver is available.",
        "Dry-run has been reviewed and blockers are resolved.",
        "Rollback path is understood before live execution.",
    ])
    command_preview("CLI command this step will run", [
        "deploy",
        "--fortiadc-config", fortiadc_cfg,
        "--env", env,
        "--execute",
    ])

    # Double confirmation for live deploy
    if not confirm("Are you inside an approved maintenance window?"):
        info("Deployment aborted. Request a maintenance window first.")
        return False

    confirm_text = ask(
        f"Type the environment name '{bold(env)}' to confirm live deployment"
    )
    if confirm_text != env:
        err(f"Input '{confirm_text}' does not match environment '{env}'. Aborted.")
        return False

    rc = run_phase([
        "deploy",
        "--fortiadc-config", fortiadc_cfg,
        "--env", env,
        "--execute"
    ])

    if rc != 0:
        err("Deployment failed. Check errors above.")
        warn("Run rollback if partial deployment occurred:")
        print(f"    bash scripts/rollback.sh --env {env} --execute")
        return False

    mark_step_done(state, env, "deploy")
    ok("FortiADC objects deployed successfully.")

    # Generate rollback script immediately after deploy
    _generate_rollback(env, fortiadc_cfg)

    explain(
        "Deployment is complete. AVI is still serving all traffic. "
        "FortiADC is now configured but not receiving traffic. "
        "Step 6 (Parallel Run) validates FortiADC health for 7 days "
        "before the DNS cutover switches real traffic."
    )
    return True


def _generate_rollback(env: str, fortiadc_cfg: str) -> None:
    """Generate environment-specific rollback script after deploy."""
    try:
        from reporters.rollback import generate_rollback_script
        cfg_data = json.loads(Path(fortiadc_cfg).read_text())
        script = generate_rollback_script(env, cfg_data)
        out = Path(f"scripts/rollback-{env}.sh")
        out.write_text(script)
        out.chmod(0o750)
        ok(f"Rollback script generated: {out}")
    except Exception as e:
        warn(f"Could not generate rollback script: {e}")


def phase_parallel_run(cfg: dict, env: str, state: dict) -> bool:
    total_steps = 7
    step_header(
        6, total_steps,
        "PARALLEL RUN  (7 days)",
        "FortiADC is deployed and AVI is still live. Run health checks "
        "against FortiADC daily for 7 days to confirm all virtual servers "
        "are healthy before switching any traffic. Nothing changes in DNS yet."
    )

    explain(
        "During the parallel run, both AVI and FortiADC are running simultaneously. "
        "AVI serves all real traffic. FortiADC is validated in parallel with no "
        "production impact. The daily check script verifies every FortiADC virtual "
        "server is healthy. After 7 days of clean checks, the DNS cutover (Step 7) "
        "switches real traffic to FortiADC."
    )

    section("Daily check command")
    print(f"  Run this every day during the 7-day parallel run period:")
    print()
    print(f"  {cyan('bash scripts/parallel-run-check.sh')} {env}")
    print()
    info("Results are saved to logs/parallel-run-<date>.log")
    info("All virtual servers must show OK for 7 consecutive days.")

    # Show days completed from state
    parallel_days = state.get("parallel_days", {}).get(env, 0)
    if parallel_days > 0:
        ok(f"Parallel run progress: {parallel_days}/7 days completed")
    else:
        info("Parallel run not yet started for this environment.")

    days_input = ask(
        "How many parallel run days have been completed? (0–7)",
        default=str(parallel_days)
    )
    try:
        days = int(days_input)
        days = max(0, min(7, days))
    except ValueError:
        days = parallel_days

    state.setdefault("parallel_days", {})[env] = days
    save_state(state)

    if days < 7:
        remaining = 7 - days
        warn(f"{remaining} more day(s) of parallel run required before DNS cutover.")
        explain(
            f"Return to this wizard in {remaining} day(s) after completing daily "
            "parallel-run-check.sh runs. Do not proceed to DNS cutover until 7 "
            "clean days are recorded."
        )
        return False

    mark_step_done(state, env, "parallel-run")
    ok("7-day parallel run complete. Ready for DNS cutover.")
    readiness_check("Before DNS cutover", [
        "Synthetic and application-level validation should be complete.",
        "Rollback script should already be tested in dry-run mode.",
        "NOC and application owners should be aligned for the cutover.",
    ])
    return True


def phase_dns_cutover(cfg: dict, env: str, state: dict) -> bool:
    total_steps = 7
    step_header(
        7, total_steps,
        "DNS CUTOVER  ⚡ TRAFFIC SWITCH",
        "Switch real traffic from AVI to FortiADC by updating DNS records "
        "in Infoblox. This is the point of no return for traffic. "
        "AVI remains running as fallback until validation is complete. "
        "Requires approved change window and NOC on standby."
    )

    warn("THIS SWITCHES REAL USER TRAFFIC TO FORTIADC.")
    explain(
        "The DNS cutover script lowers TTLs, waits for propagation, then "
        "updates all VIP A-records in Infoblox to point to FortiADC. "
        "AVI remains running and can be reverted to immediately if needed "
        "by running the rollback script. All steps are logged."
    )

    # Pre-cutover checklist
    section("Pre-cutover checklist — confirm each item")
    checklist = [
        ("Change request approved and active?", True),
        ("NOC or on-call engineer available?", True),
        ("Rollback script tested (dry-run)?", True),
        ("FortiADC parallel run 7 days complete?", True),
        ("AVI controllers confirmed healthy?", True),
        ("Infoblox credentials available?", True),
        (f"Maintenance window is active for '{env}'?", True),
    ]
    for item, required in checklist:
        ans = confirm(f"  ✓ {item}", default=False)
        if not ans and required:
            err(f"Cannot proceed: {item}")
            return False

    info(f"DNS cutover script: scripts/dns-cutover.sh")
    info(f"Log output        : logs/{env}-dns-cutover.log")
    section("Commands used in this step")
    print(f"  {cyan('bash scripts/dns-cutover.sh --env ' + env + ' --dry-run')}")
    print(f"  {cyan('bash scripts/dns-cutover.sh --env ' + env + ' --execute')}")

    if not confirm(
        f"Type 'yes' to confirm DNS cutover for '{bold(env)}'",
    ):
        return False

    # Dry-run first
    section("Running DNS cutover in DRY-RUN mode first")
    explain(
        "The script will first simulate all DNS changes and show you "
        "every record that will be modified. Review carefully, then "
        "confirm to execute the real cutover."
    )

    import subprocess
    dry = subprocess.run(
        ["bash", "scripts/dns-cutover.sh", "--env", env, "--dry-run"],
        cwd=Path(__file__).parent,
    )

    if dry.returncode != 0:
        err("DNS cutover dry-run failed. Check errors above.")
        return False

    if not confirm("Dry-run complete. Execute real DNS cutover now?", default=False):
        info("DNS cutover aborted. Run wizard again when ready.")
        return False

    live = subprocess.run(
        ["bash", "scripts/dns-cutover.sh", "--env", env, "--execute"],
        cwd=Path(__file__).parent,
    )

    if live.returncode != 0:
        err("DNS cutover encountered errors. Check log and consider rollback.")
        warn(f"Rollback: bash scripts/rollback-{env}.sh --execute")
        return False

    mark_step_done(state, env, "dns-cutover")
    ok("DNS cutover complete. Traffic is now routing through FortiADC.")
    explain(
        "Monitor traffic and error rates for 30 minutes after cutover. "
        "AVI is still running and can take traffic back immediately if "
        "you run the rollback script. After 24 hours of healthy FortiADC "
        "traffic, you can schedule AVI decommission."
    )
    return True


def phase_complete(env: str, state: dict) -> None:
    done = state.get("completed", {}).get(env, [])
    banner(
        f"Migration complete for '{env}'",
        "All steps finished successfully"
    )
    section("Completed steps")
    for step in done:
        ok(f"  {step}")

    section("Next actions")
    info("1. Monitor FortiADC traffic for 24 hours")
    info("2. Confirm application teams see no errors")
    info("3. Raise AVI decommission ticket (keep AVI running for 7 more days)")
    info("4. Run wizard again for next environment")

    all_envs = list(state.get("completed", {}).keys())
    print()
    print(dim("  Reports are in: reports/"))
    print(dim("  Logs are in:    logs/"))
    print(dim(f"  Rollback:       scripts/rollback-{env}.sh --execute"))
    print()


# ── Main wizard loop ───────────────────────────────────────────────────────────

def main():
    state = load_state()

    # Setup
    result = phase_setup(state)
    if isinstance(result, tuple):
        cfg, env = result
    else:
        sys.exit(0)

    state = load_state()  # reload after env selection

    # Determine where we are in the pipeline
    done = state.get("completed", {}).get(env, [])

    banner(f"Environment: {env}", f"Completed steps: {', '.join(done) if done else 'none'}")
    env_summary(cfg, env)

    phases = [
        ("discover",      "Step 1: Discover AVI config",       phase_discover),
        ("analyse",       "Step 2: Analyse compatibility",      phase_analyse),
        ("transform",     "Step 3: Transform to FortiADC",      phase_transform),
        ("dry-run",       "Step 4: Dry-run validation",         phase_dry_run),
        ("deploy",        "Step 5: Live deployment to FortiADC",phase_deploy),
        ("parallel-run",  "Step 6: 7-day parallel run",         phase_parallel_run),
        ("dns-cutover",   "Step 7: DNS cutover",                phase_dns_cutover),
    ]

    section("Migration pipeline")
    for step_id, label, _ in phases:
        status = green("✔ done") if step_id in done else dim("○ pending")
        print(f"  {status}  {label}")

    next_pending = next((label for step_id, label, _ in phases if step_id not in done),
                        "All steps complete")
    section("Next action")
    info(f"Wizard will guide you into: {next_pending}")
    info("Use this wizard for environment-scoped runs. Partial single-VS migration should be treated as unsupported unless scoped migration support is engineered and approved.")

    print()
    if not confirm("Continue with the next pending step?"):
        info("Wizard paused. Run again to resume.")
        sys.exit(0)

    # Run phases in order, starting from first incomplete
    for step_id, label, fn in phases:
        if step_id in done:
            continue  # already done, skip
        ok_result = fn(cfg, env, state)
        state = load_state()  # reload after each step
        if not ok_result:
            warn(f"Stopped at: {label}")
            explain(
                "Resolve the issues above and run the wizard again. "
                "The wizard will resume from where you left off."
            )
            sys.exit(1)
        # Ask before moving to next step
        next_steps = [p for p in phases if p[0] not in state.get("completed", {}).get(env, [])]
        if next_steps and next_steps[0][0] != step_id:
            if not confirm(f"Proceed to next step?"):
                info("Wizard paused. Run again to resume.")
                sys.exit(0)

    phase_complete(env, state)


if __name__ == "__main__":
    main()
