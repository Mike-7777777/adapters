"""CLI entry point: `uv run otel-bench --output-dir <path>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .adapter import TASK_IDS, UPSTREAM_COMMIT, UPSTREAM_REPO, OTelBenchAdapter

DEFAULT_OUTPUT_DIR = Path.cwd() / "datasets" / "otel-bench"

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch OTelBench tasks (already in Harbor format) at the pinned upstream commit."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write task directories to (default: datasets/otel-bench)",
    )
    parser.add_argument(
        "--task-ids", nargs="+", default=None, help="Only generate these task ids"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Generate only the first N selected tasks",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace existing task directories"
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Local copy of upstream datasets/otel at the pinned commit (skips the git fetch)",
    )
    parser.add_argument(
        "--list-tasks", action="store_true", help="Print the task ids and exit"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.list_tasks:
        print("\n".join(TASK_IDS))
        return

    adapter = OTelBenchAdapter(output_dir=args.output_dir, source_dir=args.source_dir)
    try:
        written = adapter.run(
            task_ids=args.task_ids, limit=args.limit, overwrite=args.overwrite
        )
    except (ValueError, OSError, RuntimeError) as error:
        logger.error("%s", error)
        sys.exit(1)
    logger.info(
        "Wrote %d task(s) to %s from %s@%s",
        len(written),
        args.output_dir,
        UPSTREAM_REPO,
        UPSTREAM_COMMIT[:7],
    )


if __name__ == "__main__":
    main()
