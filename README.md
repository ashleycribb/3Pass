# 3Pass Literature Review Architecture

A bookkeeping-only literature workflow and research wiki designed for human doctoral synthesis, evidence evaluation, and dissertation question discovery.

**Core Philosophy: Bookkeeping Only, Humans Digest.**  
Automated judgments, automated quality ratings, and candidate gap guessing are disabled. The system extracts structured facts (metadata, sections, figures, tables, formulas, references, and provenance) into a unified record format, generating draft dossiers with clearly marked empty templates for the human researcher's critical notes and appraisal.

---

## Architecture & Source Router

```
Researcher Selection (via Scholar Explorer, S3A, or DOI list)
  │
  ▼
Source Router (scripts/source_router.py)
  ├─ Path a: ArxivHtmlPlugin      (arXiv HTML at arxiv.org/html/<id> -> parses LaTeXML ltx_* classes)
  ├─ Path b: LatexSourcePlugin    (arXiv/LaTeX source -> compiles/parses via LaTeXML)
  ├─ Path xml: PublisherXmlPlugin (publisher JATS XML -> full structure, 'jats-verified')
  ├─ Path c+: VerifiedPdfPlugin   (PDF -> Docling text/tables/figures, every block checked against the
  │                                PDF text layer; 'pdf-verified' or 'held, review'; needs Docling)
  ├─ Path c: GrobidPdfPlugin      (PDF only -> extracts via GROBID TEI XML, marks 'pdf-extracted, verify')
  └─ Path d: CrossrefOpenAlexPlugin (DOI/metadata only -> polite pool with mailto, backoff, disk cache)
  │
  ▼
Unified Record Schema (scripts/models.py)
  ├─ Metadata, URLs, abstract, sections, figures, tables, equations, references
  ├─ Provenance: path_used, tool, tool_version, per-part verification
  └─ Completeness level: full | pdf-verified | pdf-extracted | metadata-only
  │
  ├─────────────────────────────────────────────────┐
  ▼                                                 ▼
Karpathy LLM Research Wiki (wiki/)               S3A Zero-Copy Storage Engine
  ├─ wiki/index.md  (Master catalog)               ├─ scripts/compile_s3a.py (scholar ingest)
  ├─ wiki/gaps.md   (Human gap register)           ├─ ledger/research_corpus.s3a (ADDON_EDU)
  ├─ wiki/theories.md (Theoretical frameworks)     │    ├─ Tile 10: HumanLrsRecord
  ├─ wiki/log.md    (Append-only intake log)       │    ├─ Tile 11: AiTraceRecord
  ├─ wiki/topics/*.md (Thematic portals)           │    ├─ Tile 12: AcademicPaperRecord
  └─ wiki/papers/SRC-XXXX.md                       │    ├─ Tile 13: ResearchGraphEdgeRecord
       ├─ Facts & structure only                   │    └─ Tile 14: SrsWorkspaceRecord
       └─ Empty researcher appraisal templates     └─ scripts/query_engine.py (RAG/DAG/Graph)
```

---

## Directory Layout

- **`config/`**: Central pipeline configuration (`corpus_config.yaml`). Copy from `corpus_config.example.yaml`.
- **`ledger/`**: Authoritative metadata and storage files (gitignored).
  - `research_corpus.s3a`: Native Rust zero-copy Hyper-Tile binary archive.
  - `source_ledger.json`: Machine-readable canonical ledger.
  - `source_ledger.md`: Overview and catalog dashboard.
  - `aliases.json`: Deduplicated file copy and version registry.
- **`wiki/`**: Karpathy-style markdown research wiki with 100% verified internal wikilinks.
  - `index.md`: Navigation portal and completeness catalog.
  - `gaps.md`: Open research questions and unaddressed empirical contradictions.
  - `theories.md`: Cognitive Load Theory, Human-AI Synergy, Self-Regulated Learning mappings.
  - `log.md`: Append-only event history of paper routing and updates.
  - `topics/`: Thematic sub-portals for Evidence Evaluation, Learner Agency, Traceable Workflows.
  - `papers/`: Per-source draft dossiers with empty researcher notes templates (gitignored).
- **`cache/`**: Local extraction and metadata cache.
  - `cache/html/`: Cached arXiv LaTeXML HTML documents.
  - `cache/tei/`: Cached GROBID TEI XML documents.
  - `cache/metadata/`: Polite-pool cached Crossref/OpenAlex JSON metadata.
  - `cache/s3a_intermediate/`: Pre-compiled NDJSON tiles for S3A ingestion.
- **`scripts/`**: CLI orchestration and query utilities.
  - `source_router.py`: Extensible multi-path source router.
  - `models.py`: Unified academic paper record schema.
  - `run_pass2.py`: Intake and bookkeeping CLI.
  - `wiki_manager.py`: Wiki synchronization and link linter.
  - `compile_s3a.py`: S3A binary compiler and round-trip verification.
  - `query_engine.py`: Tri-modal S3A query engine.
  - `tile_ids.py`: Central mirror of s3a-storage scholar tile IDs (Tiles 10–15).
  - `corpus_common.py`: Shared configuration loader.
  - `generate_catalog.py`: Result Families catalog generator.
- **`tests/`**: Pytest test suite with offline fixtures for all 4 paths.
- **`CONTENTS.md`**: Result Families catalog mirroring `openai/math`.
- **`LOG.md`**: Root append-only event log.

---

## Setup

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1   # or: source .venv/bin/activate
pip install -r requirements.txt
copy config\corpus_config.example.yaml config\corpus_config.yaml   # then edit for this machine
```

`config/corpus_config.yaml`, `ledger/`, `wiki/papers/` and `*.s3a` are local data and are gitignored; never commit them.
Tile IDs come from one module, `scripts/tile_ids.py`, mirroring the
[s3a-storage tile-type registry](https://github.com/ashleycribb/s3a-storage/blob/main/crates/s3a-pack-scholar/src/pack.rs).
Review reports from earlier design passes live in `docs/reviews/`.

## Research Judgment Rules

- Pass 1 is the researcher's own skim. The AI only checks whether a record exists.
- A paper gets a researcher record only when the researcher selects it for Pass 2/3.
- AI-extracted text (Pass 2 cards, candidate gaps, theory keyword matches) is an AI draft and needs researcher judgment.

## CLI Execution Guide

### 1. Paper Intake & Bookkeeping (Pass 2)
```powershell
# Ingest specific paper through best available path
python scripts/run_pass2.py --id SRC-0002

# Ingest by DOI
python scripts/run_pass2.py --doi 10.1145/3491101.3503719

# Batch intake papers
python scripts/run_pass2.py --limit 10
```

### 2. Lint Karpathy LLM Wiki
```powershell
# Validate all internal wikilinks and assert provenance compliance (exits 0)
python scripts/wiki_manager.py --lint
```

### 3. S3A Binary Export & Round-Trip Verification
```powershell
# Ingest intermediate NDJSON into ledger/research_corpus.s3a
python scripts/compile_s3a.py

# Run automated round-trip verification
python scripts/compile_s3a.py --round-trip
```

### 4. Execute Multi-Modal S3A Queries
```powershell
# Mode 1: Spatial Topic RAG Search
python scripts/query_engine.py --mode rag --query "human AI synergy IRT"

# Mode 2: Provenance & Citation Lineage DAG
python scripts/query_engine.py --mode dag --id SRC-0002

# Mode 3: Knowledge Graph Subgraph
python scripts/query_engine.py --mode graph --theory "Learner Agency"

# Mode 4: Fused Hybrid Search (RAG + DAG)
python scripts/query_engine.py --mode hybrid --query "scaffolding agency"
```

### 5. Verified PDF Path (text, tables and figures to Markdown + HTML)
The router uses this path for any local PDF when Docling is installed; otherwise it falls through to GROBID.
Each output block is checked against the PDF's own text layer (pypdfium2), independently of the converter.
Blocks that fail are held, not published. See `docs/verified_pdf/AGENT_CONTRACT.md` for what an agent may rely on.
```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-verified-pdf.txt
# Convert + verify; writes <out>/<name>/{<name>.md,.html,images/,tables/,review/,verified/}
python scripts/verified_pdf/agent_tool.py ingest paper.pdf --out cache/verified_pdf
# Verified blocks only (held blocks show status + reason, never content)
python scripts/verified_pdf/agent_tool.py blocks cache/verified_pdf/paper
# Check a quote against the PDF text layer (exit 0 found, 3 not found)
python scripts/verified_pdf/agent_tool.py check-quote paper.pdf "exact words to check"
# Researcher review: open review/review.html, record your decisions in <dir>/decisions.json (with reviewer name), then rebuild
python scripts/verified_pdf/agent_tool.py rebuild paper.pdf cache/verified_pdf/paper
# MCP server for agents (tools: ingest_pdf, get_verified_blocks, check_quote_in_pdf)
python scripts/verified_pdf/agent_tool.py mcp
```

Parallel mode (same outputs, same checks): add `--parallel` to `ingest`, pass `"parallel": True`
to the router input, or run `scripts/verified_pdf/parallel.py` directly. The default `--mode shards`
splits the PDF into page ranges, one per CPU, each converted by the unchanged serial pipeline; the
check groups (text, tables, figures, document) then run in parallel, and their union is exactly the
serial verification. `--mode anchors` runs a layout-only pass first, then separate table, figure and
text workers. Both write `anchors.json` (every block with page, box and reading-order position).
Measured on a 2-CPU machine with identical verified blocks and flags in every case: a 29-page paper
with 5 tables took 47.6 s sharded vs 54.9 s serial; a 13-page text-only paper and the fixture showed no
gain; `--mode anchors` was slower (72 s) because its table worker repeats layout on table pages.

### 6. Run Automated Test Suite
```powershell
python -m pytest tests/ -v
```
