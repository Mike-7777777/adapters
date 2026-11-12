"""Generate Harbor tasks from OpenAI HealthBench Professional.

Source: the HF dataset openai/healthbench-professional (MIT), one JSONL file of
525 clinician conversations, each with physician-written rubric items. The
dataset asks that examples not be shown in plain text online, so a generated
task holds only the record id and the canary: the image build fetches the
conversation and the verifier fetches the rubric from a pinned, sha256-checked
revision (hbp_data.py).
"""

from __future__ import annotations

import base64
import hashlib
import shutil
from collections import defaultdict
from pathlib import Path

from . import hbp_data

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "task-template"
DATA_MODULE = PACKAGE_DIR / "hbp_data.py"
NO_ORACLE = 'echo "no oracle reply was generated for this task" >&2\nexit 1'
SPLITS = ("full", "parity")
PARITY_SIZE = 150


def select_parity_ids(rows: list[dict], size: int = PARITY_SIZE) -> list[str]:
    """Pick the parity subset, stratified by (use_case, type).

    Each stratum gets seats in proportion to its size (largest remainder; ties
    go to the earlier stratum key). Within a stratum, records are taken in
    sha256(id) order, so the choice needs no random generator.
    """
    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        strata[(row["use_case"], row["type"])].append(row["id"])
    keys = sorted(strata)
    # Integer quotient and remainder, so equal remainders compare equal.
    quotas = {k: divmod(len(strata[k]) * size, len(rows)) for k in keys}
    seats = {k: quotas[k][0] for k in keys}
    by_remainder = sorted(keys, key=lambda k: -quotas[k][1])
    for k in by_remainder[: size - sum(seats.values())]:
        seats[k] += 1
    chosen = []
    for k in keys:
        ranked = sorted(strata[k], key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        chosen += ranked[: seats[k]]
    return sorted(chosen)


def fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


class HealthBenchProfessionalAdapter:
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        source: str | None = None,
        oracle_dir: Path | None = None,
        split: str = "full",
    ):
        if split not in SPLITS:
            raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
        self.split = split
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = set(task_ids) if task_ids else None
        self.source = source
        self.oracle_dir = Path(oracle_dir) if oracle_dir else None

    def run(self) -> None:
        rows = hbp_data.load_rows(self.source)
        if self.split == "parity":
            ids = set(select_parity_ids(rows, PARITY_SIZE))
            rows = [r for r in rows if r["id"] in ids]
        if self.task_ids:
            rows = [r for r in rows if r["id"] in self.task_ids]
        if self.limit is not None:
            rows = rows[: self.limit]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            self._write_task(row)

    def _oracle_body(self, task_id: str) -> str:
        reply = self.oracle_dir / f"{task_id}.txt" if self.oracle_dir else None
        if not reply or not reply.is_file():
            return NO_ORACLE
        encoded = base64.b64encode(reply.read_bytes()).decode()
        return f"mkdir -p /workspace\necho '{encoded}' | base64 -d > /workspace/response.txt"

    def _write_task(self, row: dict) -> None:
        task_dir = self.output_dir / row["id"]
        if task_dir.exists():
            if not self.overwrite:
                return
            shutil.rmtree(task_dir)
        # Tests import the template's grader, which leaves bytecode caches there.
        shutil.copytree(
            TEMPLATE_DIR,
            task_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        values = {
            "task_id": row["id"],
            "use_case": row["use_case"],
            "type": row["type"],
            "specialty": row["specialty"],
            "difficulty": "hard" if row["difficulty"] == "difficult" else "medium",
        }
        for name in ("task.toml", "environment/Dockerfile"):
            path = task_dir / name
            path.write_text(fill(path.read_text(), values))
        instruction = task_dir / "instruction.md"
        instruction.write_text(
            instruction.read_text() + f"\n<!-- canary: {row['canary_string']} -->\n"
        )
        solve = task_dir / "solution" / "solve.sh"
        solve.write_text(
            fill(solve.read_text(), {"body": self._oracle_body(row["id"])})
        )
        for sub in ("environment", "tests"):
            shutil.copy2(DATA_MODULE, task_dir / sub / "hbp_data.py")
        (task_dir / "tests" / "task_id").write_text(row["id"] + "\n")
