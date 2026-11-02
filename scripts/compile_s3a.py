"""
S3A Corpus Compiler & Ingestion Engine

Converts canonical source ledger, wiki graphs, and triage traces into NDJSON intermediate files
and invokes the native Rust S3A storage engine (`s3a-cli scholar ingest`) to construct the
authoritative zero-copy binary archive (`ledger/research_corpus.s3a`).
"""

import os
import sys
import json
import re
import zlib
import argparse
import shutil
import subprocess
from datetime import datetime
import yaml

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
    from scripts.tile_ids import load_tile_ids
except ImportError:  # run directly as `python scripts/<name>.py`
    from corpus_common import DEFAULT_CONFIG_PATH, load_config
    from tile_ids import load_tile_ids


def hilbert_3d_index(x, y, z, order=8):
    """
    Computes a 3D Hilbert curve index for coordinates normalized to 0..255.
    Provides spatial locality clustering for tri-modal topic querying.
    """
    ix = min(255, max(0, int(x * 255)))
    iy = min(255, max(0, int(y * 255)))
    iz = min(255, max(0, int(z * 255)))
    # Interleave bits as simple morton/hilbert locality approximation
    h = 0
    for i in range(8):
        bx = (ix >> i) & 1
        by = (iy >> i) & 1
        bz = (iz >> i) & 1
        h |= (bx << (3 * i)) | (by << (3 * i + 1)) | (bz << (3 * i + 2))
    return h

def hash_string_u64(s):
    """
    Deterministic 64-bit integer hash from string using CRC32 + adler32.
    """
    if not s:
        return 0
    b = s.encode("utf-8")
    crc = zlib.crc32(b) & 0xFFFFFFFF
    adler = zlib.adler32(b) & 0xFFFFFFFF
    return (crc << 32) | adler

def generate_intermediate_files(cfg):
    ledger_dir = cfg.get("ledger_dir", "ledger")
    inter_dir = cfg.get("intermediate_dir", "cache/s3a_intermediate")
    ledger_path = os.path.join(ledger_dir, "source_ledger.json")

    os.makedirs(inter_dir, exist_ok=True)
    tile_ids = load_tile_ids(cfg)  # canonical IDs: see scripts/tile_ids.py

    if not os.path.exists(ledger_path):
        print(f"Error: Ledger file not found at {ledger_path}")
        sys.exit(1)

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    sources = ledger.get("sources", [])
    print(f"Generating S3A intermediate NDJSON files for {len(sources)} sources...", flush=True)

    # 1. workspace.json
    workspace_data = {
        "project_high": hash_string_u64("Doctoral Research Review"),
        "project_low": hash_string_u64("Evidence Evaluation, Learner Agency, Traceable Workflows"),
        "question_hash": hash_string_u64("How to balance AI scaffolding with human student learner agency?"),
        "collection_mask": 1,
        "centroid_x": 0.5,
        "centroid_y": 0.5,
        "centroid_z": 0.5,
        "status": 1
    }
    with open(os.path.join(inter_dir, "workspace.json"), "w", encoding="utf-8") as f:
        json.dump(workspace_data, f, indent=2)

    # 2. academic_papers.jsonl
    papers_file = open(os.path.join(inter_dir, "academic_papers.jsonl"), "w", encoding="utf-8")
    edges_file = open(os.path.join(inter_dir, "graph_edges.jsonl"), "w", encoding="utf-8")
    human_file = open(os.path.join(inter_dir, "human_lrs.jsonl"), "w", encoding="utf-8")
    ai_file = open(os.path.join(inter_dir, "ai_traces.jsonl"), "w", encoding="utf-8")

    theory_hashes = {
        "Cognitive Load Theory": hash_string_u64("Cognitive Load Theory"),
        "Human-AI Synergy": hash_string_u64("Human-AI Synergy"),
        "Activity Theory": hash_string_u64("Activity Theory"),
        "Self-Regulated Learning": hash_string_u64("Self-Regulated Learning"),
        "Scaffolding Theory": hash_string_u64("Scaffolding Theory")
    }

    base_time = int(datetime(2026, 1, 1).timestamp())

    for idx, s in enumerate(sources, 1):
        sid_match = re.search(r"SRC-(\d+)", s["source_id"])
        pid = int(sid_match.group(1)) if sid_match else idx

        # Topic coordinates come only from researcher-entered scores. Legacy
        # keyword scores (`relevance_scores`) were AI output and are ignored.
        scores = s.get("researcher_scores") or {}
        ee = scores.get("evidence_evaluation") or 1
        la = scores.get("learner_agency") or 1
        tw = scores.get("traceable_workflows") or 1

        tx = (ee - 1) / 4.0
        ty = (la - 1) / 4.0
        tz = (tw - 1) / 4.0
        h_idx = hilbert_3d_index(tx, ty, tz)

        # Parse year safely
        raw_year = s.get("year")
        year = 2024
        if raw_year and str(raw_year).isdigit():
            year = int(raw_year)

        doi_hash = hash_string_u64(s.get("title", ""))

        paper_rec = {
            "paper_id": pid,
            "timestamp_sec": base_time + idx * 3600,
            "hilbert_index": h_idx,
            "topic_x": round(tx, 4),
            "topic_y": round(ty, 4),
            "topic_z": round(tz, 4),
            "citation_count": s.get("page_count", 10),
            "year": year,
            "venue_id": 1,
            "open_access_flag": 1,
            "warning_count": 0,
            "doi_prefix_hash": doi_hash
        }
        papers_file.write(json.dumps(paper_rec) + "\n")

        # Edges (topic/theory connections)
        theories = s.get("theoretical_frameworks", [])
        for t in theories:
            t_hash = theory_hashes.get(t, hash_string_u64(t))
            edge = {
                "subject_hash": pid,
                "object_hash": t_hash,
                "timestamp_sec": base_time + idx * 3600,
                "hilbert_coord": h_idx,
                "predicate_id": 2,  # 2: APPLIES_THEORY
                "weight": 1.0
            }
            edges_file.write(json.dumps(edge) + "\n")

        # Human LRS record: written only when the researcher selected this paper.
        # Papers that were skimmed and skipped get no record.
        if s.get("researcher_selection"):
            human_rec = {
                "session_high": 1001,
                "session_low": 2002,
                "timestamp_sec": base_time + idx * 3600,
                "actor_hash": hash_string_u64("Doctoral_Researcher"),
                "verb_id": 1,  # 1: selected for deeper pass
                "decision_score": float(s.get("researcher_rating") or 0.0),
                "object_hash": pid,
                "ai_tile": tile_ids["AI_TRACE_LOG"],
                "ai_record": idx
            }
            human_file.write(json.dumps(human_rec) + "\n")

        # AI trace record: only for papers where the AI produced a Pass 2 draft.
        # Token/cost fields are 0 because they are not measured here.
        if s.get("pass2_ai_draft"):
            ai_rec = {
                "session_high": 1001,
                "session_low": 2002,
                "timestamp_sec": base_time + idx * 3600,
                "agent_id": 1,
                "step_index": 2,
                "action_type": 1,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_micro_usd": 0,
                "thought_hash": hash_string_u64((s.get("abstract_excerpt") or "")[:100]),
                "tool_call_hash": hash_string_u64(s.get("provenance", ""))
            }
            ai_file.write(json.dumps(ai_rec) + "\n")

    papers_file.close()
    edges_file.close()
    human_file.close()
    ai_file.close()

    print(f"Generated intermediate NDJSON files in `{inter_dir}`.", flush=True)

def compile_s3a_archive(cfg, fresh=True):
    generate_intermediate_files(cfg)

    s3a_bin = cfg.get("s3a_binary_path")  # from local config / S3A_CLI env
    inter_dir = cfg.get("intermediate_dir", "cache/s3a_intermediate")
    archive_path = cfg.get("archive_path", "ledger/research_corpus.s3a")

    if not (shutil.which(s3a_bin) or os.path.exists(s3a_bin)):
        print(f"Error: s3a-cli binary not found at {s3a_bin}")
        sys.exit(1)

    os.makedirs(os.path.dirname(archive_path), exist_ok=True)

    print("=" * 60, flush=True)
    print("Invoking Native Rust S3A Storage Engine (scholar ingest)", flush=True)
    print(f"Binary:       {s3a_bin}", flush=True)
    print(f"Intermediate: {inter_dir}", flush=True)
    print(f"Output:       {archive_path}", flush=True)
    print("=" * 60, flush=True)

    cmd = [s3a_bin, "scholar", "ingest", inter_dir, archive_path]
    if fresh:
        cmd.append("--fresh")

    res = subprocess.run(cmd, capture_output=True, text=True)
    print(res.stdout, flush=True)
    if res.stderr:
        print(res.stderr, flush=True)

    if res.returncode != 0:
        print(f"Error executing s3a-cli scholar ingest (code {res.returncode})", flush=True)
        sys.exit(res.returncode)

    print("\n" + "=" * 60, flush=True)
    print("Inspecting S3A Archive Layout & Headers", flush=True)
    print("=" * 60, flush=True)

    insp_cmd = [s3a_bin, "inspect", archive_path]
    insp_res = subprocess.run(insp_cmd, capture_output=True, text=True)
    print(insp_res.stdout, flush=True)
    if insp_res.stderr:
        print(insp_res.stderr, flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile Corpus into Native S3A Archive")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--append", action="store_true", help="Append without --fresh")
    args = parser.parse_args()

    cfg = load_config(args.config)
    compile_s3a_archive(cfg, fresh=not args.append)
