"""Fetch HealthBench Professional records from a pinned Hugging Face revision.

The dataset asks that its examples not be republished in plain text, so tasks
store only a record id. This file is copied into each task's environment/ and
tests/ directories: the image build writes the conversation for the agent, and
the verifier reads the rubric from its own copy, which the agent cannot edit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = "openai/healthbench-professional"
REVISION = "349962fd46dd02343a0d8a606491baf59154ea1a"
FILENAME = "healthbench_professional_eval.jsonl"
SHA256 = "d44b08e6e952e04c945e2c406f02533d9e7a989a84e35820ee7efdff20c9e4e2"


def data_url() -> str:
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    return f"{endpoint}/datasets/{REPO}/resolve/{REVISION}/{FILENAME}"


def download(url: str, attempts: int = 3) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except OSError as exc:
            if attempt == attempts:
                raise SystemExit(
                    f"download failed after {attempts} attempts: {url}: {exc}"
                )
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def load_rows(source: str | None = None) -> list[dict]:
    """Return all records; source may be a local copy of the pinned file."""
    data = Path(source).read_bytes() if source else download(data_url())
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        raise SystemExit(
            f"sha256 mismatch for {source or data_url()}: got {digest}, expected {SHA256} "
            f"(revision {REVISION}); refusing to build or grade from different data"
        )
    # split("\n"), not splitlines(): records contain U+2028 inside JSON strings,
    # which splitlines() treats as a line break.
    return [json.loads(line) for line in data.decode().split("\n") if line.strip()]


def load_record(task_id: str) -> dict:
    for row in load_rows():
        if row["id"] == task_id:
            return row
    raise SystemExit(f"record {task_id} not found in {REPO}@{REVISION}")


def render_conversation(messages: list[dict]) -> str:
    return "\n\n".join(f"### {m['role']}\n\n{m['content']}" for m in messages)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: hbp_data.py TASK_ID OUTPUT_MD")
    record = load_record(sys.argv[1])
    Path(sys.argv[2]).write_text(
        render_conversation(record["conversation"]["messages"]) + "\n"
    )
