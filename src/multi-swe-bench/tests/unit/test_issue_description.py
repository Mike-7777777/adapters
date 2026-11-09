from __future__ import annotations

from typing import Any

import pytest

from multi_swe_bench_adapter.issue_description import resolve_issue_description_fields


def test_resolved_issue_description_takes_precedence_over_pull_request() -> None:
    title, body = resolve_issue_description_fields(
        {
            "title": "PR implementation details",
            "body": "This pull request changes internal.py",
            "resolved_issues": [
                {
                    "title": "  User-facing problem  ",
                    "body": "  Expected user-facing behavior  ",
                }
            ],
        }
    )

    assert title == "User-facing problem"
    assert body == "Expected user-facing behavior"


def test_single_resolved_issue_preserves_empty_fields() -> None:
    title, body = resolve_issue_description_fields(
        {
            "title": "  Pull request title  ",
            "resolved_issues": [{"title": None, "body": "   "}],
        }
    )

    assert title == ""
    assert body == ""


@pytest.mark.parametrize(
    ("issue", "expected"),
    [
        ({"title": "Issue title"}, ("Issue title", "")),
        ({"body": "Issue body"}, ("", "Issue body")),
    ],
)
def test_partial_issue_does_not_fall_back_to_pr_implementation_details(
    issue: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert (
        resolve_issue_description_fields(
            {
                "title": "PR title",
                "body": "PR implementation details",
                "resolved_issues": [issue],
            }
        )
        == expected
    )


def test_multiple_resolved_issues_use_only_the_first_issue() -> None:
    title, body = resolve_issue_description_fields(
        {
            "resolved_issues": [
                {"title": "First requirement", "body": "First body"},
                "not an issue",
                {"title": "", "body": ""},
                {"title": "Second requirement", "body": None},
                {"title": None, "body": "Body-only requirement"},
            ]
        }
    )

    assert title == "First requirement"
    assert body == "First body"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"title": "  PR title  ", "body": "  PR body  "},
            ("PR title", "PR body"),
        ),
        (
            {"title": None, "body": None, "resolved_issues": [None, {}]},
            ("Unknown Title", "No description provided"),
        ),
        (
            {"title": "PR title", "body": "PR body", "resolved_issues": None},
            ("PR title", "PR body"),
        ),
        ({"resolved_issues": []}, ("Unknown Title", "No description provided")),
    ],
)
def test_description_falls_back_when_no_resolved_issue_is_usable(
    record: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert resolve_issue_description_fields(record) == expected


@pytest.mark.parametrize("first_issue", [{}, {"title": None, "body": None}])
def test_empty_first_issue_does_not_select_a_later_issue(first_issue: dict) -> None:
    assert resolve_issue_description_fields(
        {"resolved_issues": [first_issue, {"title": "Later", "body": "Later body"}]}
    ) == ("", "")


def test_invalid_first_issue_does_not_select_a_later_issue() -> None:
    assert resolve_issue_description_fields(
        {
            "title": "PR title",
            "body": "PR body",
            "resolved_issues": [None, {"title": "Later"}],
        }
    ) == ("PR title", "PR body")
