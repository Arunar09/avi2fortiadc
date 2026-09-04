#!/usr/bin/env bash
# scripts/parallel-run-check.sh
# Validates FortiADC while Avi is still live.
# Run daily during the 7-day parallel run window.
set -uo pipefail
ENV="${1:?Usage: $0 <env-name>}"
FADC_HOST="${FADC_HOST:?Set FADC_HOST env var}"
TOKEN="${FADC_TOKEN:?Set FADC_TOKEN env var}"
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
PASS=0; FAIL=0
ok()   { echo -e "${GREEN}[OK]${NC}   $*"; ((PASS++)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; ((FAIL++)); }

echo "Parallel run check: ${ENV} — $(date -u)"
echo "FortiADC: ${FADC_HOST}"

# Get all VS from FortiADC
VS_LIST=$(curl -sf -H "Authorization: Bearer ${TOKEN}" \
  "${FADC_HOST}/api/load_balance/virtual_server" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); \
  [print(r['mkey']) for r in d.get('results',[])]" 2>/dev/null)

for vs in ${VS_LIST}; do
  STATUS=$(curl -sf -H "Authorization: Bearer ${TOKEN}" \
    "${FADC_HOST}/api/load_balance/virtual_server/${vs}" | \
    python3 -c "import json,sys; d=json.load(sys.stdin); \
    print(d.get('results',{}).get('status','unknown'))" 2>/dev/null)
  [[ "${STATUS}" == "enable" ]] \
    && ok "VS ${vs}: enabled" \
    || fail "VS ${vs}: status=${STATUS}"
done

echo ""
echo "Results: ${PASS} ok, ${FAIL} failed — $(date -u)"
[[ "${FAIL}" -gt 0 ]] && exit 1 || exit 0
