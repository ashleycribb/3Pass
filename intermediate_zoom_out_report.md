# Intermediate Zoom Out Review Report (Stage 8)

**Target Document**: [`docs/designs/3pass_research_corpus_architecture.md`](file:///c:/Users/Ashley/Downloads/3Pass/docs/designs/3pass_research_corpus_architecture.md)  
**Reviewer**: `egm-zoom-out` (Skeptical Architect)  
**Standard**: `zoom-out-standard.md`  
**Date**: 2026-11-01  

---

## Executive Summary
Evaluating the architecture following the Comprehension and Critic review stages confirms several fundamental simplifications are required before detailed implementation can proceed:

1. **Rejection of the Dual-State Database (`.s3a`) in Favor of Markdown-First**:
   The central tenet of Karpathy's LLM Wiki pattern is that the human-readable Markdown directory (`wiki/`) serves directly as the structured, queryable knowledge base. Maintaining an ongoing secondary compilation step into a binary container (`.s3a` / SQLite) introduces dual-state synchronization debt without clear necessity for a ~720 paper corpus. Embeddings can be indexed directly over the markdown files on demand.

2. **Rejection of Reinvented Ingestion Scripts**:
   The proposed `scripts/ingest_drive.py` duplicates existing working code in `inventory_and_sync.py` and `triage_wave.py`. Existing scripts should be parameterised via `config/corpus_config.yaml` rather than creating redundant parallel utilities.

3. **Consolidation of Redundant Synthesis Artifacts**:
   Four competing synthesis outputs (`wiki/`, `living_doc/synthesis.md`, `CONTENTS.md`, and `ledger/source_ledger.md`) must be cleanly stratified:
   - `ledger/source_ledger.md`: Administrative triage dashboard.
   - `wiki/`: Exploratory working research environment (topics, papers, gaps).
   - `living_doc/synthesis.md` / `CONTENTS.md`: Curated final research review deliverables.

4. **Deferral of Speculative Graph Subsystems**:
   Building a full-scale citation DAG traversal CLI before citation extraction pipelines (CrossRef / OpenAlex APIs) are implemented violates YAGNI. Metadata enrichment should be added incrementally in Pass 2/3.
