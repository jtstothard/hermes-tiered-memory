"""Command-line interface for hermes-tiered-memory.

Provides ``provision`` and ``doctor`` subcommands.  The implementations are
stubs — real logic lands in later release tickets.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``hermes-tiered-memory`` console script."""
    parser = argparse.ArgumentParser(
        prog="hermes-tiered-memory",
        description="hermes-tiered-memory — provision and diagnose the tiered memory plugin.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "provision",
        help="Provision L1/L2 backends and generate verification keys.",
    )
    subparsers.add_parser(
        "doctor",
        help="Run health checks against installed backends and config.",
    )

    args = parser.parse_args(argv)

    if args.command == "provision":
        print("provision: not yet implemented")
        return 0
    elif args.command == "doctor":
        print("doctor: not yet implemented")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
