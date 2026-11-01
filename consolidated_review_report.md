# Consolidated Goldfish Review Report

**Target Document**: [`docs/designs/3pass_research_corpus_architecture.md`](file:///c:/Users/Ashley/Downloads/3Pass/docs/designs/3pass_research_corpus_architecture.md)  
**Date**: 2026-11-01  
**Execution Mode**: Uninterrupted Full Flow (5 Stages Completed)  
**Reviewer Lenses Evaluated**:
* Stage 5: `egm-zoom-out` (Skeptical Architect) -> [`zoom_out_report.md`](file:///c:/Users/Ashley/Downloads/3Pass/zoom_out_report.md)
* Stage 6: `egm-comprehension` (Naive Reader) -> [`comprehension_report.md`](file:///c:/Users/Ashley/Downloads/3Pass/comprehension_report.md)
* Stage 7: `egm-design`, `egm-structural`, `egm-style` -> [`critic_report.md`](file:///c:/Users/Ashley/Downloads/3Pass/critic_report.md)
* Stage 8: `egm-zoom-out` (Intermediate Zoom Out) -> [`intermediate_zoom_out_report.md`](file:///c:/Users/Ashley/Downloads/3Pass/intermediate_zoom_out_report.md)
* Stage 9: `egm-implementation-readiness`, `egm-codebase-details` -> [`implementation_readiness_report.md`](file:///c:/Users/Ashley/Downloads/3Pass/implementation_readiness_report.md)

---

## Executive Summary & Review Verdict

The technical design document establishes a clear user need and valuable vision: structuring academic literature into reproducible, machine-comprehensible units, supporting doctoral research question discovery via Karpathy's LLM Wiki pattern, and providing fast agentic retrieval.

However, the unanimous consensus across all 7 reviewer lenses is that **the design is not yet ready for immediate code implementation**. It contains **4 Critical Blockers**, **10 Major Architectural Gaps**, and **6 Minor Polish Issues**. 

### Primary Remediation Themes
1. **Zero-Decision Violation**: The database back-end hedges between SQLite and DuckDB, provides zero SQL table DDL schemas, and omits vector embedding models.
2. **Reinvention of Working Code**: Proposes new ingestion scripts (`ingest_drive.py`, `snapshot_extractor.py`) that reimplement code already working in `inventory_and_sync.py`, `triage_wave.py`, and `convert_markitdown.py`.
3. **Ghost Script in User Journeys**: CUJ 2 explicitly commands running `python scripts/run_pass2.py`, but this script is completely missing from the target files list.
4. **Missing Risks & Mitigations**: Violates the blueprint standard by omitting a concrete evaluation of Google Drive file-lock sync races, PDF parsing failures, and API rate limits.
5. **Path Drift**: Targets `G:\My Drive\Portfolio Hub\Research Corpus` while the active 785-paper ledger is rooted at `G:\My Drive\Scholar Explorer\papers`, with zero migration logic.

---

## Prioritized Action Items

### 🔴 Blockers (Must Resolve Before Coding)

#### B1: Add Dedicated "Risks & Mitigations" Section
* **Source Lenses**: `egm-design`, `egm-structural`
* **Issue**: The document omits failure mode analysis.
* **Remediation**: Add a formal `## Risks & Mitigations` section explicitly detailing:
  - Google Drive desktop file-lock collisions, inode sync latency, and cloud-drive disconnects.
  - Multi-column and OCR extraction corruption on scanned/complex PDFs.
  - API rate-limiting (HTTP 429) and token exhaustion during batch parsing.
  - SQLite database file locking and synchronization drift.

#### B2: Resolve Database Engine, DDL Schemas, and Vector Specifications
* **Source Lenses**: `egm-implementation-readiness`, `egm-codebase-details`, `egm-design`, `egm-comprehension`
* **Issue**: The plan fluctuates between SQLite and DuckDB, cites contradictory extensions (`sqlite-vec` vs `DuckDB VSS`), and omits SQL schemas and embedding models.
* **Remediation**:
  - Pre-select a single engine (recommend standard SQLite with `.db` extension for zero-dependency simplicity on Windows).
  - Provide full SQL `CREATE TABLE` DDL for all tables (`sources`, `sections`, `vectors`, `citation_dag`, `concept_graph`).
  - Specify the exact embedding model (e.g. `sentence-transformers/all-MiniLM-L6-v2` or `fastembed`), vector dimensions (e.g. 384), and distance metric (cosine).

#### B3: Enumerate `scripts/run_pass2.py` in Target Files
* **Source Lenses**: `egm-comprehension`, `egm-implementation-readiness`, `egm-codebase-details`
* **Issue**: CUJ 2 references `scripts/run_pass2.py --topic "AI Scaffolding"`, but this file is absent from the Detailed Implementation Plan.
* **Remediation**: Add `scripts/run_pass2.py` to the Target Files list, document its CLI parameters (`--topic`, `--id`, `--limit`), and specify how it leverages or refactors `scripts/pass2_deep_analysis.py`.

#### B4: Refactor Existing Scripts Instead of Re-implementing Ingestion & Conversion
* **Source Lenses**: `egm-zoom-out`, `egm-implementation-readiness`, `egm-codebase-details`
* **Issue**: Proposes creating `scripts/ingest_drive.py` and `scripts/snapshot_extractor.py`, duplicating existing functionality in `scripts/inventory_and_sync.py`, `scripts/triage_wave.py`, and `scripts/convert_markitdown.py`.
* **Remediation**: Remove `scripts/ingest_drive.py` and `scripts/snapshot_extractor.py` as separate files. Update the plan to mark existing scripts (`inventory_and_sync.py`, `triage_wave.py`, `convert_markitdown.py`) for `(Modify)` to read paths dynamically from `config/corpus_config.yaml`.

---

### 🟡 Major Findings (High Architectural Impact)

#### M1: Eliminate Dual-State Storage Drift or Clarify Compiler Role
* **Source Lenses**: `egm-zoom-out`, `egm-design`
* **Issue**: Karpathy's LLM Wiki treats Markdown files as the database. Compiling wiki notes into a binary `.s3a` file introduces staleness whenever markdown notes are edited.
* **Remediation**: Clarify that Markdown files in `wiki/` are the authoritative human/agent workspace, while the database acts strictly as an accelerated secondary search index that rebuilds incrementally or on-demand.

#### M2: Partition Detailed Implementation Plan into Phased Checkpoints
* **Source Lenses**: `egm-implementation-readiness`, `egm-codebase-details`
* **Issue**: Flat unsequenced list of 10 files without dependency ordering or milestone verification.
* **Remediation**: Structure implementation into sequential phases:
  - *Phase 1: Config, Dependencies & Ledger Path Reconciliation*
  - *Phase 2: Ingestion & Markdown Extraction Enhancement*
  - *Phase 3: Karpathy LLM Wiki Infrastructure (`wiki/`)*
  - *Phase 4: SQLite Database Compilation & Vector Indexing*
  - *Phase 5: Query Engine CLI & Catalog Generator*
  - *Phase 6: Integration Testing & Verification*

#### M3: Specify External API Source for Citation DAG & DOI Resolution
* **Source Lenses**: `egm-design`, `egm-comprehension`, `egm-zoom-out`, `egm-codebase-details`
* **Issue**: Proposes a Citation DAG engine without explaining where citation trees originate.
* **Remediation**: Document the bibliographic metadata pipeline (e.g. using Crossref API or OpenAlex API via DOI lookup) to populate citation edges in `citation_dag`.

#### M4: Reconcile Canonical Ledger Drive Path Migration
* **Source Lenses**: `egm-zoom-out`, `egm-codebase-details`, `egm-design`
* **Issue**: `source_ledger.json` records 785 files under `Scholar Explorer\papers`, while the new configuration targets `Portfolio Hub\Research Corpus`.
* **Remediation**: Add an explicit reconciliation step in Phase 1 to verify whether `Portfolio Hub` replaces or aggregates `Scholar Explorer`, re-pointing paths without duplicating Source IDs.

#### M5: Resolve Circular Dependency in Pass 2 / Pass 3 Synthesis Flow
* **Source Lenses**: `egm-design`
* **Issue**: Pass 2/3 agents require literature retrieval to identify gaps, but the database is placed downstream of the wiki in the flow diagram.
* **Remediation**: Update the architecture diagram and data flow to allow active Pass 2/3 agents to query previous corpus indices while streaming new synthesis notes into `wiki/`.

#### M6: Consolidate Redundant Synthesis Surfaces
* **Source Lenses**: `egm-zoom-out`
* **Issue**: Four parallel synthesis surfaces (`wiki/`, `living_doc/synthesis.md`, `CONTENTS.md`, `ledger/source_ledger.md`) create user confusion regarding data authority.
* **Remediation**: Define clear roles: `ledger/source_ledger.md` is the administrative inventory; `wiki/` is the active exploratory research workspace; `living_doc/synthesis.md` and `CONTENTS.md` are the formal deliverables.

#### M7: Define Token Economics and Model Tiering
* **Source Lenses**: `egm-design`
* **Issue**: Processing hundreds of 25,000-token papers risks severe API costs and rate-limiting without budget constraints.
* **Remediation**: Specify model tiers (lightweight models for Pass 1 triage, reasoning models for Pass 2 gap analysis) and batch execution ceilings.

#### M8: Document Relevance Scoring Rubric and Thresholds
* **Source Lenses**: `egm-comprehension`
* **Issue**: Scoring dimensions (Evidence Evaluation, Learner Agency, Traceable Workflows) are project-specific terms without definitions or scoring rules.
* **Remediation**: Add a brief section or appendix defining the 1–5 scoring rubric and the threshold logic determining Include vs Maybe vs Exclude.

#### M9: Define Test Fixtures and Mocking Strategy
* **Source Lenses**: `egm-implementation-readiness`, `egm-codebase-details`
* **Issue**: Tests depend on physical Google Drive mount without fixtures.
* **Remediation**: Specify a `tests/fixtures/` directory containing mock PDFs and metadata to allow isolated offline CI testing.

#### M10: Refactor Style, Tone, and Heading Asymmetry
* **Source Lenses**: `egm-style`, `egm-structural`
* **Issue**: Narrative emotional descriptors ("cognitive collapse", "trap") and a singleton subsection (`### 1. High-Level Architecture Overview`).
* **Remediation**: Adopt objective technical phrasing and fix heading hierarchy symmetry.

---

### 🟢 Nits (Low-Risk Polish)

* **N1 (Standard Database Extension)**: Rename `research_corpus.s3a` to standard `research_corpus.db` or `research_corpus.sqlite`.
* **N2 (Dependency Manifest)**: Add `requirements.txt` to Target Files (specifying `pyyaml`, `markitdown`, `pymupdf`, `fastembed`, `pytest`).
* **N3 (Query SLA Clarification)**: Clarify that the <500ms SLA applies to local index lookups excluding remote API network round-trips.
* **N4 (Platform-Agnostic Path Handling)**: Document how non-`G:\` Drive mount letters are detected or configured.
* **N5 (Reconcile CLI Mode)**: Ensure `--mode hybrid` is documented in both CUJ 3 and `scripts/query_engine.py` specifications.
* **N6 (Markdown Formatting Consistency)**: Encapsulate naked URLs, standardize 80-character dividers, and link `microsoft/markitdown`.

---

## Traceability Matrix

| Finding ID | Title | Severity | Reviewer Lenses | Stage Report Source |
|---|---|---|---|---|
| **B1** | Missing Mandatory Risks & Mitigations Section | 🔴 Blocker | `egm-design`, `egm-structural` | `critic_report.md` |
| **B2** | Zero-Decision Violation: DB Engine & DDL Schemas | 🔴 Blocker | `egm-readiness`, `egm-codebase`, `egm-design` | `readiness_report.md` |
| **B3** | Omission of CUJ 2 Script `scripts/run_pass2.py` | 🔴 Blocker | `egm-comprehension`, `egm-readiness`, `egm-codebase` | `readiness_report.md` |
| **B4** | Code Duplication in Ingestion & Extraction | 🔴 Blocker | `egm-zoom-out`, `egm-readiness`, `egm-codebase` | `zoom_out_report.md` |
| **M1** | Dual-State Storage Compilation Drift | 🟡 Major | `egm-zoom-out`, `egm-design` | `zoom_out_report.md` |
| **M2** | Absence of Implementation Phasing | 🟡 Major | `egm-readiness`, `egm-codebase` | `readiness_report.md` |
| **M3** | Undocumented Ingestion Source for Citation DAG | 🟡 Major | `egm-comprehension`, `egm-design`, `egm-codebase` | `critic_report.md` |
| **M4** | Drive Mount Path Drift from Existing Ledger | 🟡 Major | `egm-zoom-out`, `egm-codebase`, `egm-design` | `zoom_out_report.md` |
| **M5** | Circular Dependency in Pass 2/3 Processing | 🟡 Major | `egm-design` | `critic_report.md` |
| **M6** | Redundant Synthesis & Catalog Layers | 🟡 Major | `egm-zoom-out` | `zoom_out_report.md` |
| **M7** | Undefined Token Economics & Model Tiering | 🟡 Major | `egm-design` | `critic_report.md` |
| **M8** | Undefined Relevance Scoring Rubric | 🟡 Major | `egm-comprehension` | `comprehension_report.md` |
| **M9** | Missing Test Fixtures and Mocking Strategy | 🟡 Major | `egm-readiness`, `egm-codebase` | `readiness_report.md` |
| **M10** | Non-Objective Phrasing & Heading Asymmetry | 🟡 Major | `egm-style`, `egm-structural` | `critic_report.md` |
| **N1** | Obscure `.s3a` File Extension | 🟢 Nit | `egm-zoom-out`, `egm-comprehension` | `zoom_out_report.md` |
| **N2** | Missing `requirements.txt` Manifest | 🟢 Nit | `egm-codebase` | `readiness_report.md` |
| **N3** | Unsubstantiated <500ms Query SLA | 🟢 Nit | `egm-design` | `critic_report.md` |
| **N4** | Hardcoded `G:\` Drive Letter Prerequisite | 🟢 Nit | `egm-comprehension` | `comprehension_report.md` |
| **N5** | CLI Mode Discrepancy (`--mode hybrid`) | 🟢 Nit | `egm-comprehension`, `egm-codebase` | `comprehension_report.md` |
| **N6** | Formatting & Lint Inconsistencies | 🟢 Nit | `egm-structural`, `egm-style` | `critic_report.md` |
