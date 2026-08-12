"""Command line interface for build/provenance diagnostics."""

from __future__ import annotations

import argparse
import json
import sys

from .lineage import resolve_task_lineage


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m knowe_provenance")
    sub = parser.add_subparsers(dest="command", required=True)
    lineage = sub.add_parser("lineage", help="resolve a task across Runtime and Harness projections")
    lineage.add_argument("--data-root", required=True)
    lineage.add_argument("--task-id", required=True)
    lineage.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "lineage":
        result = resolve_task_lineage(args.data_root, args.task_id)
        print(json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        ))
        return 0 if result["status"] != "not_found" else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
