"""Load PRBench records from a pinned Hugging Face revision.

Source: ScaleAI/PRBench (CC-BY-4.0). Each split is one parquet file; the hard
splits repeat rows of the base splits and differ only in the canary column.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import urllib.request
from pathlib import Path

REPO = "ScaleAI/PRBench"
REVISION = "01947c6ef913df518e6e52626a7b29f132b12d4f"
SHA256 = {
    "finance": "4e3d790d00eefdc9ec7090a2e19459e74c1788921c525b3d14ed0dbf1f155dc0",
    "legal": "3d80ca175eaf5072880f33fd28e74a610ab99cc74e6c5b173d89312365a7c67f",
    "finance_hard": "5218eeb81834b8d0786b0b1ee414dbf7f563fcd01f355420d48376614af04b2a",
    "legal_hard": "9eebf855786e9b7c719ae80ad58fab0aba184b5406944e8c5b9d9cebcdb32895",
}
MAX_TURNS = 10


def filename(split: str) -> str:
    return f"{split}-00000-of-00001.parquet"


def data_url(split: str) -> str:
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    return f"{endpoint}/datasets/{REPO}/resolve/{REVISION}/data/{filename(split)}"


def download(url: str, attempts: int = 3) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except OSError as exc:
            error = exc
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise SystemExit(f"download failed after {attempts} attempts: {url}: {error}")


def load_rows(split: str, source_dir: Path | None = None) -> list[dict]:
    """Return the records of one split; source_dir may hold the pinned files."""
    import pyarrow.parquet as pq

    origin = Path(source_dir) / filename(split) if source_dir else data_url(split)
    data = origin.read_bytes() if source_dir else download(origin)
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256[split]:
        raise SystemExit(
            f"sha256 mismatch for {origin}: got {digest}, expected {SHA256[split]} "
            f"(revision {REVISION}); refusing to build tasks from different data"
        )
    return pq.read_table(io.BytesIO(data)).to_pylist()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def build_messages(row: dict, reference_texts: str = "prepend") -> list[dict]:
    """The conversation up to and including the final user turn.

    Follows process_row_get_convo and process_reference_texts in upstream
    evals.py / util.py: turns with no text are skipped, and reference texts go
    in front of the prompt of their turn. With reference_texts="omit" they are
    left out, which is what upstream commit 7b2df0a does in practice (see the
    adapter README).
    """
    messages = []
    for i in range(MAX_TURNS):
        prompt = _text(row.get(f"prompt_{i}"))
        if prompt:
            references = row.get(f"reference_texts_{i}") or []
            if references and reference_texts == "prepend":
                body = "".join(
                    f"Reference Text {n}:\n{text}\n\n"
                    for n, text in enumerate(references)
                )
                prompt = body + prompt
            messages.append({"role": "user", "content": prompt})
        response = _text(row.get(f"response_{i}"))
        if response:
            messages.append({"role": "assistant", "content": response})
    return messages


def rubric_items(row: dict) -> list[dict]:
    """Criterion text, weight and category, as criteria.py reads them."""
    items = []
    for criterion in row["rubric"]:
        annotations = criterion["annotations"]
        weight_key = annotations["weight_class"].replace(" ", "_") + "_weight"
        items.append(
            {
                "id": criterion["id"],
                "title": criterion["title"],
                "weight": annotations[weight_key],
                "weight_class": annotations["weight_class"],
                "category": annotations["criteria_category"],
            }
        )
    return items
