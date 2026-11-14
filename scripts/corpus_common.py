"""
Shared configuration loading for the 3Pass scripts.

All machine-specific values (Drive mount, s3a-cli location, etc.) come from
config/corpus_config.yaml, which is local-only and gitignored. Copy
config/corpus_config.example.yaml to config/corpus_config.yaml and edit it.
"""

import os
import shutil
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(REPO_ROOT, "config", "corpus_config.yaml")
EXAMPLE_CONFIG_PATH = os.path.join(REPO_ROOT, "config", "corpus_config.example.yaml")

# Non-machine-specific defaults. Drive paths are intentionally absent: they must
# come from the local config file.
DEFAULTS = {
    "ledger_dir": "ledger",
    "wiki_dir": "wiki",
    "cache_dir": "cache",
    "archive_path": "ledger/research_corpus.s3a",
    "intermediate_dir": "cache/s3a_intermediate",
    "batch_size": 50,
    "s3a_binary_path": os.environ.get("S3A_CLI", "s3a-cli"),
}


def load_config(config_path=DEFAULT_CONFIG_PATH):
    """Load config, layering the file (if present) over DEFAULTS."""
    cfg = dict(DEFAULTS)
    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg.update(yaml.safe_load(f) or {})
    if os.environ.get("S3A_BINARY_PATH"):
        cfg["s3a_binary_path"] = os.environ["S3A_BINARY_PATH"]
    elif os.environ.get("S3A_CLI"):
        cfg["s3a_binary_path"] = os.environ["S3A_CLI"]
    return cfg


def require_config_value(cfg, key):
    """Return cfg[key] or exit with a clear message pointing at the example config."""
    value = cfg.get(key)
    if not value:
        raise SystemExit(
            f"Error: '{key}' is not set. Copy config/corpus_config.example.yaml to "
            f"config/corpus_config.yaml and set it for this machine."
        )
    return value
