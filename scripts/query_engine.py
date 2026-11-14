"""
Multi-Modal Query Engine for S3A Research Corpus

Executes spatial and graph queries across the native S3A binary archive:
  - Mode 'rag': Spatial topic query using 3D Hilbert topic mapping & semantic keyword scoring
  - Mode 'dag': Provenance and citation lineage for a specific Source ID using native S3A graph queries
  - Mode 'graph': Knowledge graph edges and theoretical framework associations using s3a-cli graph
  - Mode 'hybrid': Fused spatial topic + native S3A graph traversal

Graph and DAG queries are delegated directly to `s3a-cli graph ... --json` against the compiled .s3a,
keeping the ledger only as the SHA-256 intake record.
"""

import os
import sys
import json
import re
import argparse
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
    from scripts.compile_s3a import hash_string_u64
    from scripts.tile_ids import TILE_ACADEMIC_PAPERS, TILE_RESEARCH_GRAPH
except ImportError:  # run directly as `python scripts/<name>.py`
    from corpus_common import DEFAULT_CONFIG_PATH, load_config
    from compile_s3a import hash_string_u64
    from tile_ids import TILE_ACADEMIC_PAPERS, TILE_RESEARCH_GRAPH


def run_s3a_graph_cmd(cfg: Dict[str, Any], subcommand: str, *args: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Invokes `s3a-cli graph <subcommand> <archive_path> ... --json`."""
    s3a_bin = cfg.get("s3a_binary_path", "s3a-cli")
    archive_path = cfg.get("archive_path", "ledger/research_corpus.s3a")

    if not os.path.exists(archive_path):
        return None, f"S3A archive not found at '{archive_path}'. Run scripts/compile_s3a.py first."

    cmd = [s3a_bin, "graph", subcommand, archive_path, *args, "--json"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip() or f"Exited with code {res.returncode}"
            return None, err
        return json.loads(res.stdout), None
    except FileNotFoundError:
        return None, f"s3a-cli executable not found at '{s3a_bin}'"
    except json.JSONDecodeError as e:
        return None, f"Failed to parse s3a-cli JSON output: {e}\nRaw output: {res.stdout[:200]}"
    except Exception as e:
        return None, str(e)


def get_ledger_intake_map(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Loads source_ledger.json purely as the SHA-256 intake and deduplication record."""
    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    if not os.path.exists(ledger_path):
        return {}
    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)
    intake_map = {}
    for idx, s in enumerate(ledger.get("sources", [])):
        sid = s.get("source_id", "")
        intake_map[sid.upper()] = {
            "record_index": idx,
            "source_id": sid,
            "sha256": s.get("sha256", ""),
            "title": s.get("title", ""),
            "author": s.get("author", "Unknown"),
            "year": s.get("year", "Unknown"),
            "drive_path": s.get("drive_path", ""),
            "drive_file_id": s.get("drive_file_id", ""),
            "provenance": s.get("provenance", ""),
            "aliases": s.get("aliases", []),
            "pass_status": s.get("pass_status", ""),
            "abstract_excerpt": s.get("abstract_excerpt", ""),
            "researcher_scores": s.get("researcher_scores", {}),
        }
    return intake_map


def source_id_to_hex(source_id: str) -> str:
    """Converts a source ID string (e.g. 'SRC-9001' or '9001') to 64-bit hex node string."""
    m = re.search(r"\d+", source_id)
    val = int(m.group(0)) if m else 0
    return f"0x{val:016x}"


def execute_dag_query(cfg: Dict[str, Any], source_id: str):
    """Executes S3A graph queries (neighbors and traverse) against compiled .s3a, with ledger SHA-256 intake."""
    node_hex = source_id_to_hex(source_id)
    archive_path = cfg.get("archive_path", "ledger/research_corpus.s3a")

    print("=" * 70)
    print(f"S3A Native Graph & Provenance DAG Trace: `{source_id}` ({node_hex})")
    print(f"Archive: {archive_path}")
    print("=" * 70)

    # 1. Look up SHA-256 intake record from source_ledger.json
    intake_map = get_ledger_intake_map(cfg)
    target = intake_map.get(source_id.upper())

    if target:
        coord = f"L0:T{TILE_ACADEMIC_PAPERS}:R{target['record_index']}"
        print(f"\nCanonical Intake Node: `{source_id}`")
        print(f"  Title:            {target.get('title')}")
        print(f"  Physical Drive:   {target.get('drive_path')}")
        print(f"  Google Drive URI: {target.get('drive_file_id')}")
        print(f"  S3A Coordinate:   {coord}")
        print(f"  Pass Status:      {target.get('pass_status')}")
        print(f"  SHA-256 (Intake): {target.get('sha256')}")
        print(f"  Provenance:       [{target.get('provenance')}]")

        aliases = target.get("aliases", [])
        if aliases:
            print(f"\nDeduplicated Aliases ({len(aliases)}):")
            for a in aliases:
                print(f"  ├─ {a.get('alias_filename')} ({a.get('alias_source_dir')})")
    else:
        print(f"\nNode `{source_id}` not found in SHA-256 intake ledger (querying S3A binary directly).")

    # 2. Query S3A graph neighbors from native binary archive
    neighbors_data, err = run_s3a_graph_cmd(cfg, "neighbors", node_hex)
    if err:
        print(f"\n[S3A Graph Neighbors Error]: {err}")
    elif neighbors_data:
        neighbors = neighbors_data.get("neighbors", [])
        elapsed = neighbors_data.get("elapsed_us", 0)
        print(f"\nS3A Tile {TILE_RESEARCH_GRAPH} Graph Neighbors ({len(neighbors)} edges, {elapsed}µs lookup):")
        for edge in neighbors:
            edge_type = edge.get("edge_type", "related_to")
            to_node = edge.get("to", "")
            props = edge.get("properties", {})
            print(f"  ├─ [{edge_type}] -> {to_node} (predicate={props.get('predicate_id')}, weight={props.get('weight')}, hilbert={props.get('hilbert')})")

    # 3. Query S3A graph traverse from native binary archive
    traverse_data, err = run_s3a_graph_cmd(cfg, "traverse", node_hex, "--depth", "2")
    if err:
        print(f"\n[S3A Graph Traversal Error]: {err}")
    elif traverse_data:
        visited = traverse_data.get("visited_nodes", [])
        edges = traverse_data.get("edges", [])
        elapsed = traverse_data.get("elapsed_us", 0)
        print(f"\nS3A Graph Traversal (depth=2, {len(visited)} visited nodes, {len(edges)} paths, {elapsed}µs):")
        print(f"  Visited Nodes: {', '.join(visited)}")
        for edge in edges:
            print(f"  Path: {edge.get('from')} --({edge.get('edge_type')})--> {edge.get('to')}")

    print("\n" + "=" * 70)


def execute_graph_query(cfg: Dict[str, Any], theory_or_topic: str):
    """Executes S3A graph query against compiled .s3a to find papers linked to a theoretical framework or topic."""
    archive_path = cfg.get("archive_path", "ledger/research_corpus.s3a")

    # Try both normalized and raw theory hashes
    variants = [theory_or_topic, theory_or_topic.replace(" ", "_")]
    hex_keys = [f"0x{hash_string_u64(v):016x}" for v in variants]

    print("=" * 70)
    print(f"S3A Knowledge Graph Subgraph: '{theory_or_topic}'")
    print(f"Archive: {archive_path} (Tile {TILE_RESEARCH_GRAPH}: RESEARCH_GRAPH)")
    print("=" * 70)

    intake_map = get_ledger_intake_map(cfg)
    # Build hex to intake metadata lookup
    hex_to_intake = {}
    for sid, s in intake_map.items():
        h = source_id_to_hex(sid)
        hex_to_intake[h.lower()] = s

    found_edges = []
    for h in set(hex_keys):
        res, err = run_s3a_graph_cmd(cfg, "neighbors", h)
        if res and res.get("neighbors"):
            found_edges.extend(res["neighbors"])

    if not found_edges:
        print(f"\nNo S3A graph edges found for node hashes: {hex_keys}")
        print("Note: Run scripts/compile_s3a.py to update Tile 13 edges from the corpus.")
    else:
        print(f"\nFound {len(found_edges)} associated S3A graph edge(s):")
        for edge in found_edges:
            other = edge.get("to") if edge.get("to") not in hex_keys else edge.get("from")
            other_lower = other.lower() if other else ""
            paper_info = hex_to_intake.get(other_lower)
            if paper_info:
                sid = paper_info["source_id"]
                title = paper_info["title"]
                sha256 = paper_info["sha256"][:12]
                print(f"  ├─ [`{sid}` | {other}] {title[:60]}... (SHA-256: {sha256}..., edge: {edge.get('edge_type')})")
            else:
                print(f"  ├─ Node: {other} | Relation: {edge.get('edge_type')} | Props: {edge.get('properties')}")

    print("\n" + "=" * 70)


def execute_rag_query(cfg: Dict[str, Any], query_str: str, limit: int = 5):
    """Executes spatial keyword/topic scoring over the intake ledger."""
    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    if not os.path.exists(ledger_path):
        print(f"Ledger file not found at {ledger_path}")
        return []

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    terms = query_str.lower().split()
    scored = []

    for idx, s in enumerate(ledger.get("sources", [])):
        text = (s.get("title", "") + " " + s.get("abstract_excerpt", "") + " " + " ".join(s.get("theoretical_frameworks", []))).lower()
        match_score = sum(text.count(t) for t in terms)

        # Human researcher manual selection flag gives ranking prominence
        if s.get("researcher_selection"):
            match_score += 3

        if match_score > 0:
            scored.append((match_score, idx, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = scored[:limit]

    print("=" * 70)
    print(f"S3A Spatial RAG Query: '{query_str}'")
    print(f"Archive: {cfg.get('archive_path')} (Tile {TILE_ACADEMIC_PAPERS}: ACADEMIC_PAPERS)")
    print("=" * 70)

    for rank, (score, tile_rec_idx, s) in enumerate(results, 1):
        sid = s.get("source_id", "UNKNOWN")
        coord = f"L0:T{TILE_ACADEMIC_PAPERS}:R{tile_rec_idx}"
        prov = s.get("provenance", f"{sid} | {s.get('drive_path')} | {s.get('pass_status')}")
        scores = s.get("researcher_scores") or {}
        theories = ", ".join(s.get("theoretical_frameworks", [])) or "None indexed"

        print(f"\n[{rank}] Source ID: `{sid}` | S3A Address: `{coord}` | Score: {score}")
        print(f"    Title:       {s.get('title')}")
        print(f"    Authors:     {s.get('author', 'Unknown')} ({s.get('year', 'Unknown')})")
        print(f"    SHA-256:     {s.get('sha256', '-')}")
        print(f"    Provenance:  [{prov}]")
        print(f"    Researcher scores: EE: {scores.get('evidence_evaluation', '-')} | LA: {scores.get('learner_agency', '-')} | TW: {scores.get('traceable_workflows', '-')}")
        print(f"    Theories:    {theories}")
        if s.get("abstract_excerpt"):
            print(f"    Excerpt:     {s['abstract_excerpt'][:220]}...")

    print("\n" + "=" * 70)
    return results


def execute_hybrid_query(cfg: Dict[str, Any], query_str: str, limit: int = 5):
    """Executes spatial topic RAG query followed by native S3A graph traversal on the top result."""
    print("=" * 70)
    print(f"S3A Hybrid Query (Spatial RAG + Native S3A Graph Traversal): '{query_str}'")
    print("=" * 70)
    rag_res = execute_rag_query(cfg, query_str, limit=limit)
    if rag_res:
        top_sid = rag_res[0][2]["source_id"]
        print(f"\nS3A Native Graph Traversal on Primary Node: {top_sid}")
        execute_dag_query(cfg, top_sid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="S3A Research Corpus Multi-Modal Query Engine")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--mode", choices=["rag", "dag", "graph", "hybrid"], default="hybrid", help="Query mode")
    parser.add_argument("--query", help="Query string for RAG or Hybrid search")
    parser.add_argument("--id", help="Source ID for DAG query (e.g. SRC-0002)")
    parser.add_argument("--theory", help="Theory name for Graph query")
    parser.add_argument("--limit", type=int, default=5, help="Number of results to return")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.mode == "rag":
        execute_rag_query(cfg, args.query or "learner agency", limit=args.limit)
    elif args.mode == "dag":
        execute_dag_query(cfg, args.id or "SRC-9001")
    elif args.mode == "graph":
        execute_graph_query(cfg, args.theory or "Learner Agency")
    elif args.mode == "hybrid":
        execute_hybrid_query(cfg, args.query or "scaffolding agency", limit=args.limit)
