# Contributing to avi2fortiadc

Thanks for your interest in improving avi2fortiadc.

## Ground rules

- **Never commit real configuration data.** Avi exports, FortiADC configs, IPs,
  hostnames, certificates, tenant names, or credentials from any real environment
  must not appear in issues, PRs, fixtures, or logs. Use synthetic data
  (RFC 5737 IPs such as `192.0.2.0/24`, `*.example.com` hostnames).
- The core migration path is deterministic. LLM features are advisory only and
  must stay optional (`llm_gateway.enabled: false` must always work).
- Keep changes small and focused; one concern per PR.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest
```

All tests must pass locally and in the **Offline Qualification** CI workflow
before a PR can be merged.

## Reporting bugs

Open an issue using the bug report template. Include the tool version/commit,
the command or UI page used, and a **sanitized** minimal reproduction.

## Security issues

Do not open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
