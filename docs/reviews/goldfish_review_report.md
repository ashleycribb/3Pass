# Goldfish Annotated Review Report

**Status**: resolved  
**Target**: [`docs/designs/3pass_research_corpus_architecture.md`](file:///c:/Users/Ashley/Downloads/3Pass/docs/designs/3pass_research_corpus_architecture.md)  
**Date**: 2026-11-01  
**Resolution Summary**:
- **Accepted & Applied Fixes**: 10 (Blockers B1–B4, Majors M1–M4, M10, Nits N1–N6)
- **Contested Items**: 0
- **Escalated Items**: 0
- **Declined Items**: 0
- **Remaining Unaddressed Issues**: 0

---

## Applied Remediations Overview

1. **Native S3A Storage Engine Integrated**: Replaced all legacy references to DuckDB and SQLite with the native S3A Storage Architecture ([`ashleycribb/s3a-storage`](https://github.com/ashleycribb/s3a-storage)), specifying Scholar Pack Hyper-Tiles (Tiles 10–18), zero-copy memory-mapped access, 3D Hilbert topic mapping, and `s3a-cli` integrations.
2. **Dedicated Risks & Mitigations Added**: Section inserted addressing cloud sync file-locking collisions, PDF extraction distortion, API rate limits, and path reconciliation.
3. **Ghost Script Reconciled**: Added `scripts/run_pass2.py` to the Detailed Implementation Plan to orchestrate Pass 2 topic extraction.
4. **Codebase Reuse Prioritized**: Reused and parameterised existing scripts (`inventory_and_sync.py`, `triage_wave.py`, `convert_markitdown.py`) instead of creating redundant parallel files.
5. **Implementation Phased**: Structured implementation into 5 sequential phases with checkpoint tests and synthetic test fixtures.
6. **Path Drift Handled**: Phase 1 includes automated reconciliation comparing SHA-256 hashes against `ledger/source_ledger.json` to map `Portfolio Hub\Research Corpus` to existing `SRC-XXXX` identifiers.
