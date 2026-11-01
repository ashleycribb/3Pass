# Critic Review Report (Stage 7)

**Target Document**: [`docs/designs/3pass_research_corpus_architecture.md`](file:///c:/Users/Ashley/Downloads/3Pass/docs/designs/3pass_research_corpus_architecture.md)  
**Reviewers**: `egm-design` (Design Critic), `egm-structural` (Structural Validator), `egm-style` (Copy Editor)  
**Scope**: Lines 1–166 (`FeatureRange`)  
**Standards**: `feature-spec-standard.md`  
**Date**: 2026-11-01  

---

## Executive Summary
The review across system architecture, structural integrity, and technical writing identified significant gaps requiring remediation before implementation:
1. **Missing Mandatory "Risks & Mitigations" Section**: Violates `feature-spec-standard.md` by failing to specify concrete failure modes (Google Drive lock/sync races, PDF parsing errors, API rate limits, database corruption).
2. **Ambiguous & Contradictory Database Engine**: Specification fluctuates between SQLite and DuckDB, cites incompatible extensions (`sqlite-vec` vs. DuckDB VSS), and uses a confusing `.s3a` extension.
3. **Circular Pipeline Dependency**: Pass 2/3 synthesis tasks require querying literature, but the `.s3a` database is placed strictly downstream of the wiki without incremental update mechanics.
4. **Unbudgeted Token Economics**: No model tiers, token ceilings, or cost models are defined for processing hundreds of long academic manuscripts.
5. **Structural & Phrasing Flaws**: Asymmetric single-child headings, inconsistent divider syntax, and non-objective marketing/emotional phrasing.

---

## Prioritized Findings

### Finding 1: Missing Mandatory "Risks & Mitigations" Section
* **Severity**: 🔴 Blocker
* **Reviewer**: `egm-design`, `egm-structural`
* **Affected Scope**: Section 5 / End of `FeatureRange` (Lines 158–166)
* **Discrepancy Description**: The Feature Specification Standard mandates that specifications explicitly evaluate failure modes, constraints, and mitigations. The document omits a `## Risks & Mitigations` section.
* **Recommendation**: Add a dedicated `## Risks & Mitigations` section addressing:
  - Google Drive desktop file-lock collisions and sync races.
  - Multi-column and OCR extraction corruption on scanned PDFs.
  - API rate-limits and token exhaustion during batch parsing.
  - SQLite/database file corruption and synchronization drift.

### Finding 2: Contradictory Database Engine & Storage Specifications
* **Severity**: 🔴 Blocker
* **Reviewer**: `egm-design`, `egm-comprehension`, `egm-zoom-out`
* **Affected Scope**: Lines 15, 53–56, 114, 153–156, 161–163
* **Discrepancy Description**: The document defines the back-end as an "Encapsulated SQLite/DuckDB Container", but mentions SQLite locks and cites `sqlite-vec or DuckDB VSS`. SQLite and DuckDB have radically different concurrency models and dependency requirements. Furthermore, using `.s3a` as an extension conflicts with Hadoop protocols and breaks standard tooling.
* **Recommendation**: Commit decisively to SQLite with a standard `.db` or `.sqlite` extension. Pre-specify the exact embedding and vector indexing mechanism (or defer binary vector tables in favor of on-demand Markdown embedding).

### Finding 3: Unspecified Extraction Pipeline for Citation DAG & Knowledge Graph
* **Severity**: 🟡 Major
* **Reviewer**: `egm-design`, `egm-zoom-out`, `egm-comprehension`
* **Affected Scope**: Lines 53–56, 116–118, 134–135
* **Discrepancy Description**: The spec features a Citation DAG Engine and Graph Engine, but specifies zero ingestion mechanics for how citation lineage or DOIs are acquired from raw PDFs.
* **Recommendation**: Define the external metadata source (e.g., Crossref, OpenAlex, or Semantic Scholar API) and the exact schema for citation edges and concept nodes.

### Finding 4: Circular Dependency in Pass 2 / Pass 3 Analysis Flow
* **Severity**: 🟡 Major
* **Reviewer**: `egm-design`
* **Affected Scope**: Lines 93–139
* **Discrepancy Description**: The diagram places the database compilation strictly downstream of the wiki: `P2/P3 -> wiki/ -> Compiler -> S3ADB -> RAG/DAG/Graph`. However, Pass 2/3 agents need to execute RAG queries against previously ingested literature to spot gaps and synthesize findings.
* **Recommendation**: Model an incremental indexing loop or support direct in-memory search over active wiki markdown files alongside historical database queries.

### Finding 5: Unbudgeted LLM Quota and Token Economics
* **Severity**: 🟡 Major
* **Reviewer**: `egm-design`
* **Affected Scope**: Lines 44–47, 99–103
* **Discrepancy Description**: Deconstructing hundreds of papers (averaging 25,000–50,000 tokens each) will consume millions of tokens. The plan lacks model tiering, chunking constraints, and rate-limit backoffs.
* **Recommendation**: Specify model tiers (e.g. lightweight models for Pass 1 triage, reasoning models for Pass 2 gap analysis) and batch processing limits.

### Finding 6: Asymmetric Numbered Heading Hierarchy
* **Severity**: 🟡 Major
* **Reviewer**: `egm-structural`
* **Affected Scope**: Lines 88–91 (`## Technical Plan (Architecture)`)
* **Discrepancy Description**: Introducing a single numbered subsection `### 1. High-Level Architecture Overview` without any subsequent ordinal siblings (`### 2. ...`) creates a dangling, asymmetric hierarchy.
* **Recommendation**: Remove the numeral prefix or introduce peer numbered sections.

### Finding 7: Non-Objective Phrasing and Emotional Descriptors
* **Severity**: 🟡 Major
* **Reviewer**: `egm-style`
* **Affected Scope**: Lines 11, 15, 22, 28, 30, 35, 151, 155
* **Discrepancy Description**: Use of dramatic language ("bypassed this trap", "Doctoral Discovery Dilemma", "cognitive collapse", "clumsy graph traversals") and promotional adjectives ("High-Performance Storage").
* **Recommendation**: Rephrase using objective engineering terminology (e.g. "cognitive overload", "unindexed PDF files", "high-latency multi-file traversals").

### Finding 8: Heading Deviations and Divider Inconsistencies
* **Severity**: 🟢 Nit
* **Reviewer**: `egm-structural`, `egm-style`
* **Affected Scope**: Lines 19, 42, 63, 141
* **Discrepancy Description**: Naked URL on Line 42; 3-character divider on Line 141 vs 80-character dividers; first-person phrasing on Line 63.
* **Recommendation**: Encapsulate URLs in Markdown links, standardize divider syntax, and adopt third-person phrasing throughout.
