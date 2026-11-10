"""Load the pinned RewardBench 2 test split.

Source: Hugging Face dataset allenai/reward-bench-2 (ODC-BY), one parquet file
with 1,865 rows. The revision and the file's sha256 are pinned so that task
ids, answer positions and oracle solutions are reproducible.
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq

DATASET = "allenai/reward-bench-2"
REVISION = "7ff08853b0d5686e79b13fda8677024f566a104a"
FILENAME = "data/test-00000-of-00001.parquet"
SHA256 = "c8ec60efbd75d2f9dcba4121e6101f7a6015abc38a34e034ae2c7ae886265958"
EXPECTED_ROWS = 1865
DOWNLOAD_ATTEMPTS = 3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_path() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "harbor-reward-bench-2" / f"{SHA256}.parquet"


def _download(target: Path) -> None:
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    url = f"{endpoint}/datasets/{DATASET}/resolve/{REVISION}/{FILENAME}"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                partial.write_bytes(response.read())
            partial.replace(target)
            return
        except OSError as error:
            last_error = error
            time.sleep(2 * attempt)
    raise RuntimeError(
        f"download failed after {DOWNLOAD_ATTEMPTS} attempts: {url} ({last_error}). "
        "Set HF_ENDPOINT to a reachable mirror, or pass --source with a local copy."
    )


def load_rows(source: str | Path | None = None) -> list[dict]:
    """Return the test split in file order.

    The official harness assigns answer positions by row order, so the order
    returned here must stay the file order.
    """
    if source is not None:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"--source file not found: {path}")
    else:
        path = _cache_path()
        if not path.is_file():
            _download(path)
    actual = _sha256(path)
    if actual != SHA256:
        raise RuntimeError(
            f"sha256 mismatch for {path}: got {actual}, expected {SHA256} "
            f"({DATASET}@{REVISION[:8]}). Delete the file or fix --source."
        )
    rows = pq.read_table(path).to_pylist()
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"expected {EXPECTED_ROWS} rows, found {len(rows)}")
    return rows
