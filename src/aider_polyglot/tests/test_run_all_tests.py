"""Tests for which Exercism tests the verifier runs. They need no network and no Docker."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aider_polyglot.adapter import AiderPolyglotAdapter
from aider_polyglot import utils

# The sed call in the test.sh template that re-enables disabled Java tests.
JAVA_SED = re.compile(r"sed -i -E '(s/@Disabled[^']*)'")


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


def enable_java_tests(source: str) -> str:
    """Apply the test.sh template's sed program to a Java test source."""
    template = Path(utils.__file__).parent / "task-template" / "tests" / "test.sh"
    program = JAVA_SED.search(template.read_text()).group(1)
    return subprocess.run(
        ["sed", "-E", program],
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
def test_java_tests_disabled_with_a_reason_are_enabled(annotation: str) -> None:
    source = f"    {annotation}\n    @Test\n    public void second() {{}}\n"
    assert "@Disabled" not in enable_java_tests(source)


def test_java_bare_disabled_stays_as_in_aiders_harness() -> None:
    source = "    @Disabled\n    @Test\n    public void slow() {}\n"
    assert enable_java_tests(source) == source


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


def test_javascript_enables_xtest_only_as_aiders_harness_does() -> None:
    # The program uses \b, which BSD sed (macOS) lacks; the task image has GNU sed.
    sed = shutil.which("gsed") or "sed"
    if subprocess.run([sed, "--version"], capture_output=True, check=False).returncode:
        pytest.skip("needs GNU sed")
    program = re.search(
        r"sed -i '([^']*)'", utils.get_test_command_with_test_dir("javascript")
    ).group(1)
    source = "xtest('a', f);\nxit('b', f);\ntest.skip('c', f);\n"
    enabled = subprocess.run(
        [sed, program], input=source, capture_output=True, text=True, check=True
    ).stdout
    assert enabled == "test('a', f);\nxit('b', f);\ntest.skip('c', f);\n"
