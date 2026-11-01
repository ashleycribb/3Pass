# Comprehension Review Report (Stage 6)

**Target Document**: [`docs/designs/3pass_research_corpus_architecture.md`](file:///c:/Users/Ashley/Downloads/3Pass/docs/designs/3pass_research_corpus_architecture.md)  
**Reviewer**: `egm-comprehension` (Naive Reader)  
**Date**: 2026-11-01  

---

### What this proposes
This document proposes a hybrid literature review architecture for doctoral research that ingests academic papers from Google Drive and processes them through an automated three-pass pipeline. The front-end is a human-navigable Markdown wiki (`wiki/`) following Karpathy's LLM Wiki pattern to track paper notes, emerging thematic syntheses, and an active literature gap register (`wiki/gaps.md`). The back-end compiles all wiki markdown, tables, vector embeddings, and citation lineage into a single encapsulated database container (`ledger/research_corpus.s3a`) to support sub-500ms tri-modal queries (RAG, citation DAG, and concept Graph) and generate a standardized catalog (`CONTENTS.md`).

### How the existing system works, per this document
According to the document, the current system stores 721+ academic papers directly in a Google Drive folder (`Portfolio Hub/Research Corpus`) mounted locally as `G:\My Drive\...`. The research corpus currently suffers from a "PDF ingestion bottleneck" where papers exist as opaque presentation files with corrupted multi-column text, fragmented mathematical glyphs, and lost tabular structures. In addition, researchers are burdened by having to navigate hundreds of loose files and folders without a structured mechanism to map literature contradictions or iteratively formulate thesis research questions.

---

## Findings

### Finding 1: Phantom Script in CUJ 2 (`scripts/run_pass2.py`)
* **Severity**: 🔴 Blocker
* **Affected Scope**: Lines 76–80 (CUJ 2) vs. Lines 167–236 (Detailed Implementation Plan)
* **Discrepancy Description & Rationale**: In CUJ 2 (line 78), the workflow instructs the researcher to execute Pass 2 on a thematic cluster using `python scripts/run_pass2.py --topic "AI Scaffolding"`. However, in the Detailed Implementation Plan (Target Files to Create/Modify), `scripts/run_pass2.py` does not exist. A new developer or user cannot execute CUJ 2 as specified.

### Finding 2: Unspecified Data Source for Citation DAG
* **Severity**: 🔴 Blocker
* **Affected Scope**: Lines 15, 55–56, 81–85, 116, 202, 209–210 (Citation DAG Engine)
* **Discrepancy Description & Rationale**: The Citation DAG Engine is featured as a core architectural pillar (tracking DOI lineage, parent theories, and empirical replications across the corpus). However, the document never describes how citation relationships or DOIs are acquired. It is unstated whether DOIs and citations are parsed from PDF bibliographies via regex, fetched via an external API (e.g., Crossref, Semantic Scholar, OpenAlex), or extracted by an LLM during Pass 2.

### Finding 3: Ambiguous Database Engine and Non-Standard `.s3a` Extension
* **Severity**: 🟡 Major
* **Affected Scope**: Lines 15, 53, 114, 161, 202 (`research_corpus.s3a` / Database Container)
* **Discrepancy Description & Rationale**: The database file is given the custom extension `.s3a`, but its underlying engine is ambiguous and contradictory. Line 114 defines it as an "Encapsulated SQLite/DuckDB Container", while line 161 cites "SQLite locks" and line 202 defines it specifically as a "SQLite database with tables for sources, sections, vectors...". Furthermore, in standard data engineering, `.s3a` designates the Amazon S3 connector protocol for Apache Hadoop, not a local relational database file.

### Finding 4: Undefined Relevance Scoring Rubric
* **Severity**: 🟡 Major
* **Affected Scope**: Lines 45, 183 ("Evidence Evaluation, Learner Agency, Traceable Workflows" Scoring)
* **Discrepancy Description & Rationale**: Pass 1 triage requires generating relevance scores across three specific dimensions: "Evidence Evaluation, Learner Agency, Traceable Workflows", which then dictate Include/Maybe/Exclude decisions. However, these three dimensions are project-specific domain concepts that are neither defined inline nor linked to any conceptual rubric. The scoring scale (e.g., 1–5 integer scale vs. boolean) and threshold logic are absent.

### Finding 5: Undefined Vector Embedding Stack
* **Severity**: 🟡 Major
* **Affected Scope**: Lines 54, 83–84, 115, 162, 202, 208 (Vector Embeddings & RAG Engine)
* **Discrepancy Description & Rationale**: The RAG engine is specified to use dense vector embeddings over markdown sections and tables. However, the document does not specify what embedding model is used (e.g., local sentence-transformers, OpenAI `text-embedding-3-small`), what vector dimensions are indexed, or how embeddings are generated.

### Finding 6: Undefined `--mode hybrid` in Query Engine
* **Severity**: 🟡 Major
* **Affected Scope**: Line 83 (CUJ 3) vs. Lines 207–211 (Query Engine Specification)
* **Discrepancy Description & Rationale**: In CUJ 3 (line 83), the user is instructed to query the database using `python scripts/query_engine.py --mode hybrid --query "..."`. However, in the Detailed Implementation Plan (lines 207–211) and the Verification Plan (lines 249–253), `--mode` is specified only to accept three distinct options: `rag`, `dag`, and `graph`. The `hybrid` mode is never defined in the CLI specification.

### Finding 7: Unexplained "Result Families" and External References
* **Severity**: 🟡 Major
* **Affected Scope**: Lines 11, 28, 57, 121, 145, 215 (`openai/math`, `karpathy-llm-wiki`, and "Result Families")
* **Discrepancy Description & Rationale**: The design repeatedly anchors its architecture to "OpenAI's `openai/math` repository workflow", Andrej Karpathy's LLM Wiki (`Astro-Han/karpathy-llm-wiki`), and organizing catalogs into "Result Families". None of these concepts are accompanied by documentation links or contextual definitions.

### Finding 8: Hardcoded Google Drive Drive-Letter Dependency
* **Severity**: 🟢 Nit
* **Affected Scope**: Lines 41–43, 72, 175 (Google Drive Local Mount Path)
* **Discrepancy Description & Rationale**: The document hardcodes `G:\My Drive\Portfolio Hub\Research Corpus` as an environmental prerequisite. It assumes Google Drive for Desktop is installed and mapped to drive letter `G:`, but does not document this environmental dependency or provide configuration instructions for machines where Google Drive mounts under a different letter.

### Finding 9: Uncredited `MarkItDown` Dependency
* **Severity**: 🟢 Nit
* **Affected Scope**: Lines 101, 183, 188, 228, 242 (`MarkItDown`)
* **Discrepancy Description & Rationale**: `MarkItDown` is introduced as the primary converter for preserving LaTeX formulas and tabular hierarchies, but is never linked or identified as Microsoft's open-source library (`microsoft/markitdown`).

### Finding 10: Unspecified Asset Destination for Snapshots
* **Severity**: 🟢 Nit
* **Affected Scope**: Lines 186–190 (`snapshot_extractor.py`)
* **Discrepancy Description & Rationale**: The script description states that it "renders visual page snapshots of tables and figures". However, the document does not specify where these image assets are written or how they are linked within the generated markdown files.
