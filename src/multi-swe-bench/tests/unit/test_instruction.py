from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from multi_swe_bench_adapter.adapter import MultiSWEBenchAdapter


@pytest.mark.parametrize(
    ("issue_fields", "expected_description"),
    [
        (
            {
                "resolved_issues": [
                    {"title": "User-facing problem", "body": "Expected behavior"}
                ]
            },
            "# User-facing problem\n\nExpected behavior",
        ),
        (
            {
                "resolved_issues": [
                    {"title": "First problem", "body": "First requirement"},
                    {"title": "Second problem", "body": "Second requirement"},
                ]
            },
            ("# First problem\n\nFirst requirement"),
        ),
        ({}, "# PR title\n\nPR body"),
        ({"resolved_issues": [None, {}]}, "# PR title\n\nPR body"),
        (
            {"title": None, "body": None, "resolved_issues": None},
            "# Unknown Title\n\nNo description provided",
        ),
        (
            {
                "resolved_issues": [
                    {
                        "title": "Literal {language}",
                        "body": "Preserve {repo_dir}, {base_commit}, and {issue_urls}",
                    }
                ]
            },
            (
                "# Literal {language}\n\n"
                "Preserve {repo_dir}, {base_commit}, and {issue_urls}"
            ),
        ),
    ],
)
def test_generated_instruction_uses_issue_description_without_pr_metadata(
    tmp_path: Path,
    issue_fields: dict[str, Any],
    expected_description: str,
) -> None:
    record = {
        "org": "example-org",
        "repo": "example-repo",
        "number": 98765,
        "language": "python",
        "title": "PR title",
        "body": "PR body",
        "base": {"sha": "abc123"},
        **issue_fields,
    }
    dataset_path = tmp_path / "dataset"
    language_path = dataset_path / "python"
    language_path.mkdir(parents=True)
    (language_path / "python_dataset.jsonl").write_text(json.dumps(record) + "\n")
    task_path = tmp_path / "task"
    adapter = MultiSWEBenchAdapter(task_dir=task_path, dataset_path=dataset_path)

    loaded_record = adapter._index["example-org__example-repo-98765"]
    adapter._create_instruction(
        loaded_record, task_path, {"repo_dir": "/workspace/project"}
    )

    instruction = (task_path / "instruction.md").read_text()
    assert (
        f"<issue_description>\n{expected_description}\n</issue_description>"
        in instruction
    )
    assert "## Repository Information" not in instruction
    assert "## Related Issues" not in instruction
    assert "example-org/example-repo" not in instruction
    assert "98765" not in instruction
    assert "https://github.com/" not in instruction
    assert "I've uploaded a Python code repository" in instruction
    assert "directory /workspace/project" in instruction
    assert "`pytest`" in instruction
    assert "compare your changes with the base commit abc123" in instruction
