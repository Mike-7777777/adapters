"""Command line entry point: generate PRBench tasks in the Harbor format."""

import argparse
from pathlib import Path

from .adapter import (
    ADAPTER_NAME,
    PARITY_SIZE_PER_DOMAIN,
    REFERENCE_TEXT_MODES,
    SPLITS,
    PRBenchAdapter,
)

# Default output dir: <repo>/datasets/<adapter_id>
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[4] / "datasets" / ADAPTER_NAME


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write generated tasks",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Generate only the first N tasks",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing tasks",
    )
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help="Only generate these task IDs",
    )
    parser.add_argument(
        "--split",
        choices=SPLITS,
        default="full",
        help="'full': every task; a domain or its _hard subset; 'hard': all hard "
        f"subsets; 'parity': {PARITY_SIZE_PER_DOMAIN} tasks per domain "
        "(parity_task_ids.txt; see the README)",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Directory holding the pinned parquet files (default: download them)",
    )
    parser.add_argument(
        "--oracle-dir",
        type=Path,
        default=None,
        help="Directory of <task_id>.txt oracle replies for solution/solve.sh "
        "(default: an outline of the positive rubric criteria)",
    )
    parser.add_argument(
        "--reference-texts",
        choices=REFERENCE_TEXT_MODES,
        default="prepend",
        help="'prepend' puts a turn's reference texts in front of its prompt, as "
        "the paper describes; 'omit' leaves them out (see the README)",
    )
    args = parser.parse_args()

    adapter = PRBenchAdapter(
        args.output_dir,
        overwrite=args.overwrite,
        limit=args.limit,
        task_ids=args.task_ids,
        split=args.split,
        source_dir=args.source_dir,
        oracle_dir=args.oracle_dir,
        reference_texts=args.reference_texts,
    )

    adapter.run()


if __name__ == "__main__":
    main()
