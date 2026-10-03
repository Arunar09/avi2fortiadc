# Evidence Handling

This directory is for sanitized qualification evidence only.

## Never commit

- passwords
- API keys/tokens
- session cookies
- Authorization headers
- private keys
- certificate private material
- application credentials
- unrestricted production configuration
- unrelated tenant/customer data

## Evidence naming

Use:

`<test-id>_<system>_<YYYYMMDD>_<short-description>.txt`

Example:

`Q1-001_avi_20260921_controller-version.txt`

## Before committing evidence

1. Remove credentials and secret-bearing headers.
2. Remove private key material.
3. Minimize unrelated tenant/customer information.
4. Preserve enough identifiers to correlate dependencies.
5. Record the source and target versions.
6. Record the exact command/API operation used.
7. Record timestamp and exit/HTTP status.
8. Record whether the collection was read-only.

## Evidence integrity

For formal qualification, calculate a SHA-256 hash for each evidence artifact and record the hash in the qualification ledger. Do not treat a filename or timestamp alone as proof of integrity.

## Production boundary

Evidence collection does not authorize configuration changes. Any change operation must be separately approved and explicitly identified as a mutation test.
