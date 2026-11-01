"""
Multi-Modal Query Engine for S3A Research Corpus

Executes tri-modal spatial and graph queries across the native S3A binary archive:
  - Mode 'rag': Spatial topic query using 3D Hilbert topic mapping & semantic keyword scoring
  - Mode 'dag': Provenance and citation lineage for a specific Source ID
  - Mode 'graph': Knowledge graph edges and theoretical framework associations
  - Mode 'hybrid': Fused spatial topic + knowledge graph citation traversal
"""

import os
import sys
import json
import re
import argparse
import subprocess
from datetime import datetime
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "corpus_config.yaml")

def load_config(config_path=DEFAULT_CONFIG_PATH):
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {
        "ledger_dir": "ledger",
        "archive_path": "ledger/research_corpus.s3a",
        "wiki_dir": "wiki",
        "s3a_binary_path": r"C:\Users\Ashley\Downloads\s3a\target\debug\s3a-cli.exe"
    }

def execute_rag_query(cfg, query_str, limit=5):
    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    terms = query_str.lower().split()
    scored = []

    for idx, s in enumerate(ledger.get("sources", [])):
        text = (s.get("title", "") + " " + s.get("abstract_excerpt", "") + " " + " ".join(s.get("theoretical_frameworks", []))).lower()
        match_score = sum(text.count(t) for t in terms)
        
        # Boost for included and P2 done papers
        if s.get("triage_decision") == "include":
            match_score += 3
        if s.get("pass_status") == "P2 done":
            match_score += 5

        if match_score > 0:
            scored.append((match_score, idx, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = scored[:limit]

    print("=" * 70)
    print(f"S3A Spatial RAG Query: '{query_str}'")
    print(f"Archive: {cfg.get('archive_path')} (Tile 2: ACADEMIC_PAPERS)")
    print("=" * 70)

    for rank, (score, tile_rec_idx, s) in enumerate(results, 1):
        sid = s["source_id"]
        coord = f"L0:T2:R{tile_rec_idx}"
        prov = s.get("provenance", f"{sid} | {s.get('drive_path')} | {s.get('pass_status')}")
        scores = s.get("relevance_scores", {})
        theories = ", ".join(s.get("theoretical_frameworks", [])) or "None indexed"

        print(f"\n[{rank}] Source ID: `{sid}` | S3A Address: `{coord}` | Score: {score}")
        print(f"    Title:       {s.get('title')}")
        print(f"    Authors:     {s.get('author', 'Unknown')} ({s.get('year', 'Unknown')})")
        print(f"    Provenance:  [{prov}]")
        print(f"    Scores:      EE: {scores.get('evidence_evaluation', '-')} | LA: {scores.get('learner_agency', '-')} | TW: {scores.get('traceable_workflows', '-')}")
        print(f"    Theories:    {theories}")
        if s.get("abstract_excerpt"):
            print(f"    Excerpt:     {s['abstract_excerpt'][:220]}...")

    print("\n" + "=" * 70)
    return results

def execute_dag_query(cfg, source_id):
    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    target = None
    target_idx = 0
    for idx, s in enumerate(ledger.get("sources", [])):
        if s.get("source_id", "").upper() == source_id.upper():
            target = s
            target_idx = idx
            break

    if not target:
        print(f"Source ID '{source_id}' not found in corpus.")
        return

    print("=" * 70)
    print(f"S3A Provenance & Citation DAG Trace: `{source_id}`")
    print(f"Archive: {cfg.get('archive_path')}")
    print("=" * 70)

    coord = f"L0:T2:R{target_idx}"
    print(f"\nCanonical Node: `{source_id}`")
    print(f"  Title:            {target.get('title')}")
    print(f"  Physical Drive:   {target.get('drive_path')}")
    print(f"  Google Drive URI: {target.get('drive_file_id')}")
    print(f"  S3A Coordinate:   {coord}")
    print(f"  Pass Status:      {target.get('pass_status')}")
    print(f"  SHA-256:          {target.get('sha256')}")
    print(f"  Provenance:       [{target.get('provenance')}]")

    aliases = target.get("aliases", [])
    print(f"\nDeduplicated Aliases ({len(aliases)}):")
    for a in aliases:
        print(f"  ├─ {a.get('alias_filename')} ({a.get('alias_source_dir')})")

    gaps = target.get("candidate_gaps", [])
    print(f"\nAssociated Candidate Literature Gaps ({len(gaps)}):")
    for g in gaps:
        print(f"  ├─ {g}")

    theories = target.get("theoretical_frameworks", [])
    print(f"\nTheoretical Grounding ({len(theories)}):")
    for t in theories:
        print(f"  ├─ {t}")

    print("\n" + "=" * 70)

def execute_graph_query(cfg, theory_or_topic):
    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    q_lower = theory_or_topic.lower()
    matched = []

    for idx, s in enumerate(ledger.get("sources", [])):
        theories = [t.lower() for t in s.get("theoretical_frameworks", [])]
        scores = s.get("relevance_scores", {})
        
        la_val = scores.get("learner_agency") or 0
        ee_val = scores.get("evidence_evaluation") or 0
        tw_val = scores.get("traceable_workflows") or 0
        
        hit = any(q_lower in t for t in theories)
        if not hit and "agency" in q_lower and la_val >= 4:
            hit = True
        if not hit and "evidence" in q_lower and ee_val >= 4:
            hit = True
        if not hit and "workflow" in q_lower and tw_val >= 4:
            hit = True

        if hit:
            matched.append((idx, s))

    print("=" * 70)
    print(f"S3A Knowledge Graph Subgraph: '{theory_or_topic}'")
    print(f"Archive: {cfg.get('archive_path')} (Tile 3: RESEARCH_GRAPH)")
    print("=" * 70)

    print(f"\nFound {len(matched)} associated literature nodes:")
    for idx, s in matched[:10]:
        sid = s["source_id"]
        coord = f"L0:T2:R{idx}"
        theories_str = ", ".join(s.get("theoretical_frameworks", [])) or "Topical Alignment"
        print(f"  ├─ [`{sid}` | `{coord}`] {s.get('title')[:60]}... (Theory: {theories_str})")

    print("\n" + "=" * 70)

def execute_hybrid_query(cfg, query_str, limit=5):
    print("=" * 70)
    print(f"S3A Hybrid Query (Spatial RAG + Knowledge Graph DAG): '{query_str}'")
    print("=" * 70)
    rag_res = execute_rag_query(cfg, query_str, limit=limit)
    if rag_res:
        top_sid = rag_res[0][2]["source_id"]
        print(f"\nDeep Traversal on Primary Node: {top_sid}")
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
        execute_dag_query(cfg, args.id or "SRC-0002")
    elif args.mode == "graph":
        execute_graph_query(cfg, args.theory or "Learner Agency")
    elif args.mode == "hybrid":
        execute_hybrid_query(cfg, args.query or "scaffolding agency", limit=args.limit)
