"""Generation tests for the oracle layout. They need no network and no Docker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aider_polyglot.adapter import AiderPolyglotAdapter


def write_exercise(repo: Path, examples: dict[str, str]) -> None:
    """Write a one-exercise Python track; examples maps a relative path to its text."""
    exercise = repo / "python" / "exercises" / "practice" / "demo"
    (exercise / ".meta").mkdir(parents=True)
    (exercise / ".docs").mkdir()
    (exercise / ".docs" / "instructions.md").write_text("Add two numbers.\n")
    (exercise / "demo.py").write_text("def add(a, b):\n    pass\n")
    (exercise / "demo_test.py").write_text("from demo import add\n")
    for rel, text in examples.items():
        path = exercise / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    config = {
        "files": {
            "solution": ["demo.py"],
            "test": ["demo_test.py"],
            "example": list(examples),
        }
    }
    (exercise / ".meta" / "config.json").write_text(json.dumps(config))


def make_adapter(tmp_path: Path, examples: dict[str, str]) -> AiderPolyglotAdapter:
    repo = tmp_path / "polyglot"
    write_exercise(repo, examples)
    return AiderPolyglotAdapter(
        task_dir=tmp_path / "tasks", polyglot_repo_path=repo, languages="python"
    )


def test_reference_goes_to_solution_and_stays_out_of_the_image(tmp_path: Path) -> None:
    reference = "def add(a, b):\n    return a + b\n"
    adapter = make_adapter(tmp_path, {".meta/example.py": reference})

    assert adapter.run(task_ids=["python_demo"]) == (1, 0)

    task_dir = tmp_path / "tasks" / "polyglot_python_demo"
    oracle = task_dir / "solution" / "oracle"
    assert (oracle / "files" / "example.py").read_text() == reference
    assert (oracle / "manifest.txt").read_text() == "files/example.py|demo.py\n"
    # environment/ is the Docker build context: the reference must not be in it.
    environment = task_dir / "environment"
    assert not (environment / "workspace" / ".oracle").exists()
    assert all(
        reference not in path.read_text()
        for path in environment.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize(
    "examples",
    [
        pytest.param({}, id="no example files"),
        pytest.param(
            {".meta/a/example.py": "A = 1\n", ".meta/b/example.py": "B = 1\n"},
            id="two examples with one basename",
        ),
    ],
)
def test_task_without_a_usable_reference_is_not_generated(
    tmp_path: Path, examples: dict[str, str]
) -> None:
    adapter = make_adapter(tmp_path, examples)

    assert adapter.run(task_ids=["python_demo"]) == (0, 1)
    assert not (tmp_path / "tasks" / "polyglot_python_demo").exists()
