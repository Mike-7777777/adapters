"""Generate Harbor tasks from Scale AI PRBench.

Source: the HF dataset ScaleAI/PRBench (CC-BY-4.0): 600 Finance and 500 Legal
conversations, each with an expert-written weighted rubric. A task asks the
agent for the assistant's next reply; the verifier grades that reply against
the rubric with the upstream judge prompt and scoring (tests/grader.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

from . import dataset

ADAPTER_NAME = "prbench"
DOMAINS = ("finance", "legal")

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "task-template"
PARITY_IDS_FILE = PACKAGE_DIR / "parity_task_ids.txt"
PARITY_SIZE_PER_DOMAIN = 100
REFERENCE_TEXT_MODES = ("prepend", "omit")
SPLITS = (
    "full",
    "hard",
    "parity",
    *DOMAINS,
    *(f"{domain}_hard" for domain in DOMAINS),
)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def select_parity_ids(
    tasks: list[dict], size: int = PARITY_SIZE_PER_DOMAIN
) -> list[str]:
    """Pick one domain's parity subset, stratified by (hard, topic).

    Each stratum gets seats in proportion to its size (largest remainder; ties
    go to the earlier stratum key). Within a stratum, records are taken in
    sha256(task id) order, so the choice needs no random generator.
    """
    strata: dict[tuple[bool, str], list[str]] = defaultdict(list)
    for task in tasks:
        strata[(task["hard"], task["topic"])].append(task["id"])
    keys = sorted(strata)
    quotas = {k: len(strata[k]) * size / len(tasks) for k in keys}
    seats = {k: int(quotas[k]) for k in keys}
    by_remainder = sorted(keys, key=lambda k: -(quotas[k] - seats[k]))
    for k in by_remainder[: size - sum(seats.values())]:
        seats[k] += 1
    chosen = []
    for k in keys:
        ranked = sorted(strata[k], key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        chosen += ranked[: seats[k]]
    return sorted(chosen)


def render_conversation(messages: list[dict]) -> str:
    total = len(messages)
    return "\n\n".join(
        f"===== {m['role'].upper()} (message {n} of {total}) =====\n\n{m['content']}"
        for n, m in enumerate(messages, 1)
    )


def rubric_outline(rubric: list[dict]) -> str:
    """Default oracle reply: the positive criteria of the rubric, verbatim.

    PRBench has no reference answers. This outline is what an ideal reply must
    cover according to the rubric; a reply written for the task and passed in
    with --oracle-dir takes its place.
    """
    points = "".join(f"- {item['title']}\n" for item in rubric if item["weight"] > 0)
    return (
        "Reference outline built from the positive criteria of this task's "
        f"rubric:\n\n{points}\nEnd of outline.\n"
    )


def fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


class PRBenchAdapter:
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        source_dir: Path | None = None,
        oracle_dir: Path | None = None,
        split: str = "full",
        reference_texts: str = "prepend",
        **kwargs,
    ):
        self.split = split
        self.reference_texts = reference_texts
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = set(task_ids) if task_ids else None
        self.source_dir = Path(source_dir) if source_dir else None
        self.oracle_dir = Path(oracle_dir) if oracle_dir else None

    def load_tasks(self) -> list[dict]:
        """All records of the adapter's domains, in upstream order."""
        tasks = []
        for domain in DOMAINS:
            hard_canaries = {
                row["task"]: row["canary"]
                for row in dataset.load_rows(f"{domain}_hard", self.source_dir)
            }
            for row in dataset.load_rows(domain, self.source_dir):
                task = row["task"]
                canaries = [row["canary"]]
                if task in hard_canaries:
                    canaries.append(hard_canaries[task])
                tasks.append(
                    {
                        "id": f"{domain}-{task}",
                        "domain": domain,
                        "hard": task in hard_canaries,
                        "topic": row["topic"],
                        "canaries": canaries,
                        "row": row,
                    }
                )
        return tasks

    def select(self, tasks: list[dict]) -> list[dict]:
        if self.split == "parity":
            ids = set()
            for domain in DOMAINS:
                in_domain = [t for t in tasks if t["domain"] == domain]
                ids.update(select_parity_ids(in_domain, PARITY_SIZE_PER_DOMAIN))
            # The original-benchmark side reads the committed list, so both
            # sides must run the same records.
            if ids != set(PARITY_IDS_FILE.read_text().split()):
                raise RuntimeError(f"parity subset differs from {PARITY_IDS_FILE.name}")
            tasks = [t for t in tasks if t["id"] in ids]
        elif self.split != "full":
            domain = self.split.removesuffix("_hard")
            hard_only = self.split.endswith("hard")
            tasks = [
                t
                for t in tasks
                if (domain == "hard" or t["domain"] == domain)
                and (t["hard"] or not hard_only)
            ]
        if self.task_ids:
            unknown = self.task_ids - {t["id"] for t in tasks}
            if unknown:
                raise ValueError(
                    f"task ids not in split {self.split!r}: {sorted(unknown)}"
                )
            tasks = [t for t in tasks if t["id"] in self.task_ids]
        if self.limit is not None:
            tasks = tasks[: self.limit]
        return tasks

    def run(self) -> None:
        tasks = self.select(self.load_tasks())
        self.output_dir.mkdir(parents=True, exist_ok=True)
        written = sum(self._write_task(task) for task in tasks)
        print(
            f"split {self.split!r}: wrote {written} tasks, skipped "
            f"{len(tasks) - written} existing (--overwrite replaces them) "
            f"-> {self.output_dir}"
        )

    def _oracle_body(self, task_id: str, rubric: list[dict]) -> str:
        written = self.oracle_dir / f"{task_id}.txt" if self.oracle_dir else None
        if written and written.is_file():
            reply = written.read_bytes()
        else:
            reply = rubric_outline(rubric).encode()
        encoded = base64.b64encode(reply).decode()
        return f"mkdir -p /workspace\necho '{encoded}' | base64 -d > /workspace/response.txt"

    def _write_task(self, task: dict) -> bool:
        """Write one task directory; False when it exists and is left as is."""
        task_dir = self.output_dir / task["id"]
        if task_dir.exists():
            if not self.overwrite:
                return False
            shutil.rmtree(task_dir)
        # Importing the template's grader (unit tests do) leaves a bytecode cache there.
        shutil.copytree(
            TEMPLATE_DIR,
            task_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        row = task["row"]
        messages = dataset.build_messages(row, self.reference_texts)
        keywords = [
            "prbench",
            "rubric",
            "llm-judge",
            task["domain"],
            slug(row["topic"]),
        ]
        if task["hard"]:
            keywords.append("hard")
        values = {
            "adapter_name": ADAPTER_NAME,
            "task_id": task["id"],
            "source_id": row["task"],
            "domain": task["domain"],
            "topic": json.dumps(row["topic"]),
            "keywords": json.dumps(keywords),
            "difficulty": "hard" if task["hard"] else "medium",
            "hard": "true" if task["hard"] else "false",
            "turns": str(sum(m["role"] == "user" for m in messages)),
        }
        toml = task_dir / "task.toml"
        toml.write_text(fill(toml.read_text(), values))
        instruction = task_dir / "instruction.md"
        canary = "".join(f"<!-- canary: {c} -->\n" for c in task["canaries"])
        instruction.write_text(instruction.read_text() + "\n" + canary)
        (task_dir / "environment" / "conversation.md").write_text(
            render_conversation(messages) + "\n"
        )
        rubric = dataset.rubric_items(row)
        solve = task_dir / "solution" / "solve.sh"
        solve.write_text(
            fill(solve.read_text(), {"body": self._oracle_body(task["id"], rubric)})
        )
        record = {
            "task_id": task["id"],
            "source_id": row["task"],
            "domain": task["domain"],
            "canaries": task["canaries"],
            "messages": messages,
            "rubric": rubric,
        }
        (task_dir / "tests" / "task.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        )
        return True
