#!/usr/bin/env bash
# scripts/rollback.sh
# ─────────────────────────────────────────────────────────────────────────────
# AVI → FortiADC Rollback Script
# Reverts traffic from FortiADC back to AVI by:
#   1. Disabling all FortiADC virtual servers for the environment
#   2. Restoring Infoblox DNS records to AVI VIPs (from backup)
#   3. Verifying AVI is healthy
#
# USAGE:
#   bash scripts/rollback.sh --env <env> --dry-run    # simulate (default)
#   bash scripts/rollback.sh --env <env> --execute    # real rollback
#
# REQUIRED FILES:
#   fortiadc/<env>-config.json       — list of FortiADC objects to disable
#   state/<env>-dns-backup.json      — DNS backup from dns-cutover.sh
#   config.yaml                      — FortiADC credentials
#
# DESIGN:
#   - Safe to run at any point in the migration (before or after cutover)
#   - Idempotent — re-running does not cause additional harm
#   - All actions logged to logs/<env>-rollback.log
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; }
step() { echo -e "\n${BOLD}─── $* ───${NC}"; }

# ── Args ──────────────────────────────────────────────────────────────────────
ENV=""
DRY_RUN=true

usage() {
    echo "Usage: $0 --env <environment> [--dry-run | --execute]"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)     ENV="${2:?}"; shift 2 ;;
        --dry-run) DRY_RUN=true;  shift ;;
        --execute) DRY_RUN=false; shift ;;
        --help|-h) usage ;;
        *) echo "Unknown: $1"; usage ;;
    esac
done

[[ -z "$ENV" ]] && { fail "--env required"; usage; }

# ── Setup ─────────────────────────────────────────────────────────────────────
mkdir -p logs state
LOG_FILE="logs/${ENV}-rollback.log"
MODE=$( $DRY_RUN && echo "DRY-RUN" || echo "EXECUTE" )
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  AVI → FortiADC ROLLBACK"
echo "  Environment : $ENV"
echo "  Mode        : $MODE"
echo "  Started     : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "══════════════════════════════════════════════════════════════"

if ! $DRY_RUN; then
    warn "ROLLBACK WILL DISABLE FORTIADC VIRTUAL SERVERS AND RESTORE DNS TO AVI."
    echo ""
    read -r -p "  Type 'ROLLBACK' to confirm: " CONFIRM
    [[ "$CONFIRM" == "ROLLBACK" ]] || { info "Aborted."; exit 0; }
fi

# ── Load config ───────────────────────────────────────────────────────────────
step "Loading configuration"

FORTIADC_CFG="fortiadc/${ENV}-config.json"
DNS_BACKUP="state/${ENV}-dns-backup.json"

[[ -f "$FORTIADC_CFG" ]] && ok "FortiADC config found: $FORTIADC_CFG" \
                          || warn "FortiADC config not found — cannot disable VS"

[[ -f "$DNS_BACKUP" ]] && ok "DNS backup found: $DNS_BACKUP" \
                        || warn "DNS backup not found — DNS rollback skipped"

# Load FortiADC credentials from config.yaml
FADC_HOST=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print(cfg.get('fortiadc', {}).get('host', ''))
" 2>/dev/null || echo "")

FADC_USER=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print(cfg.get('fortiadc', {}).get('username', 'admin'))
" 2>/dev/null || echo "admin")

FADC_PASS=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print(cfg.get('fortiadc', {}).get('password', ''))
" 2>/dev/null || echo "")

FADC_VDOM=$(python3 -c "
import yaml, json
cfg = yaml.safe_load(open('config.yaml'))
envs = {e['name']: e for e in cfg.get('environments', [])}
env  = envs.get('$ENV', {})
print(env.get('fortiadc_vdom', cfg.get('fortiadc', {}).get('vdom', 'root')))
" 2>/dev/null || echo "root")

[[ -n "$FADC_HOST" ]] && info "FortiADC: $FADC_HOST (VDOM: $FADC_VDOM)" \
                       || warn "FortiADC host not found in config.yaml"

# ── Step 1: Disable FortiADC virtual servers ──────────────────────────────────
step "Step 1: Disable FortiADC virtual servers"

if [[ -f "$FORTIADC_CFG" && -n "$FADC_HOST" ]]; then
    VS_NAMES=$(python3 -c "
import json
cfg = json.load(open('$FORTIADC_CFG'))
for vs in cfg.get('virtual_servers', []):
    print(vs.get('name', ''))
" 2>/dev/null || echo "")

    VS_COUNT=$(echo "$VS_NAMES" | grep -c . || echo 0)
    info "$VS_COUNT virtual server(s) to disable"

    # Get auth token from FortiADC
    if ! $DRY_RUN && [[ -n "$FADC_HOST" && -n "$FADC_PASS" ]]; then
        TOKEN=$(curl -sk -X POST \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"$FADC_USER\",\"password\":\"$FADC_PASS\"}" \
            "${FADC_HOST}/api/user/login" 2>/dev/null | \
            python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" \
            2>/dev/null || echo "")
        [[ -n "$TOKEN" ]] && ok "FortiADC authenticated" || warn "FortiADC auth failed — cannot disable VS"
    fi

    echo "$VS_NAMES" | while read -r VS_NAME; do
        [[ -z "$VS_NAME" ]] && continue
        if $DRY_RUN; then
            info "[DRY-RUN] Would disable FortiADC VS: $VS_NAME"
        else
            if [[ -n "${TOKEN:-}" ]]; then
                RESULT=$(curl -sk -X PUT \
                    -H "Authorization: Bearer $TOKEN" \
                    -H "Content-Type: application/json" \
                    -d '{"status": "disable"}' \
                    "${FADC_HOST}/api/v2.0/cmdb/load-balance/virtual-server/${VS_NAME}?vdom=${FADC_VDOM}" \
                    2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('mkey','error'))" \
                    2>/dev/null || echo "error")
                [[ "$RESULT" != "error" ]] \
                    && ok "Disabled FortiADC VS: $VS_NAME" \
                    || warn "Failed to disable VS: $VS_NAME (may already be disabled)"
            else
                warn "No auth token — skipping VS disable for: $VS_NAME"
                echo "MANUAL: Disable FortiADC VS '$VS_NAME' in VDOM '$FADC_VDOM'"
            fi
        fi
    done
else
    warn "Skipping FortiADC VS disable — config or host not available"
    info "MANUAL ACTION: Disable all FortiADC virtual servers for env '$ENV'"
fi

ok "Step 1 complete"

# ── Step 2: Restore DNS records to AVI VIPs ───────────────────────────────────
step "Step 2: Restore DNS A-records to AVI VIPs"

if [[ -f "$DNS_BACKUP" ]]; then
    # Extract Infoblox credentials if not already set
    INFOBLOX_HOST="${INFOBLOX_HOST:-}"
    INFOBLOX_USER="${INFOBLOX_USER:-}"
    INFOBLOX_PASS="${INFOBLOX_PASS:-}"
    INFOBLOX_VIEW="${INFOBLOX_VIEW:-default}"
    WAPI_VERSION="${WAPI_VERSION:-2.10}"

    if [[ -z "$INFOBLOX_HOST" ]]; then
        read -r -p "  Infoblox host: " INFOBLOX_HOST
    fi
    if [[ -z "$INFOBLOX_USER" ]]; then
        read -r -p "  Infoblox user: " INFOBLOX_USER
    fi
    if [[ -z "$INFOBLOX_PASS" ]]; then
        read -r -s -p "  Infoblox password: " INFOBLOX_PASS; echo
    fi

    WAPI_BASE="https://${INFOBLOX_HOST}/wapi/v${WAPI_VERSION}"

    # Read backup and restore
    python3 - <<'PYEOF'
import json, subprocess, os, sys

backup_file = f"state/{os.environ.get('ENV', '')}-dns-backup.json"
dry_run     = os.environ.get('DRY_RUN_PY', 'true') == 'true'
host        = os.environ.get('INFOBLOX_HOST', '')
user        = os.environ.get('INFOBLOX_USER', '')
password    = os.environ.get('INFOBLOX_PASS', '')
view        = os.environ.get('INFOBLOX_VIEW', 'default')
wapi        = f"https://{host}/wapi/v2.10"

try:
    backup = json.load(open(backup_file))
except FileNotFoundError:
    print(f"  WARN: Backup file not found: {backup_file}")
    sys.exit(0)

records = backup.get("dns_records", [])
print(f"  Found {len(records)} record(s) to restore")

for r in records:
    name    = r.get("name", "")
    avi_vip = r.get("ipv4addr", "")
    ref     = r.get("_ref", "")
    old_ttl = r.get("ttl", 300)

    if dry_run:
        print(f"  [DRY-RUN] Would restore: {name} → {avi_vip} (TTL:{old_ttl})")
        continue

    if not ref:
        print(f"  WARN: No ref for {name} — skipping")
        continue

    result = subprocess.run(
        ["curl", "-sk", "-X", "PUT",
         "-u", f"{user}:{password}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"ipv4addr": avi_vip, "ttl": old_ttl, "use_ttl": True}),
         f"{wapi}/{ref}"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip().startswith('"'):
        print(f"  OK: Restored {name} → {avi_vip}")
    else:
        print(f"  FAIL: Could not restore {name}: {result.stderr[:80]}")
PYEOF

    export ENV DRY_RUN_PY=$( $DRY_RUN && echo "true" || echo "false" )
    export INFOBLOX_HOST INFOBLOX_USER INFOBLOX_PASS INFOBLOX_VIEW
    ok "Step 2 complete"
else
    warn "No DNS backup found — cannot auto-restore DNS."
    info "MANUAL ACTION: Update Infoblox A-records to point back to AVI VIPs."
    info "AVI VIPs are in: discovery/${ENV}.json"
fi

# ── Step 3: Verify AVI health ──────────────────────────────────────────────────
step "Step 3: Verify AVI controllers are healthy"

AVI_HOST=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print(cfg.get('avi', {}).get('controller', ''))
" 2>/dev/null || echo "")

if [[ -n "$AVI_HOST" ]]; then
    AVI_HTTP=$(curl -sk -o /dev/null -w "%{http_code}" \
        "${AVI_HOST}/api/initial-data" 2>/dev/null || echo "000")
    case "$AVI_HTTP" in
        200|302) ok "AVI controller responding: $AVI_HOST" ;;
        *)       warn "AVI controller response: HTTP $AVI_HTTP — verify manually" ;;
    esac
else
    warn "AVI host not found in config.yaml — verify AVI health manually"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  ROLLBACK ${MODE} COMPLETE"
echo "  Environment : $ENV"
echo "  Finished    : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Log         : $LOG_FILE"
if $DRY_RUN; then
echo "  Result      : DRY-RUN — no changes made"
echo "  Next step   : Run with --execute for real rollback"
else
echo "  Result      : FortiADC VSes disabled. DNS restored to AVI."
echo "  Action      : Confirm AVI is serving traffic before closing window"
fi
echo "══════════════════════════════════════════════════════════════"
echo ""
echo "POST-ROLLBACK CHECKLIST:"
echo "  1. Verify applications accessible via AVI"
echo "  2. Check AVI VS health in AVI UI"
echo "  3. Notify app teams that rollback is complete"
echo "  4. Raise incident ticket with rollback cause"
echo "  5. Investigate FortiADC issue before re-attempting migration"
echo ""
