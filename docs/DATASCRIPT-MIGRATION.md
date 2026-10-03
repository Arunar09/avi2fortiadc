# DataScript Migration

## Why DataScripts are different

A DataScript is executable application/control-plane logic, not merely configuration data. Avi events, APIs, variables and helper functions may have no one-to-one FortiADC equivalent.

The repository therefore treats DataScripts as a manual migration class rather than silently generating target code.

## Current behavior

- DataScripts are discovered and retained in the discovery model.
- Analysis can flag DataScript concentration/complexity.
- Unsupported transformers can emit MANUAL events.
- The optional LLM gateway/advisory path can assist with interpretation.
- Final implementation and validation remain human responsibilities.

## Workflow

1. Inventory script name and trigger events.
2. Record required business behavior.
3. Classify behavior: redirect, headers, authentication, rate limiting, logging, security or application-specific logic.
4. Identify a target-native feature.
5. If scripting is still required, use the exact target-release documentation.
6. Generate a sanitized LLM pack only if approved.
7. Human-review the proposal.
8. Test in a non-production target.
9. Validate representative application behavior during parallel run.
10. Record evidence.

## LLM boundary

Safe pattern:

source DataScript → sanitized advisory context → human review → target implementation → target test

Unsafe pattern:

source DataScript → LLM → automatic production deployment

LLM output is advisory and does not change the deterministic migration contract.
