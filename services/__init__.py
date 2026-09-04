# services/__init__.py
# Service layer for the Migration Operator Console.
# All functions return plain dicts or lists — no Flask objects, no HTTP.
# Services read from local filesystem (state/, reports/, discovery/, logs/).
# Services never write to disk, never execute deployments.
