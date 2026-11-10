"""
Main entry point for the template adapter. Do not modify any of the existing flags.
You can add any additional flags you need.

Constructs the Adapter class defined in adapter.py and calls run() to generate tasks
in the Harbor format at the configured output directory.
"""

import argparse
from pathlib import Path

from .adapter import PARITY_SIZE, REWARD_MODES, SPLITS, HealthBenchProfessionalAdapter

# Default output dir: <repo>/datasets/<adapter_id>
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[4] / "datasets" / "healthbench-professional"
)


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
        "--source",
        default=None,
        help="Local copy of the pinned dataset file (default: download it)",
    )
    parser.add_argument(
        "--oracle-dir",
        type=Path,
        default=None,
        help="Directory of <task_id>.txt oracle replies for solution/solve.sh",
    )
    parser.add_argument(
        "--reward-mode",
        choices=REWARD_MODES,
        default="both",
        help="Default REWARD_MODE written into task.toml (see tests/grader.py); "
        "HBP_REWARD_MODE overrides it at run time",
    )
    parser.add_argument(
        "--split",
        choices=SPLITS,
        default="full",
        help=f"'parity' generates only the {PARITY_SIZE}-task parity subset "
        "(parity_task_ids.txt; see the README)",
    )
    args = parser.parse_args()

    adapter = HealthBenchProfessionalAdapter(
        args.output_dir,
        overwrite=args.overwrite,
        limit=args.limit,
        task_ids=args.task_ids,
        source=args.source,
        oracle_dir=args.oracle_dir,
        reward_mode=args.reward_mode,
        split=args.split,
    )

    adapter.run()


if __name__ == "__main__":
    main()
