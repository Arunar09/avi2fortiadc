#!/usr/bin/env bash
# install.sh — Offline installer for AVI → FortiADC Migration Tool
# ─────────────────────────────────────────────────────────────────
# Installs all Python dependencies from the bundled vendor/ directory.
# No internet access required. Works in air-gapped environments.
#
# USAGE:
#   bash install.sh           — install + verify
#   bash install.sh --check   — verify only (no install)
#   bash install.sh --bundle  — download deps for bundling (requires internet)
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; }

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$TOOL_DIR/vendor"
REQUIREMENTS="$TOOL_DIR/requirements.txt"
PYTHON="${PYTHON:-python3}"

# ── Mode ──────────────────────────────────────────────────────────
MODE="install"
[[ "${1:-}" == "--check"  ]] && MODE="check"
[[ "${1:-}" == "--bundle" ]] && MODE="bundle"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  AVI → FortiADC Migration Tool — Installer"
echo "  Mode   : $MODE"
echo "  Python : $($PYTHON --version 2>&1)"
echo "  Dir    : $TOOL_DIR"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Check Python version ──────────────────────────────────────────
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 8 ) ]]; then
    fail "Python 3.8+ required. Found: $PY_VER"
    exit 1
fi
ok "Python $PY_VER (>= 3.8)"

# ── Bundle mode (download deps — requires internet) ───────────────
if [[ "$MODE" == "bundle" ]]; then
    info "Downloading dependencies to vendor/ for air-gapped bundling..."
    mkdir -p "$VENDOR_DIR"
    $PYTHON -m pip download \
        --dest "$VENDOR_DIR" \
        --requirement "$REQUIREMENTS" \
        --no-deps
    ok "Dependencies downloaded to: $VENDOR_DIR"
    info "You can now zip this directory and ship to air-gapped environment."
    info "Run 'bash install.sh' in the target environment to install offline."
    exit 0
fi

# ── Check mode ────────────────────────────────────────────────────
if [[ "$MODE" == "check" ]]; then
    info "Verifying installed packages..."
    FAIL=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip comments and blanks
        [[ "$line" =~ ^#  ]] && continue
        [[ -z "$line"     ]] && continue
        # Extract package name (before >= or < or ==)
        pkg=$(echo "$line" | sed 's/[><=!].*//' | tr '[:upper:]' '[:lower:]')
        if $PYTHON -c "import importlib; importlib.import_module('${pkg//-/_}')" 2>/dev/null; then
            ok "$pkg"
        elif $PYTHON -c "import importlib; importlib.import_module('$pkg')" 2>/dev/null; then
            ok "$pkg"
        else
            fail "$pkg — NOT installed"
            FAIL=$((FAIL + 1))
        fi
    done < "$REQUIREMENTS"

    if [[ "$FAIL" -gt 0 ]]; then
        fail "$FAIL package(s) missing. Run: bash install.sh"
        exit 1
    fi
    ok "All dependencies verified"
    exit 0
fi

# ── Install mode ──────────────────────────────────────────────────
if [[ -d "$VENDOR_DIR" ]] && [[ -n "$(ls -A "$VENDOR_DIR" 2>/dev/null)" ]]; then
    info "Installing from bundled vendor/ directory (offline mode)..."
    $PYTHON -m pip install \
        --no-index \
        --find-links "$VENDOR_DIR" \
        --requirement "$REQUIREMENTS" \
        --quiet
    ok "Dependencies installed from vendor/"
else
    warn "vendor/ directory not found or empty."
    info "Attempting online install (requires internet)..."
    warn "For air-gapped environments, run 'bash install.sh --bundle' on an"
    warn "internet-connected machine first, then ship the zip."
    $PYTHON -m pip install \
        --requirement "$REQUIREMENTS" \
        --quiet
    ok "Dependencies installed from PyPI"
fi

# ── Verify install ────────────────────────────────────────────────
info "Verifying installation..."
$PYTHON -c "import requests; import yaml; import urllib3; import certifi"
ok "Core dependencies verified: requests, pyyaml, urllib3, certifi"

# ── Create required directories ───────────────────────────────────
info "Creating required directories..."
mkdir -p "$TOOL_DIR"/{discovery,fortiadc,reports,logs,state,scripts}
ok "Directories created"

# ── Config check ──────────────────────────────────────────────────
if [[ -f "$TOOL_DIR/config.yaml" ]]; then
    ok "config.yaml found"
else
    warn "config.yaml not found."
    info "Copy config.example.yaml to config.yaml and fill in your details:"
    info "  cp config.example.yaml config.yaml"
    info "  nano config.yaml"
fi

# ── Make scripts executable ───────────────────────────────────────
chmod +x "$TOOL_DIR/scripts/"*.sh 2>/dev/null || true
chmod +x "$TOOL_DIR/wizard.py"    2>/dev/null || true
chmod +x "$TOOL_DIR/migrate.py"   2>/dev/null || true
ok "Scripts marked executable"

# ── Test run ──────────────────────────────────────────────────────
info "Smoke-testing migrate.py..."
if $PYTHON "$TOOL_DIR/migrate.py" --help > /dev/null 2>&1; then
    ok "migrate.py responds to --help"
else
    warn "migrate.py --help failed — check Python path"
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  Installation complete."
echo ""
echo "  Start the migration wizard:"
echo "    python3 wizard.py"
echo ""
echo "  Or use the CLI directly:"
echo "    python3 migrate.py --help"
echo ""
echo "  Run tests:"
echo "    python3 -m pytest tests/ -v"
echo "══════════════════════════════════════════════════════════════"
echo ""
