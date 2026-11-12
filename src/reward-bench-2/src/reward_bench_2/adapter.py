"""Generate Harbor tasks from RewardBench 2 (allenai/reward-bench-2).

One dataset row becomes one task. The five best-of-4 subsets use the official
four-way judge prompt, with the chosen response moved to the position the
official harness draws for that row (seed 0). Ties rows ask for a 1-10 rating
of every response, the protocol the official harness enforces for Ties.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np

from . import prompts, rb2_data

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "task-template"
PARITY_IDS_FILE = PACKAGE_DIR / "parity_task_ids.txt"

TIES = "Ties"
LETTERS = "ABCD"
OFFICIAL_SEED = 0
# sha256 of json.dumps(positions) for the 1,763 best-of-4 rows at seed 0,
# as produced by rewardbench.random_utils.generate_shuffle_positions.
OFFICIAL_POSITIONS_SHA256 = (
    "c2355d24213b63e4d5bded92451b1eda65688016be1736d908458ebfd21c08e8"
)
OFFICIAL_BEST_OF_4_ROWS = 1763
SPLITS = ("full", "parity")
PARITY_PER_SUBSET = 40
ORACLE_CORRECT_RATING = 10
ORACLE_INCORRECT_RATING = 1


def generate_shuffle_positions(num_examples: int, seed: int) -> list[int]:
    """Same call as rewardbench.random_utils.generate_shuffle_positions."""
    return np.random.default_rng(seed).integers(0, 4, size=num_examples).tolist()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def task_id_for(row: dict) -> str:
    """Row ids repeat across subsets, so the subset is part of the task id."""
    return f"{slugify(row['subset'])}-{slugify(row['id'])}"


def place_chosen(chosen: str, rejected: list[str], position: int) -> list[str]:
    """Swap the chosen response from slot A into `position`, as the harness does."""
    answers = [chosen, *rejected[:3]]
    answers[0], answers[position] = answers[position], answers[0]
    return answers


def ties_display_order(task_id: str, total: int) -> list[int]:
    """Displayed position -> dataset index, in sha256 order.

    The dataset lists correct responses first, so they are shown in an order
    that does not reveal the split and needs no random generator.
    """
    return sorted(
        range(total),
        key=lambda index: hashlib.sha256(f"{task_id}:{index}".encode()).hexdigest(),
    )


def select_parity_ids(task_ids_by_subset: dict[str, list[str]]) -> list[str]:
    """All Ties tasks (the Ties score needs every ref/tied pair) plus, for each
    best-of-4 subset, the first PARITY_PER_SUBSET task ids in sha256 order."""
    chosen: list[str] = []
    for subset, task_ids in task_ids_by_subset.items():
        if subset == TIES:
            chosen += task_ids
            continue
        ranked = sorted(task_ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        chosen += ranked[:PARITY_PER_SUBSET]
    return sorted(chosen)


def fill(template: str, values: dict[str, str]) -> str:
    """Replace `{key}` placeholders in one pass, so inserted dataset text is
    never scanned for placeholders itself."""
    return re.sub(
        r"\{(\w+)\}", lambda match: values.get(match.group(1), match.group(0)), template
    )


class RewardBench2Adapter:
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        split: str = "full",
    ):
        if split not in SPLITS:
            raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
        if limit is not None and limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = set(task_ids) if task_ids else None
        self.split = split

    def build_tasks(self) -> list[dict]:
        """One spec per dataset row, in file order, with its answer key."""
        rows = rb2_data.load_rows()
        best_of_4 = [row for row in rows if row["subset"] != TIES]
        positions = generate_shuffle_positions(len(best_of_4), OFFICIAL_SEED)
        if len(best_of_4) == OFFICIAL_BEST_OF_4_ROWS:
            digest = hashlib.sha256(json.dumps(positions).encode()).hexdigest()
            if digest != OFFICIAL_POSITIONS_SHA256:
                raise RuntimeError(
                    "numpy produced answer positions that differ from the pinned "
                    f"official seed-0 positions (sha256 {digest}); check the numpy version."
                )
        position_iter = iter(positions)
        specs = []
        for row in rows:
            spec = {
                "task_id": task_id_for(row),
                "subset": row["subset"],
                "source_id": row["id"],
                "row": row,
            }
            if row["subset"] == TIES:
                total = len(row["chosen"]) + len(row["rejected"])
                spec["protocol"] = "ratings"
                spec["order"] = ties_display_order(spec["task_id"], total)
            else:
                if len(row["chosen"]) != 1 or len(row["rejected"]) != 3:
                    raise RuntimeError(
                        f"{spec['task_id']}: expected 1 chosen, 3 rejected"
                    )
                spec["protocol"] = "rankings"
                spec["position"] = next(position_iter)
            specs.append(spec)
        if len({spec["task_id"] for spec in specs}) != len(specs):
            raise RuntimeError("task ids are not unique")
        return specs

    def select(self, specs: list[dict]) -> list[dict]:
        if self.split == "parity":
            by_subset: dict[str, list[str]] = {}
            for spec in specs:
                by_subset.setdefault(spec["subset"], []).append(spec["task_id"])
            parity_ids = select_parity_ids(by_subset)
            # The original-benchmark side reads the committed list, so both
            # sides must run the same rows.
            if parity_ids != PARITY_IDS_FILE.read_text().split():
                raise RuntimeError(f"parity subset differs from {PARITY_IDS_FILE.name}")
            specs = [spec for spec in specs if spec["task_id"] in set(parity_ids)]
        if self.task_ids:
            unknown = self.task_ids - {spec["task_id"] for spec in specs}
            if unknown:
                raise ValueError(
                    f"task ids not in the {self.split} split: {sorted(unknown)}"
                )
            specs = [spec for spec in specs if spec["task_id"] in self.task_ids]
        if self.limit is not None:
            specs = specs[: self.limit]
        return specs

    def run(self) -> None:
        specs = self.select(self.build_tasks())
        self.output_dir.mkdir(parents=True, exist_ok=True)
        written = sum(self._write_task(spec) for spec in specs)
        print(f"wrote {written} of {len(specs)} selected tasks to {self.output_dir}")

    def _write_task(self, spec: dict) -> bool:
        task_dir = self.output_dir / spec["task_id"]
        if task_dir.exists() and not self.overwrite:
            return False
        # Build in a staging directory and rename, so an interrupted run never
        # leaves a half-written task that a later run would skip.
        staging = self.output_dir / f".{spec['task_id']}.partial"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(
            TEMPLATE_DIR, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        ties_template = staging / "instruction-ties.md"
        if spec["protocol"] == "ratings":
            instruction, truth, oracle = self._ratings_task(
                spec, ties_template.read_text()
            )
        else:
            instruction, truth, oracle = self._ranking_task(
                spec, (staging / "instruction.md").read_text()
            )
        ties_template.unlink()
        # newline="" keeps the dataset text byte-for-byte (some rows contain \r).
        (staging / "instruction.md").write_text(
            instruction, encoding="utf-8", newline=""
        )
        truth = {
            "task_id": spec["task_id"],
            "subset": spec["subset"],
            "source_id": spec["source_id"],
            "protocol": spec["protocol"],
            **truth,
        }
        (staging / "tests" / "ground_truth.json").write_text(json.dumps(truth) + "\n")
        task_toml = staging / "task.toml"
        task_toml.write_text(
            fill(
                task_toml.read_text(),
                {
                    "task_id": spec["task_id"],
                    "subset": spec["subset"],
                    "subset_slug": slugify(spec["subset"]),
                    "source_id": spec["source_id"],
                    "protocol": spec["protocol"],
                },
            )
        )
        solve = staging / "solution" / "solve.sh"
        solve.write_text(fill(solve.read_text(), {"body": oracle}))
        if task_dir.exists():
            shutil.rmtree(task_dir)
        staging.rename(task_dir)
        return True

    def _ranking_task(self, spec: dict, template: str) -> tuple[str, dict, str]:
        row = spec["row"]
        answers = place_chosen(row["chosen"][0], row["rejected"], spec["position"])
        instruction = fill(
            template, {"judge_prompt": prompts.ranking_prompt(row["prompt"], answers)}
        )
        answer = LETTERS[spec["position"]]
        oracle = f"echo '[[{answer}]]' > /workspace/judgment.txt"
        return instruction, {"answer": answer}, oracle

    def _ratings_task(self, spec: dict, template: str) -> tuple[str, dict, str]:
        row = spec["row"]
        completions = [*row["chosen"], *row["rejected"]]
        num_correct = len(row["chosen"])
        order = spec["order"]
        instruction = fill(
            template,
            {
                "n": str(len(order)),
                "query": row["prompt"],
                "responses": prompts.rating_responses([completions[i] for i in order]),
            },
        )
        ratings = {
            str(position + 1): ORACLE_CORRECT_RATING
            if original_index < num_correct
            else ORACLE_INCORRECT_RATING
            for position, original_index in enumerate(order)
        }
        oracle = f"echo '{json.dumps(ratings)}' > /workspace/ratings.json"
        return instruction, {"num_correct": num_correct, "order": order}, oracle
