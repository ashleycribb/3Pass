"""
DEPRECATED shim. Kept so old invocations do not break.

The previous version of this script hardcoded a list of eight Source IDs and
marked them "P2 done" without the researcher choosing them, and hardcoded
machine-specific ledger paths. Use run_pass2.py instead; it only runs on papers
the researcher selects with --id and labels every output as an AI draft.
"""

import argparse

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
    from scripts.run_pass2 import run_pass2_analysis
except ImportError:  # run directly as `python scripts/<name>.py`
    from corpus_common import DEFAULT_CONFIG_PATH, load_config
    from run_pass2 import run_pass2_analysis

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deprecated: use run_pass2.py")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--id", nargs="+", required=True, dest="ids", help="Researcher-selected Source ID(s)")
    args = parser.parse_args()
    print("pass2_deep_analysis.py is deprecated; forwarding to run_pass2.py", flush=True)
    run_pass2_analysis(load_config(args.config), args.ids)
