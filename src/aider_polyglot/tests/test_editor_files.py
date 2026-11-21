"""Editor files reach the agent's workspace. No network and no Docker needed."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from aider_polyglot.adapter import DEFAULT_ORACLE_SECRET, AiderPolyglotAdapter

# One exercise per language, shaped like java/alphametics and go/sublist in
# polyglot-benchmark: solution file, editor files, test file, example file.
CASES = {
    "java": (
        "src/main/java/Demo.java",
        ["src/main/java/DemoException.java"],
        "src/test/java/DemoTest.java",
        ".meta/src/reference/java/Demo.java",
    ),
    "go": (
        "demo.go",
        ["relations.go", "cases_test.go"],
        "demo_test.go",
        ".meta/example.go",
    ),
}


def generate(tmp_path: Path, language: str) -> Path:
    solution, editor, test, example = CASES[language]
    exercise = tmp_path / "polyglot" / language / "exercises" / "practice" / "demo"
    config = {
        "files": {
            "solution": [solution],
            "test": [test],
            "example": [example],
            "editor": editor,
        }
    }
    files = {
        solution: "stub\n",
        **{rel: f"editor {rel}\n" for rel in editor},
        test: "test\n",
        example: "reference\n",
        ".meta/config.json": json.dumps(config),
        ".docs/instructions.md": "Do the thing.\n",
    }
    for rel, text in files.items():
        path = exercise / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    adapter = AiderPolyglotAdapter(
        task_dir=tmp_path / "tasks",
        polyglot_repo_path=tmp_path / "polyglot",
        languages=language,
    )
    assert adapter.run(task_ids=[f"{language}_demo"]) == (1, 0)
    return tmp_path / "tasks" / f"polyglot_{language}_demo"


@pytest.mark.parametrize("language", sorted(CASES))
def test_editor_files_are_in_the_workspace_and_in_tests(
    tmp_path: Path, language: str
) -> None:
    _, editor, test, example = CASES[language]
    task = generate(tmp_path, language)
    workspace = task / "environment" / "workspace"
    for rel in editor:
        assert (task / "tests" / rel).read_text() == f"editor {rel}\n"
        # Go's cases_test.go holds the test cases, which stay hidden.
        assert (workspace / rel).exists() == (not rel.endswith("_test.go"))
    assert (workspace / editor[0]).read_text() == f"editor {editor[0]}\n"
    assert not (workspace / test).exists()
    assert not (workspace / example).exists()


def oracle_manifest(task: Path) -> list[str]:
    """Lines of the oracle payload's manifest: files/<name>|<destination in /app>."""
    enc = task / "environment" / "workspace" / ".oracle" / "solution.enc"
    tar = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2"]
        + ["-pass", f"pass:{DEFAULT_ORACLE_SECRET}", "-in", str(enc)],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as payload:
        return payload.extractfile("payload/manifest.txt").read().decode().split()


@pytest.mark.parametrize("language", sorted(CASES))
def test_oracle_still_replaces_the_solution_file(tmp_path: Path, language: str) -> None:
    solution, _, _, example = CASES[language]
    task = generate(tmp_path, language)
    assert oracle_manifest(task) == [f"files/{Path(example).name}|{solution}"]
