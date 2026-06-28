"""
Adapter runner. Refuses to run LIVE adapters unless config/runtime.yaml live_feeds_enabled.
This is the eval-gate-before-live-feed enforcer at the ingest boundary.

Usage:
    python -m ingest.run --adapter ucdp_ged    # only works after Gate 1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_RUNTIME = Path(__file__).parent.parent / "config" / "runtime.yaml"


def live_feeds_enabled() -> bool:
    with open(_RUNTIME) as f:
        return bool(yaml.safe_load(f)["runtime"].get("live_feeds_enabled", False))


def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(prog="python -m ingest.run")
    p.add_argument("--adapter", required=True, choices=["ucdp_ged", "firms"])
    p.add_argument("--force", action="store_true", help="override the live-feed gate (NOT for CI)")
    args = p.parse_args()

    if not live_feeds_enabled() and not args.force:
        print(
            "REFUSED: live_feeds_enabled is false in config/runtime.yaml.\n"
            "The eval gate (python -m eval.harness) must pass before live feeds are wired.\n"
            "This is the eval-gate-before-live-feed hard rule.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Phase-2 adapters are dispatched here once they exist.
    if args.adapter == "ucdp_ged":
        from ingest.text.ucdp_ged import run as run_adapter  # noqa: F401
    elif args.adapter == "firms":
        from ingest.thermal.firms import run as run_adapter  # noqa: F401
    else:
        print(f"Unknown adapter {args.adapter}", file=sys.stderr)
        sys.exit(1)

    run_adapter()


if __name__ == "__main__":
    main()
