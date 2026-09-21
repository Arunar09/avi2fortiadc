from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = [
    "docs/INDEX.md",
    "docs/00-PRODUCT-OVERVIEW.md",
    "docs/USER-GUIDE.md",
    "docs/OPERATING-MODEL.md",
    "docs/ARCHITECTURE.md",
    "docs/OBJECT-MAPPING-MATRIX.md",
    "docs/UNSUPPORTED-FEATURES.md",
    "docs/DATASCRIPT-MIGRATION.md",
    "docs/GSLB-MIGRATION.md",
    "docs/ENVIRONMENT-DEPENDENCIES.md",
    "docs/SECURITY.md",
    "docs/CLI-REFERENCE.md",
    "docs/WEB-CONSOLE.md",
    "docs/RUNBOOK.md",
    "docs/TROUBLESHOOTING.md",
    "docs/QUALIFICATION.md",
    "docs/TECHNICAL-ARCHITECTURE.md",
    "qualification/README.md",
    "qualification/QUALIFICATION-MATRIX.md",
]

REQUIRED_ENTRYPOINTS = [
    "install.sh",
    "migrate.py",
    "wizard.py",
    "run_ui.py",
    "scripts/dns-cutover.sh",
    "scripts/rollback.sh",
    "scripts/parallel-run-check.sh",
]

FORBIDDEN_UNQUALIFIED_CLAIMS = [
    "Rollback is automated and safe.",
    "Deployment is idempotent",
    "The deployer is idempotent",
    "AVI was never modified and is always available.",
]


def test_canonical_documentation_set_exists():
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).is_file()]
    assert not missing, f"Missing canonical documentation: {missing}"


def test_documented_entrypoints_exist():
    missing = [path for path in REQUIRED_ENTRYPOINTS if not (ROOT / path).is_file()]
    assert not missing, f"Documentation references missing entrypoints: {missing}"


def test_no_known_unqualified_operational_overclaims():
    docs = [
        ROOT / "README.md",
        ROOT / "USER-GUIDE.md",
        ROOT / "ARCHITECTURE.md",
        ROOT / "SECURITY.md",
    ]
    docs.extend(ROOT.joinpath("docs").glob("*.md"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in docs if path.exists())

    offenders = [claim for claim in FORBIDDEN_UNQUALIFIED_CLAIMS if claim in text]
    assert not offenders, f"Unqualified operational claims remain: {offenders}"


def test_root_docs_are_compatibility_pointers():
    assert "docs/USER-GUIDE.md" in (ROOT / "USER-GUIDE.md").read_text(encoding="utf-8")
    assert "docs/ARCHITECTURE.md" in (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "docs/SECURITY.md" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_qualification_matrix_retains_non_pass_statuses_for_live_integration():
    matrix = (ROOT / "qualification/QUALIFICATION-MATRIX.md").read_text(encoding="utf-8")
    for marker in ("Q1-001", "Q2-001", "Q3-001", "Q4-002", "Q4-006", "Q5-003"):
        assert marker in matrix

    # Documentation changes must not silently turn live integration requirements
    # into repository-only PASS claims.
    assert "NOT-TESTED" in matrix
