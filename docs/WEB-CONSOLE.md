# Web Operator Console

## Purpose

The Flask console provides a browser-based operator surface over the migration services.

Application factory: ui/app.py
Launcher: run_ui.py

## Security controls

The current implementation includes explicit production Flask secret requirements, session-cookie controls, CSRF protection, login/session management, role-based route protection for sensitive operations, explicit bootstrap-admin password handling, and sanitization before audit/event persistence.

The exact authorization decorators in ui/routes.py are authoritative.

## Sensitive operations

Protected operations include import commit, AI indexing/experience save, VDOM/network strategy operations, existing FortiADC configuration access and validation pre-flight.

## Operator responsibilities

Use a dedicated account, do not share credentials, verify tenant/VDOM before mutation, review the decision manifest, preserve change/approval evidence and stop on unexpected scope expansion.

## Qualification terminology

A UI label such as Qualified must not be interpreted as equivalent to production qualification. Production qualification is defined by the qualification matrix and evidence ledger.

## Air-gap note

The core UI is local. Optional LLM gateway behavior is separately controlled and may introduce an external data path if configured.
