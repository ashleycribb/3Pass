# 3Pass Literature Review Architecture

An automated, Drive-aware 3-pass literature workflow built for doctoral research question discovery, theoretical framework mapping, and verifiable evidence synthesis.

Incorporates structural patterns from [`openai/math`](https://github.com/openai/math) (modular Result Families and catalog layout) and Andrej Karpathy's LLM Wiki architecture (`wiki/`), powered by the native zero-copy Rust **S3A Storage Architecture** ([`ashleycribb/s3a-storage`](https://github.com/ashleycribb/s3a-storage)).

---

## Architectural Overview

```
Google Drive Corpus (811 PDFs)
  │  <drive_local_path from config/corpus_config.yaml>
  ▼
[Phase 1 & 2] Ingestion, MarkItDown & Triage Engine
  ├─ scripts/inventory_and_sync.py  (Reconciles SHA-256 hashes against ledger)
  ├─ scripts/triage_wave.py         (Pass 1: lists candidates for YOUR skim; no scoring, writes nothing)
  ├─ scripts/convert_markitdown.py  (MarkItDown + PyMuPDF table & math extraction)
  └─ scripts/run_pass2.py           (Pass 2 deep methodology & gap extraction)
  ▼
[Phase 3] Karpathy LLM Research Wiki (wiki/)
  ├─ wiki/index.md                  (Master navigation portal & corpus statistics)
  ├─ wiki/gaps.md                   (Doctoral literature gap register)
  ├─ wiki/theories.md               (Theoretical framework registry: CLT, SRL, Activity Theory)
  ├─ wiki/topics/*.md               (Thematic portals: EE, LA, TW)
  └─ wiki/papers/SRC-XXXX.md        (Strict provenance dossiers: [Source ID | Drive Path | Pass Level])
  ▼
[Phase 4] S3A Zero-Copy Binary Storage Engine
  ├─ scripts/compile_s3a.py         (Builds NDJSON & executes s3a-cli scholar ingest)
  ├─ ledger/research_corpus.s3a     (Authoritative binary archive with ADDON_EDU capability)
  │    ├─ Tile 10: HumanLrsRecord   (Researcher ratings & audit trails)
  │    ├─ Tile 11: AiTraceRecord    (AI pass reasoning traces)
  │    ├─ Tile 12: AcademicPaper    (811 papers with 3D Hilbert topic mapping)
  │    ├─ Tile 13: ResearchGraph    (Knowledge graph & citation edges)
  │    └─ Tile 14: SrsWorkspace     (Active research question pod)
  └─ scripts/query_engine.py        (Tri-modal query engine: RAG, DAG, Graph, Hybrid)
  ▼
[Front-End Catalog]
  └─ CONTENTS.md                    (Result Families mirroring openai/math)
```

---

## Directory Layout

- **`config/`**: Central configuration files (`corpus_config.yaml`).
- **`ledger/`**: Authoritative metadata and storage files.
  - `research_corpus.s3a`: Native Rust zero-copy Hyper-Tile binary archive.
  - `source_ledger.json`: Machine-readable canonical ledger (811 sources).
  - `source_ledger.md`: Executive overview and triage dashboard.
  - `aliases.json`: Deduplicated file copy and version registry.
- **`wiki/`**: Karpathy-style markdown research wiki with 100% verified internal wikilinks.
  - `index.md`: Navigation portal and prioritized paper index.
  - `gaps.md`: Open research questions and unaddressed empirical contradictions.
  - `theories.md`: Cognitive Load Theory, Human-AI Synergy, Self-Regulated Learning mappings.
  - `topics/`: Thematic sub-portals for Evidence Evaluation, Learner Agency, Traceable Workflows.
  - `papers/`: Per-source dossiers with strict cryptographic provenance headers.
- **`cache/`**: Local build and conversion cache.
  - `cache/markdown/`: Structured markdown conversions and Pass 2 synthesis cards.
  - `cache/s3a_intermediate/`: Pre-compiled NDJSON tiles for S3A ingestion.
- **`scripts/`**: CLI orchestration and query utilities.
- **`tests/`**: Pytest test suite and offline CI test fixtures.
- **`CONTENTS.md`**: Modular Result Families catalog mirroring `openai/math`.

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

### 1. Reconcile Corpus & Ingest Ledger
```powershell
# Reconcile Google Drive corpus against canonical ledger
python scripts/inventory_and_sync.py --reconcile
```

### 2. Pass 1: Your Skim (~15 s per paper)
```powershell
# Lists papers that have no researcher record yet. It does not score, rank,
# include/exclude, or record anything. Skipped papers are never recorded.
python scripts/triage_wave.py --show-text --limit 25
```

### 3. Pass 2: Selected Papers Only
```powershell
# Extraction runs only on papers you select. Selecting a paper is what creates its record.
# Every output is labelled "AI draft — needs researcher judgment".
python scripts/run_pass2.py --id SRC-0002 SRC-0003
```

### 4. Maintain & Lint Karpathy LLM Wiki
```powershell
# Generate and sync all wiki paper dossiers and index portals
python scripts/wiki_manager.py --sync-all

# Lint all internal wikilinks and assert provenance headers
python scripts/wiki_manager.py --lint
```

### 5. Compile Native S3A Binary Archive
```powershell
# Ingest intermediate NDJSON into ledger/research_corpus.s3a via s3a-cli
python scripts/compile_s3a.py

# Inspect S3A Hyper-Tiles, CRC32 checksums, and metadata slots
s3a-cli inspect  # path set by s3a_binary_path in config/corpus_config.yaml ledger/research_corpus.s3a
```

### 6. Execute Multi-Modal S3A Queries
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

### 7. Generate Result Families Catalog
```powershell
python scripts/generate_catalog.py
```

### 8. Run Automated Verification Tests
```powershell
python -m pytest tests/test_pipeline.py -v
```
