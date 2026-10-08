#!/bin/bash
set -euo pipefail
pip install -q "openai>=1.0.0"
python3 /tests/grader.py
