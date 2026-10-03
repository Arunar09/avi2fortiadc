# AVI → FortiADC Migration Tool
## Troubleshooting Guide

**Document type:** Troubleshooting Reference  
**Version:** 0.2  
**Audience:** Migration engineers, NOC

---

## 1. Tool Installation Issues

### `bash install.sh` fails with Python version error

**Cause:** System Python is too old (requires 3.8+).
**Fix:**
```bash
python3.10 wizard.py    # or python3.11
python3.10 -m pytest tests/ -v
```

### vendor/ empty — offline install fails

On an internet-connected machine first:
```bash
bash install.sh --bundle
# Re-zip and ship to air-gapped environment
```

### PyYAML not installed
```bash
pip install --no-index --find-links vendor/ pyyaml
```

---

## 2. Configuration Issues

### Config file not found
```bash
cp config.example.yaml config.yaml
nano config.yaml   # fill in your values
```

### Environment not found in config
Environment names are case-sensitive and must exactly match `environments[].name` in `config.yaml`.

### AVI API version mismatch (KeyError: 'results')
Check the exact AVI version:
```bash
curl -k https://<avi-ip>/api/cluster/version | python3 -m json.tool
# Update api_version in config.yaml to match
```

---

## 3. AVI Connectivity Issues

### Connection refused / unreachable
- Confirm URL includes `https://` in config.yaml
- Test: `curl -k https://<avi-ip>/api/initial-data`
- Confirm AVI API access enabled: AVI UI → Administration → Settings → Access Settings

### Authentication failed
- Confirm credentials correct — log into AVI UI with the service account to verify
- Confirm account has Viewer role at minimum
- AVI passwords expire — reset if needed

### SSL verify failed
Set `verify_ssl: false` in config.yaml for internal controllers with self-signed certificates.

### Discovery returns 0 Virtual Services
- Confirm tenant name in config.yaml matches exactly (case-sensitive)
- Confirm service account has access to that tenant
- Test: `curl -k -H "X-Avi-Tenant: <tenant>" https://<avi>/api/virtualservice?page_size=1`

---

## 4. Transform Issues

### Fewer virtual servers than expected
DataScript VS are intentionally skipped (BLOCKED). Check what was skipped:
```bash
cat state/<env>-unsupported.json | python3 -c "
import json, sys
for i in json.load(sys.stdin):
    if i['severity'] == 'BLOCKED':
        print(f\"BLOCKED: {i['object_name']}: {i['reason']}\")
"
```

### Wrong pool member IPs in generated config
Re-run discovery to get a fresh snapshot, then re-run transform.

---

## 5. Dry-Run Issues

### FortiADC API unreachable
- Test: `curl -k https://<fortiadc-ip>/api/user/login`
- Enable REST API in FortiADC: System → Admin → Admin Settings → REST API Access

### VDOM does not exist
Create the VDOM in FortiADC: System → VDOM → Create New.  
Or update `fortiadc_vdom` in config.yaml to an existing VDOM.

### Name conflict — VS already exists
Delete the existing VS in FortiADC (if leftover), or rename it in `fortiadc/<env>-config.json`.

---

## 6. Deployment Issues

### VS shows DOWN immediately after deploy
The timing below is a diagnostic starting point, not a universal target guarantee. Validate the target release's health-check timing. If the VS remains DOWN,

1. Check pool member health: FortiADC UI → Server Load Balance → Real Server Pool
2. Verify pool members are reachable from FortiADC (may differ from AVI network path)
3. Check FortiADC interface routing to pool member subnets

### SSL certificate import fails
- Verify cert/key pair: `openssl verify -CAfile ca.pem cert.pem`
- HSM-backed certs cannot be exported from AVI — new cert required from CA

### Partial deployment — some objects failed
Do not assume a blanket idempotency guarantee. Review the deployment/audit result and the target state first, then re-run only after confirming the generated configuration and target scope. The deployer has existence/update handling, and the repository security suite verifies create-then-update behavior for a representative object. Complete repeated-run behavior must be qualified on the exact target release.

---

## 7. DNS Cutover Issues

### Infoblox authentication failed
Verify `INFOBLOX_USER` and `INFOBLOX_PASS` are correct and the account has WAPI write access.

### No DNS records found for AVI VIPs
Try different DNS views:
```bash
export INFOBLOX_VIEW="internal"   # or "default" or your view name
```
If VIPs are in Contrail DNS (not Infoblox), manual DNS update is required — consult your DNS team.

### Traffic still going to AVI after cutover
DNS propagation depends on resolver/client behavior and the actual TTL. Confirm the authoritative record and effective TTL first. Application teams can flush local caches when appropriate:
```bash
sudo systemd-resolve --flush-caches   # Linux
ipconfig /flushdns                     # Windows
```

---

## 8. Rollback Issues

### Rollback script can't connect to FortiADC
Manual alternative: FortiADC UI → Server Load Balance → Virtual Server → Select All → Disable.

### DNS backup file missing
Restore from `discovery/<env>.json` manually:
```bash
cat discovery/<env>.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for vs in d.get('virtual_services', []):
    for vip in vs.get('_vips', []):
        print(f'{vs[\"name\"]}: {vip}')
" 
# Use these AVI VIP addresses to manually restore Infoblox A-records
```

---

## 9. Parallel Run Issues

### parallel-run-check.sh authentication error
```bash
export FADC_HOST="https://fortiadc.internal"
FADC_TOKEN=$(curl -sk -X POST -H "Content-Type: application/json" \
  -d '{"username":"migration-svc","password":"<pass>"}' \
  $FADC_HOST/api/user/login | \
  python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))")
export FADC_TOKEN
bash scripts/parallel-run-check.sh <env>
```

### One VS consistently DOWN in parallel run
Network path issue — check:
```bash
# From FortiADC CLI
execute ping <pool-member-ip>
execute traceroute <pool-member-ip>
```
Compare with AVI network path — firewall rules may differ.

---

## 10. State / Wizard Issues

### Wizard marks step done but output was deleted
Edit `state/wizard_state.json` and remove the step from the completed list.

### Need to reset a phase in the ledger
```python
from core.state_ledger import get_ledger
l = get_ledger('<env>')
l._data['phases']['deploy']['status'] = 'pending'
l._save()
```

---

## 11. Getting Help

**Step 1:** Check error in logs:
```bash
cat logs/<env>-<phase>.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        if e.get('level') in ('ERROR', 'CRITICAL'):
            print(f\"{e['timestamp']} {e['message']}\")
    except: pass
"
```

**Step 2:** Generate sanitized help pack for LLM assistance:
```bash
python3 migrate.py llm-pack --type next_steps --input discovery/<env>.json
```

**Step 3:** Escalate with: tool version, environment name, failed step, full error message, `state/<env>-ledger.json`.
