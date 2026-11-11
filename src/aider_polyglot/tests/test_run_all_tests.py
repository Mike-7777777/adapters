"""Generation tests for running every Exercism test. They need no network and no Docker."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from aider_polyglot.adapter import AiderPolyglotAdapter

# The sed call in tests/test.sh that re-enables disabled Java tests.
JAVA_SED = re.compile(r"sed -i( -E)? '(s/@Disabled[^']*)'")


def generate(tmp_path: Path, language: str, files: dict[str, str]) -> Path:
    """Generate one task from a one-exercise track; files maps a relative path to its text."""
    exercise = tmp_path / "polyglot" / language / "exercises" / "practice" / "demo"
    for rel, text in files.items():
        path = exercise / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (exercise / ".docs").mkdir()
    (exercise / ".docs" / "instructions.md").write_text("Do the thing.\n")
    adapter = AiderPolyglotAdapter(
        task_dir=tmp_path / "tasks",
        polyglot_repo_path=tmp_path / "polyglot",
        languages=language,
    )
    assert adapter.run(task_ids=[f"{language}_demo"]) == (1, 0)
    return tmp_path / "tasks" / f"polyglot_{language}_demo"


def config(solution: str, test: str, example: str) -> str:
    return json.dumps(
        {"files": {"solution": [solution], "test": [test], "example": [example]}}
    )


def enable_java_tests(tmp_path: Path, source: str) -> str:
    """Apply the generated test.sh's sed program to a Java test source."""
    task_dir = generate(
        tmp_path,
        "java",
        {
            ".meta/config.json": config(
                "src/main/java/Demo.java",
                "src/test/java/DemoTest.java",
                ".meta/src/reference/java/Demo.java",
            ),
            ".meta/src/reference/java/Demo.java": "class Demo {}\n",
            "src/main/java/Demo.java": "class Demo {}\n",
            "src/test/java/DemoTest.java": source,
        },
    )
    extended, program = JAVA_SED.search(
        (task_dir / "tests" / "test.sh").read_text()
    ).groups()
    flags = ["-E"] if extended else []
    return subprocess.run(
        ["sed", *flags, program],
        input=source,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


# Every form of the annotation in polyglot-benchmark's Java tests at 7e0611e.
@pytest.mark.parametrize(
    "annotation",
    [
        '@Disabled("Remove to run test")',
        '@Disabled("Remove to run")',
        '@Disabled("Remove to run test.")',
    ],
)
def test_java_tests_disabled_with_a_reason_are_enabled(
    tmp_path: Path, annotation: str
) -> None:
    source = f"    {annotation}\n    @Test\n    public void second() {{}}\n"
    assert "@Disabled" not in enable_java_tests(tmp_path, source)


def test_java_bare_disabled_stays_as_in_aiders_harness(tmp_path: Path) -> None:
    source = "    @Disabled\n    @Test\n    public void slow() {}\n"
    assert enable_java_tests(tmp_path, source) == source


def test_rust_tasks_ship_the_whole_tests_directory(tmp_path: Path) -> None:
    task_dir = generate(
        tmp_path,
        "rust",
        {
            ".meta/config.json": config(
                "src/lib.rs", "tests/demo.rs", ".meta/example.rs"
            ),
            ".meta/example.rs": "pub fn demo() {}\n",
            "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
            "src/lib.rs": "pub fn demo() {}\n",
            "tests/demo.rs": "#[test]\nfn first() {}\n",
            "tests/invalid/fixture.rs": "fn main() {}\n",
        },
    )
    assert (task_dir / "tests" / "tests" / "invalid" / "fixture.rs").is_file()
