# CLI and Interface Reference

## Installer

bash install.sh

Modes:

- --check — verify installed dependencies.
- --bundle — download dependencies into vendor/ on an internet-connected packaging machine.

The migration run can be air-gapped when the bundle is complete.

## Guided wizard

python3 wizard.py

The wizard guides environment selection and migration phases with confirmations and resumable state.

## CLI

python3 migrate.py --help

The CLI is the direct automation interface. The checked-in migrate.py parser is authoritative for exact options.

Common command families documented by the repository include:

- discover
- import-avi-json
- analyse
- transform
- deploy
- ops-check
- rag
- llm-pack
- audit

## Web operator console

python3 run_ui.py

Default bind is 127.0.0.1:5000. Host/port can be overridden by the launcher options or its environment variables.

## Deployment scripts

DNS:
bash scripts/dns-cutover.sh --env <env> --dry-run
bash scripts/dns-cutover.sh --env <env> --execute

Rollback:
bash scripts/rollback.sh --env <env> --dry-run
bash scripts/rollback.sh --env <env> --execute

Parallel run:
bash scripts/parallel-run-check.sh <env>

## Command safety

Before production use, check source/target versions, credentials/RBAC, tenant/VDOM, target API syntax, DNS view/zone, approval and dry-run evidence.

## Artifacts

- discovery/ — source discovery snapshots.
- reports/ — analysis and sanitized reports.
- fortiadc/ — generated target configuration.
- state/ — ledgers, manifests and state.
- logs/ — structured event/audit logs.
- qualification/ — qualification evidence and matrix.
