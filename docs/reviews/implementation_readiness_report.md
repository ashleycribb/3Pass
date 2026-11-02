# Implementation Readiness Review Report (Stage 9)

**Target Document**: [`docs/designs/3pass_research_corpus_architecture.md`](file:///c:/Users/Ashley/Downloads/3Pass/docs/designs/3pass_research_corpus_architecture.md)  
**Reviewers**: `egm-implementation-readiness` (Pragmatic Tech Lead), `egm-codebase-details` (Codebase Auditor)  
**Scope**: Lines 167–258 (`DetailedRange`)  
**Standard**: `implementation-readiness-standard.md`  
**Date**: 2026-11-01  

---

## Executive Summary
The target implementation plan fails the Implementation Readiness Standard. It violates the **Zero-Decision Rule** by omitting concrete database DDL schemas, vector indexing engines, and embedding models; it lacks **Phasing and Testable Checkpoints**; it references phantom scripts (`run_pass2.py`) in CUJ 2 that are absent from the target file list; and it fails to specify the migration and lifecycle path for existing workspace scripts and ledger records.

---

## Prioritized Findings

### Finding 1: Plan Violates Zero-Decision Rule on Database Schema, Engine, & Vector Extensions
* **Severity**: 🔴 Blocker
* **Reviewers**: `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope**: Lines 162, 200–204 (`scripts/compile_database.py`)
* **Discrepancy Description**: Section 5 specifies creating `ledger/research_corpus.s3a` as a "SQLite database with tables for `sources`, `sections`, `vectors`, `citation_dag`, and `concept_graph`" without defining column definitions, data types, primary/foreign keys, or constraints. Furthermore, the plan hedges on the database engine (`sqlite-vec or DuckDB VSS`) and omits the embedding model, dimensions, and chunking parameters.
* **Recommendation**: Pre-select either SQLite or DuckDB decisively; provide explicit SQL DDL schemas for all tables; specify exact embedding models (e.g. `fastembed`, `all-MiniLM-L6-v2`) and chunking boundaries.

### Finding 2: Omission of Referenced CUJ Script `scripts/run_pass2.py` in Target Files
* **Severity**: 🔴 Blocker
* **Reviewers**: `egm-implementation-readiness`, `egm-codebase-details`, `egm-comprehension`
* **Affected Scope**: Line 78 (CUJ 2) vs Lines 169–235 (Target Files)
* **Discrepancy Description**: CUJ 2 instructs running: `python scripts/run_pass2.py --topic "AI Scaffolding"`. However, `scripts/run_pass2.py` is missing from the Target Files section. Existing script `scripts/pass2_deep_analysis.py` has hardcoded IDs and lacks a `--topic` flag.
* **Recommendation**: Add `scripts/run_pass2.py` (or document the refactoring of `pass2_deep_analysis.py`) in the Target Files list with CLI arguments, scope, and rationale.

### Finding 3: Absence of Incremental Phasing and Testable Checkpoints
* **Severity**: 🟡 Major
* **Reviewers**: `egm-implementation-readiness`, `egm-codebase-details`
* **Affected Scope**: Lines 167–258
* **Discrepancy Description**: The plan presents an unsequenced flat list of 10 file additions/modifications without logical phases, dependency ordering, or intermediate milestone verification criteria.
* **Recommendation**: Partition implementation into sequential phases (e.g. *Phase 1: Config & Ledger Reconciliation; Phase 2: Snapshot & MarkItDown Extraction; Phase 3: Karpathy Wiki Engine; Phase 4: Query Engine & Catalog; Phase 5: Testing & Documentation*).

### Finding 4: Duplication of Existing Pipeline Code & Missing Lifecycle Directives
* **Severity**: 🟡 Major
* **Reviewers**: `egm-implementation-readiness`, `egm-codebase-details`, `egm-zoom-out`
* **Affected Scope**: Lines 181–190 (`scripts/ingest_drive.py`, `scripts/snapshot_extractor.py`) vs `scripts/inventory_and_sync.py`, `scripts/triage_wave.py`, `scripts/convert_markitdown.py`, `scripts/sync_delta.py`
* **Discrepancy Description**: `scripts/ingest_drive.py` duplicates existing working logic in `inventory_and_sync.py` and `triage_wave.py`. `snapshot_extractor.py` duplicates `convert_markitdown.py`. The plan does not mark existing scripts for refactoring or deletion.
* **Recommendation**: Refactor existing scripts to read from `config/corpus_config.yaml` rather than creating redundant parallel files.

### Finding 5: Drive Mount Path Drift from Canonical Ledger
* **Severity**: 🟡 Major
* **Reviewers**: `egm-codebase-details`, `egm-implementation-readiness`, `egm-zoom-out`
* **Affected Scope**: Lines 175, 183
* **Discrepancy Description**: The configuration specifies `drive_local_path: "G:\My Drive\Portfolio Hub\Research Corpus"`, while `ledger/source_ledger.json` (785 tracked sources) and all current scripts point to `G:\My Drive\Scholar Explorer\papers`. The plan lacks migration logic to reconcile records between the two paths.
* **Recommendation**: Provide an explicit migration step to reconcile or re-index records from `Scholar Explorer\papers` to `Portfolio Hub\Research Corpus`.

### Finding 6: Undocumented Mechanism for Citation DAG and DOI Resolution
* **Severity**: 🟡 Major
* **Reviewers**: `egm-codebase-details`, `egm-implementation-readiness`, `egm-design`
* **Affected Scope**: Lines 202, 209, 251
* **Discrepancy Description**: The plan specifies compiling a `citation_dag` table for "bidirectional citation tree traversal from DOIs", but neither `source_ledger.json` nor PyMuPDF extracts citation edges, nor is an external bibliographic API (OpenAlex, CrossRef) specified.
* **Recommendation**: Document the external bibliographic API integration (e.g. CrossRef / OpenAlex API) and define the schema for citation edges.

### Finding 7: Unstated LLM Dependencies & Execution Flow in `wiki_manager.py`
* **Severity**: 🟡 Major
* **Reviewers**: `egm-codebase-details`
* **Affected Scope**: Lines 191–198
* **Discrepancy Description**: `wiki_manager.py` is tasked with extracting literature gaps and generating topic pages, but the plan does not state whether this relies on an LLM API (provider, SDK, model, prompt template) or a heuristic parser.
* **Recommendation**: Specify whether gap extraction is rule-based or LLM-driven, detailing model selection and prompt templates.

### Finding 8: Test Infrastructure Lacks Fixtures and Mocking Strategy
* **Severity**: 🟡 Major
* **Reviewers**: `egm-codebase-details`, `egm-implementation-readiness`
* **Affected Scope**: Lines 226–230, 240–243
* **Discrepancy Description**: Unit tests depend on physical Drive paths (`G:\...`) and PDFs. The plan fails to define a `tests/fixtures/` directory, sample PDF fixtures, or mock strategies for Google Drive operations.
* **Recommendation**: Define `tests/fixtures/` with synthetic sample PDFs and mock metadata fixtures.

### Finding 9: Missing Dependency Manifest
* **Severity**: 🟢 Nit
* **Reviewers**: `egm-codebase-details`
* **Affected Scope**: Lines 169–235
* **Discrepancy Description**: The repository lacks `requirements.txt` or `pyproject.toml`. The architecture introduces new packages (`pyyaml`, vector extensions, `pytest`).
* **Recommendation**: Add `requirements.txt` to the Target Files list.

### Finding 10: Query Engine CLI Mode Discrepancy
* **Severity**: 🟢 Nit
* **Reviewers**: `egm-codebase-details`, `egm-comprehension`
* **Affected Scope**: Line 83 vs Lines 207–210
* **Discrepancy Description**: Line 83 demonstrates `--mode hybrid`, but Line 207 specifies only three modes (`--mode rag`, `--mode dag`, `--mode graph`), omitting `--mode hybrid`.
* **Recommendation**: Reconcile CLI documentation to include `--mode hybrid`.
