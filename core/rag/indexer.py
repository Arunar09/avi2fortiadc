"""
core/rag/indexer.py
Ingestion pipeline — converts project sources into indexed Document chunks.

Sources (each modular, independently togglable):
  1. MarkdownSource   — docs/ directory markdown files
  2. LedgerSource     — state/*-ledger.json unsupported + resolved items
  3. DiscoverySource  — discovery/*.json pattern/object summaries
  4. MappingSource    — transformers/mappings.py mapping tables

Each source implements Source.load() -> list[Document].

Usage:
    from core.rag.indexer import index_all
    stats = index_all(dirs)           # rebuild entire index
    stats = index_all(dirs, sources=["docs", "ledger"])   # partial rebuild
"""
from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

from core.rag.document import Document


# ── Chunk helpers ──────────────────────────────────────────────────────────────

_MAX_CHUNK_CHARS = 800
_CHUNK_OVERLAP   = 100


def _make_id(source_path: str, chunk_index: int) -> str:
    raw = f"{source_path}::{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _split_text(text: str, max_chars: int = _MAX_CHUNK_CHARS,
                overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """
    Split text into overlapping chunks of max_chars.
    Tries to break on sentence boundaries first, then word boundaries.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        # Try to find a sentence boundary in the last 20% of the window
        if end < len(text):
            boundary_search_from = start + int(max_chars * 0.8)
            period_pos = text.rfind(".", boundary_search_from, end)
            newline_pos = text.rfind("\n", boundary_search_from, end)
            boundary = max(period_pos, newline_pos)
            if boundary > boundary_search_from:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = max(end - overlap, start + 1)
        if start >= len(text):
            break

    return chunks


# ── Source base class ──────────────────────────────────────────────────────────

class Source(ABC):
    """Base class for a RAG document source."""

    SOURCE_TYPE: str = ""

    @abstractmethod
    def load(self, dirs: dict) -> list[Document]:
        """Load and chunk all documents from this source."""
        ...

    def name(self) -> str:
        return self.SOURCE_TYPE


# ── Markdown source ────────────────────────────────────────────────────────────

class MarkdownSource(Source):
    """
    Indexes all .md files in the docs/ directory.
    Chunks by top-level heading sections (##).
    Each H2 section becomes one or more chunks.
    """
    SOURCE_TYPE = "docs"

    def load(self, dirs: dict) -> list[Document]:
        tool_root = Path(dirs.get("tool_root", "."))
        docs_dir = tool_root / "docs"
        if not docs_dir.exists():
            return []

        docs: list[Document] = []
        for md_path in sorted(docs_dir.glob("*.md")):
            docs.extend(self._load_file(md_path))

        return docs

    def _load_file(self, path: Path) -> list[Document]:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return []

        rel_path = str(path.name)
        chunks = self._chunk_by_heading(content, path.stem)
        docs = []
        for i, (title, chunk_text) in enumerate(chunks):
            for j, text_part in enumerate(_split_text(chunk_text)):
                doc = Document(
                    id=_make_id(rel_path, i * 100 + j),
                    source_type=self.SOURCE_TYPE,
                    source_path=rel_path,
                    title=title,
                    content=text_part,
                    metadata={
                        "filename": rel_path,
                        "tag": _doc_tag(rel_path),
                    },
                    chunk_index=i * 100 + j,
                )
                docs.append(doc)
        return docs

    @staticmethod
    def _chunk_by_heading(text: str, filename: str) -> list[tuple[str, str]]:
        """Split markdown into (heading_title, section_content) pairs."""
        lines = text.splitlines()
        sections: list[tuple[str, str]] = []
        current_title = filename.replace("-", " ").replace("_", " ").title()
        current_lines: list[str] = []

        for line in lines:
            if line.startswith("## "):
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = line.lstrip("# ").strip()
                current_lines = []
            else:
                # Skip pure markdown decorators but keep content
                if not re.match(r'^[-=]{3,}$', line.strip()):
                    current_lines.append(line)

        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))

        # Filter out near-empty sections
        return [(t, c) for t, c in sections if len(c.split()) >= 10]


def _doc_tag(filename: str) -> str:
    lower = filename.lower()
    if "runbook" in lower or "operations" in lower:
        return "operations"
    if "security" in lower or "access" in lower or "governance" in lower:
        return "security"
    if "migration" in lower or "object" in lower or "mapping" in lower:
        return "migration"
    if "troubleshoot" in lower:
        return "support"
    if "llm" in lower or "opsai" in lower or "rag" in lower:
        return "ai"
    if "data" in lower or "architecture" in lower:
        return "architecture"
    return "reference"


# ── Ledger source ──────────────────────────────────────────────────────────────

class LedgerSource(Source):
    """
    Indexes unsupported items and resolved notes from state ledgers.
    Each ledger item becomes a Document with full context.
    Resolved items are particularly valuable — they are learned patterns.
    """
    SOURCE_TYPE = "ledger"

    def load(self, dirs: dict) -> list[Document]:
        state_dir = Path(dirs.get("state_dir", "state"))
        if not state_dir.exists():
            return []

        docs: list[Document] = []
        for ledger_path in sorted(state_dir.glob("*-ledger.json")):
            docs.extend(self._load_ledger(ledger_path))
        return docs

    def _load_ledger(self, path: Path) -> list[Document]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

        env = self._extract_env(path.name)
        docs: list[Document] = []

        # Index each unsupported/blocked item
        for i, item in enumerate(data.get("unsupported_items", [])):
            obj_type   = item.get("object_type", "unknown")
            obj_name   = item.get("object_name", "unknown")
            reason     = item.get("reason", "")
            action     = item.get("action", "")
            severity   = item.get("severity", "MANUAL")
            resolved   = item.get("resolved", False)
            note       = item.get("resolved_note", "")

            content = (
                f"Object type: {obj_type}\n"
                f"Object name: {obj_name}\n"
                f"Environment: {env}\n"
                f"Severity: {severity}\n"
                f"Reason: {reason}\n"
                f"Required action: {action}\n"
            )
            if resolved and note:
                content += f"Resolution applied: {note}\n"

            title = (
                f"[{env}] {obj_type}: {obj_name} — "
                f"{'Resolved' if resolved else severity}"
            )

            docs.append(Document(
                id=_make_id(f"ledger:{env}", i),
                source_type=self.SOURCE_TYPE,
                source_path=str(path.name),
                title=title,
                content=content,
                metadata={
                    "env":         env,
                    "object_type": obj_type,
                    "object_name": obj_name,
                    "severity":    severity,
                    "resolved":    resolved,
                },
                chunk_index=i,
            ))

        # Index phase summary (compact)
        phases = data.get("phases", {})
        if phases:
            phase_lines = []
            for phase, info in phases.items():
                status = info.get("status", "pending")
                notes  = info.get("notes", "")
                phase_lines.append(f"Phase '{phase}': {status}. {notes}")
            phase_content = "\n".join(phase_lines)
            docs.append(Document(
                id=_make_id(f"ledger:{env}:phases", 0),
                source_type=self.SOURCE_TYPE,
                source_path=str(path.name),
                title=f"[{env}] Migration phase summary",
                content=phase_content,
                metadata={"env": env, "object_type": "phase_summary"},
                chunk_index=9999,
            ))

        return docs

    @staticmethod
    def _extract_env(filename: str) -> str:
        stem = Path(filename).stem
        return stem.replace("-ledger", "").replace("_ledger", "")


# ── Discovery source ───────────────────────────────────────────────────────────

class DiscoverySource(Source):
    """
    Indexes high-level summaries from discovery JSON files.
    Does NOT index raw object data (too voluminous, mostly not useful for Q&A).
    Indexes: virtual service names + descriptions, pool details, cert metadata,
    connection types, and DataScript summaries.
    """
    SOURCE_TYPE = "discovery"

    def load(self, dirs: dict) -> list[Document]:
        disc_dir = Path(dirs.get("discovery_dir", "discovery"))
        if not disc_dir.exists():
            return []

        docs: list[Document] = []
        for disc_path in sorted(disc_dir.glob("*.json")):
            docs.extend(self._load_discovery(disc_path))
        return docs

    def _load_discovery(self, path: Path) -> list[Document]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

        meta = data.get("_meta", {})
        env  = path.stem.replace("_discovery", "").replace("-discovery", "")
        docs: list[Document] = []
        idx = 0

        # Virtual Services summary
        vs_list = data.get("virtual_services", [])
        if vs_list:
            vs_names = [v.get("name", "?") for v in vs_list]
            ds_count = sum(1 for v in vs_list if v.get("_has_datascripts"))
            content = (
                f"Environment: {env}\n"
                f"Total Virtual Services: {len(vs_list)}\n"
                f"VS with DataScripts: {ds_count}\n"
                f"Virtual Service names: {', '.join(vs_names[:30])}"
            )
            docs.append(Document(
                id=_make_id(f"disc:{env}:vs", idx),
                source_type=self.SOURCE_TYPE,
                source_path=str(path.name),
                title=f"[{env}] Virtual Services ({len(vs_list)} total)",
                content=content,
                metadata={"env": env, "object_type": "virtual_services"},
                chunk_index=idx,
            ))
            idx += 1

        # Individual DataScript summaries (useful for Q&A)
        for ds in data.get("datascripts", []):
            ds_name   = ds.get("name", "?")
            events    = ds.get("_events", [])
            lines     = ds.get("_total_lines", 0)
            hint      = ds.get("_llm_hint", "")
            content = (
                f"DataScript: {ds_name}\n"
                f"Environment: {env}\n"
                f"Events: {', '.join(events)}\n"
                f"Total lines: {lines}\n"
            )
            if hint:
                content += f"Migration hint: {hint}\n"
            docs.append(Document(
                id=_make_id(f"disc:{env}:ds:{ds_name}", idx),
                source_type=self.SOURCE_TYPE,
                source_path=str(path.name),
                title=f"[{env}] DataScript: {ds_name}",
                content=content,
                metadata={
                    "env": env,
                    "object_type": "datascript",
                    "name": ds_name,
                },
                chunk_index=idx,
            ))
            idx += 1

        # SSL certificates
        certs = data.get("ssl_certificates", [])
        if certs:
            cert_lines = []
            for cert in certs:
                days     = cert.get("_days_until_expiry")
                exp      = cert.get("_exportable", True)
                name     = cert.get("name", "?")
                status   = "NOT EXPORTABLE (HSM)" if not exp else (
                    f"expires in {days}d" if days is not None else "ok"
                )
                cert_lines.append(f"  - {name}: {status}")
            docs.append(Document(
                id=_make_id(f"disc:{env}:certs", idx),
                source_type=self.SOURCE_TYPE,
                source_path=str(path.name),
                title=f"[{env}] SSL Certificates ({len(certs)} total)",
                content=(
                    f"Environment: {env}\n"
                    f"Total certificates: {len(certs)}\n"
                    + "\n".join(cert_lines[:30])
                ),
                metadata={"env": env, "object_type": "ssl_certificates"},
                chunk_index=idx,
            ))
            idx += 1

        # External connections
        conns = data.get("connections", [])
        if conns:
            conn_lines = [
                f"  - {c.get('name','?')} ({c.get('type','?')})"
                for c in conns
            ]
            docs.append(Document(
                id=_make_id(f"disc:{env}:conns", idx),
                source_type=self.SOURCE_TYPE,
                source_path=str(path.name),
                title=f"[{env}] External Connections",
                content=(
                    f"Environment: {env}\n"
                    f"External systems connected to Avi:\n"
                    + "\n".join(conn_lines)
                ),
                metadata={"env": env, "object_type": "connections"},
                chunk_index=idx,
            ))

        return docs


# ── Mapping source ─────────────────────────────────────────────────────────────

class MappingSource(Source):
    """
    Indexes Avi → FortiADC mapping tables from transformers/mappings.py.
    Makes mapping rules searchable: "what does Avi LB algorithm X map to?"
    Parses the Python dict literals statically using regex (no exec).
    """
    SOURCE_TYPE = "mappings"

    def load(self, dirs: dict) -> list[Document]:
        tool_root = Path(dirs.get("tool_root", "."))
        mappings_path = tool_root / "transformers" / "mappings.py"
        if not mappings_path.exists():
            return []

        try:
            source = mappings_path.read_text(encoding="utf-8")
        except Exception:
            return []

        return self._extract_mapping_docs(source)

    @staticmethod
    def _extract_mapping_docs(source: str) -> list[Document]:
        """
        Extract named dict constants from mappings.py and create searchable docs.
        Pattern: CONSTANT_NAME = { ... }
        """
        docs: list[Document] = []
        # Find all top-level dict assignments
        pattern = re.compile(
            r'^([A-Z_]{4,})\s*[:=]\s*\{([^}]{5,})\}',
            re.MULTILINE | re.DOTALL
        )
        idx = 0
        for match in pattern.finditer(source):
            name    = match.group(1)
            body    = match.group(2)

            # Extract key: value lines
            pairs = re.findall(r'"([^"]+)"\s*:\s*"?([^",\n#]+)"?', body)
            if len(pairs) < 2:
                continue

            lines = [f"  {k} → {v.strip()}" for k, v in pairs if v.strip() and v.strip() != "None"]
            content = (
                f"Mapping table: {name}\n"
                f"This table translates Avi configuration values to FortiADC equivalents.\n"
                f"Entries:\n" + "\n".join(lines[:40])
            )

            # Derive a human title from the constant name
            human = name.replace("_", " ").title()
            docs.append(Document(
                id=_make_id(f"mappings:{name}", idx),
                source_type="mappings",
                source_path="transformers/mappings.py",
                title=f"Mapping: {human}",
                content=content,
                metadata={"mapping_table": name, "entry_count": len(pairs)},
                chunk_index=idx,
            ))
            idx += 1

        return docs


# ── Built-in curated facts source ─────────────────────────────────────────────

class BuiltinFactsSource(Source):
    """
    Hardcoded migration knowledge facts — always indexed, no files needed.
    Covers FortiADC constraints, known limitations, and common Q&A patterns.
    This is the "baked-in" knowledge layer that never needs a doc file.
    """
    SOURCE_TYPE = "builtin"

    _FACTS: list[tuple[str, str]] = [
        (
            "FortiADC health monitor timeout constraint",
            "FortiADC requires that the health monitor timeout MUST be less than "
            "the health monitor interval. This is a hard constraint. If your Avi "
            "health monitor has timeout >= interval, the migration tool automatically "
            "adjusts timeout to (interval - 1). This is recorded as an approximation "
            "in the state ledger."
        ),
        (
            "Avi LB algorithm SOURCE_IP fallback",
            "Avi's SOURCE_IP load balancing algorithm is not directly supported in "
            "FortiADC. The migration tool falls back to Round Robin (RR) and records "
            "this as a WARN. If source-IP persistence is required, configure a "
            "source-address persistence profile in FortiADC instead."
        ),
        (
            "DataScript migration — no auto-migration",
            "Avi DataScripts (Lua) cannot be automatically migrated to FortiADC. "
            "FortiADC does not have an equivalent scripting engine. Each DataScript "
            "must be analysed individually. Common replacements: HTTP content routing "
            "rules (for path-based routing), WAF custom rules (for security logic), "
            "or application-layer changes (for business logic)."
        ),
        (
            "HSM-backed certificates cannot be exported",
            "Certificates stored in Avi with HSM-backed private keys cannot be "
            "exported. The private key is non-exportable by design. These certs "
            "are flagged as BLOCKED in the migration tool. Resolution: engage your "
            "PKI/Wintel team to issue a replacement certificate from the internal CA "
            "and import it directly into FortiADC."
        ),
        (
            "Pool member weight 0 in FortiADC",
            "FortiADC does not support a pool member weight of 0 (which in Avi means "
            "'disabled but present'). The migration tool sets minimum weight to 1. "
            "To disable a pool member in FortiADC, use 'status: disable' on the "
            "real server, not the weight."
        ),
        (
            "FortiADC VDOM isolation",
            "FortiADC uses VDOMs to isolate environments. Each Avi tenant maps to "
            "one FortiADC VDOM. The migration tool enforces this via the config.yaml "
            "environments section. Every FortiADC API call includes ?vdom=<name> to "
            "scope operations to the correct VDOM."
        ),
        (
            "DNS cutover via Infoblox WAPI",
            "The migration tool performs DNS cutover by updating Avi VIP A-records "
            "in Infoblox via the WAPI v2.10 REST API. DNS records are backed up to "
            "state/<env>-dns-backup.json before any change. Rollback restores records "
            "from this backup. Reduce DNS TTLs to 60-300 seconds at least 24 hours "
            "before the maintenance window."
        ),
        (
            "Migration pipeline phases in order",
            "The migration pipeline has 7 phases in order: "
            "1. Discover — read Avi API (read-only), snapshot to discovery/env.json. "
            "2. Analyse — dependency graph, compatibility check, complexity score. "
            "3. Transform — translate Avi objects to FortiADC API payloads. "
            "4. Dry-run — validate FortiADC readiness without making changes. "
            "5. Deploy — create FortiADC objects (Avi stays live). "
            "6. Parallel-run — 7 day validation with both platforms live. "
            "7. DNS cutover — switch traffic by updating DNS A-records."
        ),
        (
            "FortiADC certificate deployment order",
            "Objects must be deployed to FortiADC in strict dependency order: "
            "SSL certificates → SSL profiles → health checks → real server pools "
            "→ real servers → persistence profiles → virtual servers. "
            "The FortiADC deployer enforces this order. Deploying out of order "
            "causes reference errors."
        ),
        (
            "GSLB migration is out of tool scope",
            "GSLB (Global Server Load Balancing) migration is not handled by this "
            "tool. Avi and FortiADC use fundamentally different GSLB models. "
            "A separate design exercise is required. The tool detects GSLB usage "
            "and flags it as CRITICAL pattern P07, but does not attempt to migrate it. "
            "FortiGSLB or DNS-based GSLB must be designed separately."
        ),
        (
            "Dry-run mode is always the default",
            "The migration tool defaults to dry-run mode for all deployment operations. "
            "The --execute flag must be explicitly passed to make real changes to "
            "FortiADC. The wizard enforces a check that dry-run was reviewed first. "
            "In dry-run mode, all payloads are constructed and validated but no "
            "API calls are made to FortiADC."
        ),
        (
            "import-avi-json command for offline import",
            "If you have an Avi configuration JSON export (not from live API discovery), "
            "use: python migrate.py import-avi-json --input <file.json> --env <name> "
            "This normalizes the export into the tool's discovery schema and produces "
            "a discovery/env.json that all subsequent phases (analyse, transform, deploy) "
            "can work from, without needing a live Avi controller connection."
        ),
        (
            "Governance gate requires CR ID and SoD approver",
            "Before executing a live deployment (--execute), the governance gate "
            "requires: a Change Request ID (e.g. CHG0012345) and a Separation of "
            "Duties approver (a different person from the operator). Both are "
            "logged in the audit trail. The --override-governance flag bypasses "
            "this but is always logged and requires justification."
        ),
        (
            "LLM is optional and advisory only",
            "The LLM integration in this tool is entirely optional. All migration "
            "decisions (translate, deploy, rollback) are fully deterministic and do "
            "not require an LLM. The LLM is used only for: DataScript analysis, "
            "plain-English report enrichment, unsupported item guidance, and Q&A "
            "via the RAG system. Set llm_gateway.enabled: false to disable all LLM."
        ),
    ]

    def load(self, dirs: dict) -> list[Document]:
        docs: list[Document] = []
        for i, (title, content) in enumerate(self._FACTS):
            docs.append(Document(
                id=_make_id(f"builtin:{i}", 0),
                source_type=self.SOURCE_TYPE,
                source_path="builtin",
                title=title,
                content=content,
                metadata={"fact_index": i},
                chunk_index=i,
            ))
        return docs


# ── Python Code source ─────────────────────────────────────────────────────────

class PythonCodeSource(Source):
    """
    Indexes the tool's own source code (.py files).
    Focuses on: core/, services/, transformers/, and reporters/.
    Chunks by class/function boundaries using regex for better technical context.
    """
    SOURCE_TYPE = "code"

    # Only index code that defines migration logic (mappings, transformations, reporting rules)
    # Avoid indexing internal tool plumbing (core, services, ui) which creates noise for operators.
    _CODE_DIRS = ["transformers", "reporters"]

    def load(self, dirs: dict) -> list[Document]:
        tool_root = Path(dirs.get("tool_root", "."))
        docs: list[Document] = []

        for dname in self._CODE_DIRS:
            code_dir = tool_root / dname
            if not code_dir.exists():
                continue
            
            for py_path in code_dir.rglob("*.py"):
                # Skip build artifacts and init files if empty
                if "__pycache__" in str(py_path) or py_path.name == "__init__.py":
                    continue
                docs.extend(self._load_file(py_path, tool_root))

        return docs

    def _load_file(self, path: Path, root: Path) -> list[Document]:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return []

        rel_path = str(path.relative_to(root))
        
        # Smart chunking: find class/def boundaries
        sections = self._split_by_signature(content)
        
        docs = []
        for i, (title, section_text) in enumerate(sections):
            # Further split large sections if needed
            for j, text_part in enumerate(_split_text(section_text, max_chars=1200)):
                doc = Document(
                    id=_make_id(f"code:{rel_path}", i * 100 + j),
                    source_type=self.SOURCE_TYPE,
                    source_path=rel_path,
                    title=f"{rel_path}: {title}",
                    content=text_part,
                    metadata={
                        "filename": rel_path,
                        "module":   rel_path.replace("/", ".").replace("\\", ".").replace(".py", ""),
                        "type":     "function" if "def " in title else ("class" if "class " in title else "module")
                    },
                    chunk_index=i * 100 + j,
                )
                docs.append(doc)
        return docs

    @staticmethod
    def _split_by_signature(text: str) -> list[tuple[str, str]]:
        """Split python code into logical chunks based on class/def signatures."""
        lines = text.splitlines()
        sections: list[tuple[str, str]] = []
        
        current_title = "module"
        current_lines: list[str] = []
        
        # Pattern for class or def starting at col 0
        sig_pattern = re.compile(r"^(class|def)\s+([a-zA-Z0-9_]+)")

        for line in lines:
            match = sig_pattern.match(line)
            if match:
                # Save previous section
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                
                # Start new section
                stype = match.group(1)
                sname = match.group(2)
                current_title = f"{stype} {sname}"
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))

        return sections


# ── Experience source ──────────────────────────────────────────────────────────

class ExperienceSource(Source):
    """
    Indexes 'Verified Experiences' from state/experiences/.
    These are the 'learning' facts saved from previous AI interactions.
    They are given higher relevance priority in the retrieval pipeline.
    """
    SOURCE_TYPE = "experience"

    def load(self, dirs: dict) -> list[Document]:
        exp_dir = Path(dirs.get("state_dir", "state")) / "experiences"
        if not exp_dir.exists():
            return []

        docs: list[Document] = []
        # Reuse MarkdownSource logic for experience files
        from core.rag.indexer import MarkdownSource
        inner_loader = MarkdownSource()
        
        for md_path in exp_dir.glob("*.md"):
            try:
                # We override source_type to ensure matches are identified as experiences
                file_docs = inner_loader.load_file(md_path, exp_dir.parent)
                for d in file_docs:
                    d.source_type = self.SOURCE_TYPE
                    docs.append(d)
            except Exception:
                continue
        return docs


# ── Registry ───────────────────────────────────────────────────────────────────

#: All available source classes, keyed by SOURCE_TYPE
INDEX_SOURCES: dict[str, type[Source]] = {
    "docs":     MarkdownSource,
    "ledger":   LedgerSource,
    "discovery":DiscoverySource,
    "mappings": MappingSource,
    "builtin":  BuiltinFactsSource,
    "code":     PythonCodeSource,
    "experience": ExperienceSource,
}


# ── index_all ──────────────────────────────────────────────────────────────────

def index_all(
    dirs: dict,
    sources: Sequence[str] | None = None,
    rebuild: bool = False,
) -> dict:
    """
    Build or rebuild the RAG index.

    Args:
        dirs     — standard dirs dict (tool_root, state_dir, discovery_dir, ...)
        sources  — list of source types to index; None = all
        rebuild  — if True, delete existing chunks for selected sources first

    Returns:
        {
          indexed: int,          # total chunks written
          by_source: dict,       # chunks per source type
          duration_ms: int,
          errors: list[str],
        }
    """
    import time
    from core.rag.vectorstore import VectorStore

    db_path = Path(dirs.get("state_dir", "state")) / "rag_index.db"
    store   = VectorStore(str(db_path))

    active_sources = sources or list(INDEX_SOURCES.keys())
    total    = 0
    by_src: dict[str, int] = {}
    errors:  list[str] = []
    t0       = time.monotonic()

    for src_type in active_sources:
        cls = INDEX_SOURCES.get(src_type)
        if cls is None:
            errors.append(f"Unknown source type: {src_type}")
            continue

        try:
            if rebuild:
                store.delete_by_source(src_type)

            source   = cls()
            docs     = source.load(dirs)
            written  = store.upsert(docs)
            total   += written
            by_src[src_type] = written
        except Exception as e:
            errors.append(f"{src_type}: {e}")

    return {
        "indexed":     total,
        "by_source":   by_src,
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "errors":      errors,
        "db_path":     str(db_path),
    }
