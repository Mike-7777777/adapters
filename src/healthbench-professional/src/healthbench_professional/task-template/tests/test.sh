#!/bin/bash
set -euo pipefail
pip install -q "openai>=1.0.0"
# grader.py writes /logs/verifier/reward.json and /logs/verifier/grading.json.
python3 /tests/grader.py
