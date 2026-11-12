"""Fetch OTelBench tasks at a pinned upstream commit and prepare them for the registry.

OTelBench is authored in Harbor's task format, so nothing is converted. Each task's
instruction, environment and tests are copied byte for byte from the upstream
repository. The adapter re-renders `task.toml` from `task-template/task.toml` to add
the `[task]` table (registry name and benchmark authors), and applies the oracle
files under `oracles/` so that every task has a reference solution that passes.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

UPSTREAM_REPO = "https://github.com/QuesmaOrg/otel-bench.git"
# Last commit before upstream prepended a canary line to instruction.md and five
# other files of every task (5afbba1); the content is otherwise identical. See
# README, Notes & Caveats.
UPSTREAM_COMMIT = "bb6e21ad3caeceb82617dc5bf36e47deb33172ae"
UPSTREAM_TASKS_DIR = "datasets/otel"

TASK_IDS = (
    "cpp-advanced",
    "cpp-distributed-context-propagation",
    "cpp-simple",
    "erlang-microservices",
    "go-distributed-context-propagation",
    "go-grpc-fix",
    "go-http-tracing",
    "go-microservices",
    "go-microservices-logs",
    "go-microservices-traces",
    "go-microservices-traces-simple",
    "go-workflow-tracing",
    "java-advanced",
    "java-distributed-context-propagation",
    "java-microservices",
    "java-simple",
    "js-microservices",
    "net-microservices",
    "php-distributed-context-propagation",
    "php-microservices",
    "python-distributed-context-propagation",
    "python-microservices",
    "ruby-microservices",
    "rust-microservices",
    "swift-microservices",
)

# Upstream tasks left out of the dataset, with the reason. See README, Notes & Caveats.
EXCLUDED_TASKS = {
    "rust-distributed-context-propagation": (
        "its image no longer builds: `cargo fetch` on rust:1.82 without a Cargo.lock "
        "now resolves idna_adapter 1.2.2, which needs the 2024 edition"
    ),
}

# Microservices tasks whose services need more than the verifier's one-second
# start-up window; their oracles get the shared readiness wait.
READINESS_WAIT_TASK_IDS = (
    "java-microservices",
    "net-microservices",
    "ruby-microservices",
)

# Harbor's task components. Upstream task directories also hold run summaries and
# spare collector configs that are not part of a task and are left behind.
TASK_COMPONENTS = ("task.toml", "instruction.md", "environment", "solution", "tests")

# Template placeholder -> key in the upstream task.toml. `version` is the only other
# upstream field; generation fails on anything else.
CARRIED_FIELDS = {
    "author_name": ("metadata", "author_name"),
    "author_email": ("metadata", "author_email"),
    "keywords": ("metadata", "tags"),
    "difficulty": ("metadata", "difficulty"),
    "category": ("metadata", "category"),
    "verifier_timeout_sec": ("verifier", "timeout_sec"),
    "agent_timeout_sec": ("agent", "timeout_sec"),
    "build_timeout_sec": ("environment", "build_timeout_sec"),
    "cpus": ("environment", "cpus"),
    "memory_mb": ("environment", "memory_mb"),
    "storage_mb": ("environment", "storage_mb"),
}

PACKAGE_DIR = Path(__file__).resolve().parent
TASK_TEMPLATE = PACKAGE_DIR / "task-template" / "task.toml"
ORACLE_DIR = PACKAGE_DIR / "oracles"


class OTelBenchAdapter:
    """Materialize OTelBench tasks into a Harbor dataset directory."""

    def __init__(self, output_dir: Path, source_dir: Path | None = None):
        self.output_dir = Path(output_dir)
        self.source_dir = Path(source_dir) if source_dir else None

    def run(
        self,
        task_ids: list[str] | None = None,
        limit: int | None = None,
        overwrite: bool = False,
    ) -> list[Path]:
        selected = self._select(task_ids, limit)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.source_dir is not None:
            return self._generate(self.source_dir, selected, overwrite)
        with tempfile.TemporaryDirectory(prefix="otel-bench-") as tmp:
            return self._generate(fetch_upstream(Path(tmp)), selected, overwrite)

    @staticmethod
    def _select(task_ids: list[str] | None, limit: int | None) -> list[str]:
        selected = list(task_ids) if task_ids else list(TASK_IDS)
        for task_id in selected:
            if task_id in EXCLUDED_TASKS:
                raise ValueError(f"{task_id} is excluded: {EXCLUDED_TASKS[task_id]}.")
        unknown = sorted(set(selected) - set(TASK_IDS))
        if unknown:
            raise ValueError(
                f"Unknown task ids: {unknown}. Run with --list-tasks to see valid ids."
            )
        if limit is not None and limit < 0:
            raise ValueError(f"--limit must not be negative, got {limit}.")
        return selected[:limit] if limit is not None else selected

    def _generate(
        self, tasks_root: Path, task_ids: list[str], overwrite: bool
    ) -> list[Path]:
        written = []
        for task_id in task_ids:
            target = self.output_dir / task_id
            if target.exists():
                if not overwrite:
                    logger.info("Skipping %s: %s exists", task_id, target)
                    continue
                shutil.rmtree(target)
            source = tasks_root / task_id
            if not source.is_dir():
                raise FileNotFoundError(
                    f"Task {task_id} not found at {source}; expected upstream "
                    f"commit {UPSTREAM_COMMIT}."
                )
            try:
                copy_task_components(source, target)
                add_oracle(task_id, target)
                (target / "task.toml").write_text(
                    render_task_toml(task_id, target / "task.toml"), encoding="utf-8"
                )
            except BaseException:
                # A half-written directory would be skipped as existing on the next run.
                shutil.rmtree(target, ignore_errors=True)
                raise
            written.append(target)
            logger.info("Wrote %s", target)
        return written


def fetch_upstream(workdir: Path) -> Path:
    """Sparse-clone the pinned commit and return the directory holding the tasks."""
    repo = workdir / "otel-bench"
    for args in (
        [
            "clone",
            "--filter=blob:none",
            "--sparse",
            "--no-checkout",
            UPSTREAM_REPO,
            str(repo),
        ],
        ["-C", str(repo), "sparse-checkout", "set", UPSTREAM_TASKS_DIR],
        ["-C", str(repo), "checkout", "--quiet", UPSTREAM_COMMIT],
    ):
        result = subprocess.run(
            ["git", *args], check=False, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`git {' '.join(args)}` failed ({result.returncode}) while fetching "
                f"{UPSTREAM_REPO}@{UPSTREAM_COMMIT}: {result.stderr.strip()}. "
                "Check network access to github.com, or pass --source-dir with a "
                f"local checkout of {UPSTREAM_TASKS_DIR} at that commit."
            )
    return repo / UPSTREAM_TASKS_DIR


def check_source_commit(source_dir: Path) -> None:
    """Refuse a --source-dir that is not a git checkout of the pinned commit."""
    result = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    head = result.stdout.strip() if result.returncode == 0 else "not a git checkout"
    if head != UPSTREAM_COMMIT:
        raise ValueError(
            f"{source_dir} is not at the pinned commit {UPSTREAM_COMMIT} ({head})."
        )


def copy_task_components(source: Path, target: Path) -> None:
    target.mkdir()
    for name in TASK_COMPONENTS:
        component = source / name
        if component.is_dir():
            shutil.copytree(component, target / name)
        elif component.is_file():
            shutil.copy(component, target / name)


def add_oracle(task_id: str, target: Path) -> None:
    """Apply the adapter's oracle files for a task. Tests and environment stay untouched.

    `solve.sh` supplies the oracle for a task that ships none upstream. The fix
    snippets wrap an upstream oracle: `solve_prepend.sh` goes right after its
    shebang line, `solve_append.sh` at its end. The oracles listed in
    READINESS_WAIT_TASK_IDS also get `_shared/wait_for_services.sh`.
    """
    oracle = ORACLE_DIR / task_id
    solve = target / "solution" / "solve.sh"
    if (oracle / "solve.sh").is_file():
        if solve.exists():
            raise FileExistsError(
                f"Upstream now ships a solution for {task_id}; drop {oracle} and re-pin."
            )
        solve.parent.mkdir()
        shutil.copy(oracle / "solve.sh", solve)

    prepends = [oracle / "solve_prepend.sh"]
    appends = [oracle / "solve_append.sh"]
    if task_id in READINESS_WAIT_TASK_IDS:
        appends.append(ORACLE_DIR / "_shared" / "wait_for_services.sh")
    prepends = [path for path in prepends if path.is_file()]
    appends = [path for path in appends if path.is_file()]
    if not (prepends or appends):
        return

    shebang, _, body = solve.read_text(encoding="utf-8").partition("\n")
    if not shebang.startswith("#!"):
        raise ValueError(f"{solve} does not start with a shebang line")
    parts = [shebang + "\n"]
    parts += [path.read_text(encoding="utf-8") for path in prepends]
    parts.append(body.rstrip("\n") + "\n")
    parts += [path.read_text(encoding="utf-8") for path in appends]
    solve.write_text("".join(parts), encoding="utf-8")


def render_task_toml(task_id: str, upstream_task_toml: Path) -> str:
    """Re-render an upstream task.toml into the template, keeping every upstream value."""
    config = tomllib.loads(upstream_task_toml.read_text(encoding="utf-8"))
    dropped = sorted(_leaf_keys(config) - {("version",), *CARRIED_FIELDS.values()})
    if dropped:
        raise ValueError(
            f"{upstream_task_toml} has fields the template does not carry: {dropped}. "
            "Extend task-template/task.toml before generating."
        )
    values = {"task_id": task_id, "source_commit": UPSTREAM_COMMIT}
    for placeholder, (table, key) in CARRIED_FIELDS.items():
        values[placeholder] = json.dumps(config[table][key])
    rendered = TASK_TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", value)
    tomllib.loads(rendered)
    return rendered


def _leaf_keys(config: dict, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    keys = set()
    for key, value in config.items():
        if isinstance(value, dict):
            keys |= _leaf_keys(value, (*prefix, key))
        else:
            keys.add((*prefix, key))
    return keys
