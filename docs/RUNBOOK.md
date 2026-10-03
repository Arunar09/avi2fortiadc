# AVI → FortiADC Migration Tool
## Operations Runbook

**Document type:** Operations Runbook  
**Version:** 0.2  
**Audience:** NOC, on-call engineers, change managers  
**Use this document:** During and after migration maintenance windows

> **Qualification status:** This runbook is an operational template. FortiADC API paths, CLI syntax, UI labels, Infoblox WAPI paths, timing thresholds, and platform-specific behavior shown below are examples/planning values unless separately validated against the exact deployed releases. Do not treat this document as evidence of target-platform compatibility.

---

## 1. Quick Reference — Contacts and Access

Fill in before the maintenance window. Have this open during cutover.

| Role | Name | Contact | Availability |
|---|---|---|---|
| Migration lead | | | |
| Network engineer | | | |
| NOC on-call | | | |
| Application owner (Prod-A) | | | |
| Application owner (Prod-B) | | | |
| Fortinet TAC | | +1-XXX-XXX-XXXX | 24/7 |
| AVI/Broadcom support | | | |

| System | URL / IP | Credentials location |
|---|---|---|
| AVI controller | | Vault: `infra/avi/migration-readonly` |
| FortiADC | | Vault: `infra/fortiadc/migration-svc` |
| Infoblox | | Vault: `infra/infoblox/wapi` |
| Jump host | | SSH key |

---

## 2. Pre-Window Checklist (Complete 2 hours before window opens)

### 2.1 Tool readiness

```bash
# Verify tool is installed
bash install.sh --check

# Verify config is complete
python3 migrate.py --help   # should print usage

# Verify AVI connectivity
python3 migrate.py discover --env dev-b   # run once, confirm no errors
```

- [ ] Tool responds to `--help`
- [ ] AVI connectivity confirmed from jump host
- [ ] FortiADC API responding (`curl -k https://fortiadc.internal/api/user/login`)
- [ ] Infoblox WAPI responding (`curl -k https://infoblox.internal/wapi/v2.10/`)
- [ ] `state/<env>-ledger.json` shows deploy phase complete
- [ ] Rollback script generated: `scripts/rollback-<env>.sh` exists

### 2.2 Environment state

```bash
# Confirm FortiADC VS are deployed
cat state/<env>-ledger.json | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('Deploy status:', d['phases']['deploy']['status'])
print('Parallel run:', d['phases']['parallel-run']['status'])
"
```

- [ ] Deploy phase: `done`
- [ ] Parallel run: `done` (7+ clean days)
- [ ] All FortiADC VS health showing UP (verify in FortiADC UI)
- [ ] AVI VS health all showing UP (verify in AVI UI)

### 2.3 DNS TTL pre-lowering (should have been done 24h ago)

```bash
# Verify current TTLs on VIP records
cat state/<env>-dns-backup.json | python3 -c "
import json,sys; d=json.load(sys.stdin)
for r in d.get('dns_records', []):
    print(f'{r[\"name\"]:<50} TTL: {r.get(\"ttl\", \"unknown\")}')
"
```

- [ ] All VIP A-record TTLs are 300s or lower
- [ ] If not: lower TTLs now and wait 24h before proceeding with cutover

### 2.4 Rollback dry-run

```bash
bash scripts/rollback.sh --env <env> --dry-run
```

- [ ] Dry-run completed with no errors
- [ ] Rollback would disable correct VS names
- [ ] DNS backup file present: `state/<env>-dns-backup.json`

---

## 3. Maintenance Window Execution

### 3.1 Timeline (typical 2-hour window)

| Time | Action | Who |
|---|---|---|
| T+0:00 | Open window. Start wizard Step 7 (DNS cutover) | Migration lead |
| T+0:05 | DNS cutover dry-run review | Migration lead + Network engineer |
| T+0:10 | DNS cutover execute | Migration lead |
| T+0:15 | Monitor traffic for 15 minutes | All |
| T+0:30 | Application health check with app owners | Migration lead |
| T+0:45 | Confirm traffic on FortiADC, minimal on AVI | NOC |
| T+1:00 | Decision point: close window or extend | Migration lead |
| T+1:30 | Document outcomes, close window | Change manager |

### 3.2 Running the DNS cutover

The wizard handles this in Step 7. If running manually:

```bash
# Dry-run (always first)
bash scripts/dns-cutover.sh --env <env> --dry-run

# Review output — confirm:
# - Correct VIP addresses shown (AVI → FortiADC)
# - Correct DNS record names
# - Correct Infoblox view

# Execute (only after dry-run reviewed and approved)
bash scripts/dns-cutover.sh --env <env> --execute
```

### 3.3 During cutover — what to watch

Open the following in parallel during the cutover:

**FortiADC UI (Monitor → Virtual Server):**
All VS should remain UP. If any VS shows DOWN during or after cutover, this is a routing or connectivity issue — roll back immediately.

**AVI UI (Applications → Virtual Services):**
Traffic metrics should start decreasing as DNS propagates. AVI should not be disabled — keep it running as a fallback.

**Application monitoring / Synthetic tests:**
Application teams should run their smoke tests against the application URLs (not FortiADC VIPs directly) to confirm end-to-end traffic works.

**Logs (migration tool):**
```bash
# Monitor DNS cutover log in real time
tail -f logs/<env>-dns-cutover.log
```

---

## 4. Rollback Procedures

### 4.1 Decision criteria — when to roll back

Roll back **immediately** (do not wait for investigation) if:
- Any production VS shows DOWN for more than 2 minutes after cutover
- Application error rate rises above pre-migration baseline + 5%
- Application team reports active user-facing errors
- Unable to reach FortiADC management interface during cutover

Roll back **after brief investigation** (5-minute window) if:
- One non-critical VS is down but critical VS are healthy
- Application team reports minor anomalies not affecting all users
- Traffic is flowing but latency has increased significantly

Do not roll back if:
- All VS are UP and traffic is flowing normally
- Intermittent connectivity from a single monitoring location
- DNS propagation is still in progress (allow 5 minutes after cutover)

### 4.2 Emergency rollback — step by step

**Step 1: Announce rollback decision**
Inform all parties (NOC, app owners, change manager) that rollback is being executed.

**Step 2: Execute rollback script**
```bash
bash scripts/rollback.sh --env <env> --execute
```

Type `ROLLBACK` when prompted to confirm.

The script:
1. Disables all FortiADC virtual servers (stops FortiADC from competing for traffic)
2. Restores Infoblox A-records to original AVI VIPs
3. Verifies AVI controller is responding

**Step 3: Verify AVI is healthy**
```bash
# AVI controller API
curl -k https://<avi-controller>/api/initial-data | python3 -m json.tool | head -10

# Check AVI VS health in AVI UI
# All VS should show UP — they were never disabled
```

**Step 4: Verify application traffic**
Confirm with application owners that traffic is flowing through AVI again.

**Step 5: Close maintenance window**
Update change request with rollback status and cause.

**Step 6: Root cause analysis**
Do not retry the migration until root cause is identified and resolved. File an incident ticket.

### 4.3 Rollback timing expectations

| Action | Typical time |
|---|---|
| FortiADC VS disabled | Planning estimate; validate in target environment |
| Infoblox records restored | Planning estimate; validate in target environment |
| DNS propagation (60s TTL) | Depends on resolver/client behavior; validate |
| Application traffic restored | Planning estimate; validate in target environment |

If DNS records were not lowered to 60s before cutover, propagation may take longer. In that case, application teams may need to flush local DNS cache.

### 4.4 Manual rollback (if rollback script fails)

If the rollback script fails (e.g., Infoblox unreachable), roll back manually:

**Step 1 — Disable FortiADC VS manually:**
FortiADC UI → Load Balance → Virtual Server → Select all → Disable

Or via FortiADC CLI:
```
config load-balance virtual-server
  edit vs-name
    set status disable
  end
```

**Step 2 — Restore DNS manually in Infoblox:**
The original VIP-to-hostname mapping is in `state/<env>-dns-backup.json`. Use Infoblox UI to update each A-record manually.

**Step 3 — Verify:**
Use `nslookup` from multiple locations to confirm records are resolving to AVI VIPs.

---

## 5. Post-Migration Verification (24 hours)

### 5.1 Immediate checks (0–30 minutes post-cutover)

```bash
# Check all FortiADC VS health
curl -sk -H "Authorization: Bearer $FADC_TOKEN" \
  "https://fortiadc.internal/api/v2.0/monitor/load-balance/virtual-server/stats" \
  | python3 -m json.tool

# Check parallel run script
bash scripts/parallel-run-check.sh <env>
```

- [ ] All VS show `enable` status
- [ ] All pool members health check passing
- [ ] No CRITICAL events in logs: `cat logs/<env>-deploy.jsonl | grep CRITICAL`
- [ ] Application smoke tests passing (confirm with app team)

### 5.2 4-hour check

- [ ] FortiADC access logs show real traffic (not test traffic only)
- [ ] AVI traffic graphs confirm near-zero connections (DNS propagated)
- [ ] No error rate anomalies in application monitoring
- [ ] SSL certificates valid and not showing warnings

### 5.3 24-hour check

```bash
# Run the standard parallel-run check one more time
bash scripts/parallel-run-check.sh <env>
```

- [ ] All VS healthy for 24 hours
- [ ] No rollback-worthy events occurred overnight
- [ ] Application owners confirm no user-reported issues

### 5.4 AVI decommission hold

**Do not decommission or power off AVI SE instances for the environment until:**
- 7 days have passed since DNS cutover with no issues
- All MANUAL items have been implemented and tested in FortiADC
- A separate change request for AVI SE decommission has been raised and approved

AVI controller and SE VMs consume compute resources but provide critical rollback capability.

---

## 6. Day-2 Operations

### 6.1 Adding a new virtual server to FortiADC after migration

When a new application requires load balancing after the migration is complete, configure directly in FortiADC — do not add to AVI. The migration tool is not needed for ongoing FortiADC operations.

### 6.2 Updating pool members

FortiADC REST API or UI can be used directly. If the migration tool's config JSON needs to stay in sync, re-run transform for that VS and deploy the updated config:

```bash
# Re-run for a specific VS (filter in fortiadc/<env>-config.json manually first)
python3 migrate.py deploy \
  --fortiadc-config fortiadc/<env>-config.json \
  --env <env> --execute
```

The deployer performs existence/update handling for already-existing objects. Complete idempotency, including preservation of unrelated target configuration, must be validated against the exact FortiADC release and target configuration before production use.

### 6.3 Monitoring FortiADC health ongoing

```bash
# Run parallel-run-check.sh on a schedule (cron or monitoring tool)
bash scripts/parallel-run-check.sh <env>

# Or integrate with your monitoring system:
# FortiADC SNMP OID for VS health: 1.3.6.1.4.1.12356.112.3.1.x
```

### 6.4 Certificate renewal on FortiADC

When certificates expire, renew via FortiADC directly:
- System → Certificate → Local Certificate → Import
- Update the virtual server to reference the new certificate
- Verify VS health after update

The migration tool's certificate audit (Pattern P04) will have flagged any near-expiry certs during the migration. Reference `state/<env>-unsupported.json` for any certs that were flagged.

---

## 7. Known Operational Constraints

> **Qualification note:** The differences in the table below are migration hypotheses/operational checks, not repository qualification results. Confirm each item against the exact Avi, FortiADC, OpenStack, and Contrail releases in the target environment before relying on it.

These are FortiADC behaviours that differ from AVI and may affect day-2 operations.

| Behaviour | AVI | FortiADC | Impact |
|---|---|---|---|
| Source IP persistence | Supports GSLB-aware | Source address only | May affect multi-site users |
| LB algorithm: SOURCE_IP | Supported | Falls back to Round Robin | Verify session consistency |
| Health monitor: timeout | Can exceed interval | Must be < interval | Stricter timing requirements |
| Pool member weight 0 | Disabled | Minimum weight is 1 | Use enabled=disable instead |
| HA with MAC spoofing protection | Handled by SE model | Requires port security disabled or allowed-address-pairs | Verify with cloud team |
| VRRP/HA visibility | OpenStack-aware | Requires manual port config | Pre-configure before enabling HA |

---

## 8. Log Files Reference

All logs are in JSONL format (one JSON object per line) for easy parsing.

```bash
# Show all CRITICAL events across all environments
cat logs/*-*.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        if e.get('level') == 'CRITICAL':
            print(f\"{e['timestamp']} [{e['phase']}] {e['message']}\")
    except: pass
"

# Show MANUAL items from a specific environment
cat logs/prod-a-transform.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        if e.get('level') == 'MANUAL':
            print(f\"{e['object_name']}: {e['message']}\")
    except: pass
"

# Count events by level
cat logs/<env>-discover.jsonl | python3 -c "
import json, sys, collections
counts = collections.Counter()
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        counts[e.get('level', 'UNKNOWN')] += 1
    except: pass
for k, v in sorted(counts.items()):
    print(f'  {k}: {v}')
"
```
