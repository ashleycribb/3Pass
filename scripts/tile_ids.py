"""
Single source of truth for the S3A tile IDs used by 3Pass.

Canonical definitions live in the s3a-storage tile-type registry:
  https://github.com/ashleycribb/s3a-storage/blob/main/crates/s3a-core/src/registry.rs
  https://github.com/ashleycribb/s3a-storage/blob/main/crates/s3a-pack-scholar/src/pack.rs
The registry is Rust-only (no machine-readable export yet), so the values are
mirrored here. Do NOT hardcode tile numbers anywhere else in 3Pass; import from
this module. If s3a-storage changes its layout, update this file (or override
at runtime with a `tile_ids:` mapping in config/corpus_config.yaml).
"""

# Scholar pack tile IDs (s3a-pack-scholar::pack).
DEFAULT_TILE_IDS = {
    "HUMAN_LRS": 10,         # HumanLrsRecord - researcher-authored decisions only
    "AI_TRACE_LOG": 11,      # AiTraceRecord - AI step traces (labelled AI output)
    "ACADEMIC_PAPERS": 12,   # AcademicPaperRecord
    "RESEARCH_GRAPH": 13,    # ResearchGraphEdgeRecord
    "SRS_WORKSPACE": 14,     # SrsWorkspaceRecord
    "SRS_MANIFEST": 15,      # SrsManifestHeader
}


def load_tile_ids(cfg=None):
    """Return the tile-id map, applying an optional `tile_ids` override from config."""
    ids = dict(DEFAULT_TILE_IDS)
    override = (cfg or {}).get("tile_ids") or {}
    for name, value in override.items():
        key = str(name).upper()
        if key not in ids:
            raise ValueError(f"Unknown tile id name in config: {name}")
        ids[key] = int(value)
    return ids


TILE_HUMAN_LRS = DEFAULT_TILE_IDS["HUMAN_LRS"]
TILE_AI_TRACE_LOG = DEFAULT_TILE_IDS["AI_TRACE_LOG"]
TILE_ACADEMIC_PAPERS = DEFAULT_TILE_IDS["ACADEMIC_PAPERS"]
TILE_RESEARCH_GRAPH = DEFAULT_TILE_IDS["RESEARCH_GRAPH"]
TILE_SRS_WORKSPACE = DEFAULT_TILE_IDS["SRS_WORKSPACE"]
TILE_SRS_MANIFEST = DEFAULT_TILE_IDS["SRS_MANIFEST"]

# Label shown on every AI-generated artifact (pass 2 cards, gap candidates, traces).
AI_DRAFT_LABEL = "AI draft \u2014 needs researcher judgment"
