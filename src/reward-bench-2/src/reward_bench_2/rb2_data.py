"""Load the pinned RewardBench 2 test split.

Source: Hugging Face dataset allenai/reward-bench-2 (ODC-BY), one parquet file
with 1,865 rows. The revision is a commit, so task ids, answer positions and
oracle solutions are reproducible.
"""

from __future__ import annotations

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

DATASET = "allenai/reward-bench-2"
REVISION = "7ff08853b0d5686e79b13fda8677024f566a104a"
FILENAME = "data/test-00000-of-00001.parquet"
EXPECTED_ROWS = 1865


def load_rows() -> list[dict]:
    """Return the test split in file order.

    The official harness assigns answer positions by row order, so the order
    returned here must stay the file order.
    """
    path = hf_hub_download(DATASET, FILENAME, repo_type="dataset", revision=REVISION)
    rows = pq.read_table(path).to_pylist()
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"expected {EXPECTED_ROWS} rows, found {len(rows)}")
    return rows
