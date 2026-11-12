#!/bin/bash
# Grades /app/answer.txt with the official GAIA scorer (question_scorer,
# vendored unchanged apart from one import in gaia_scorer.py next to this file).

set -euo pipefail

echo "=== STARTING GAIA TEST EXECUTION ==="

mkdir -p /logs/verifier
# Never trust a reward file that is already there.
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json

# The scorer receives the file contents unmodified, as the leaderboard receives
# the submitted answer; it ignores whitespace itself. Exit 0 = correct,
# 10 = incorrect, anything else = the verifier could not grade.
status=0
python3 -I - <<'PY' || status=$?
import runpy
import sys

# Run the scorer from its source by path: nothing else in /tests is imported,
# and no bytecode cache is read or written.
question_scorer = runpy.run_path("/tests/gaia_scorer.py")["question_scorer"]

with open("/tests/expected_answer.txt", encoding="utf-8") as f:
    expected_answer = f.read()
try:
    with open("/app/answer.txt", encoding="utf-8", errors="replace") as f:
        agent_answer = f.read()
except OSError as exc:
    print(f"Cannot read /app/answer.txt: {exc}")
    sys.exit(10)

print(f"Agent answer: {agent_answer!r}")
print(f"Expected answer: {expected_answer!r}")
sys.exit(0 if question_scorer(agent_answer, expected_answer) else 10)
PY

case "$status" in
    0)
        echo "✓ Test passed - answer is correct"
        echo 1 > /logs/verifier/reward.txt
        ;;
    10)
        echo "✗ Test failed - answer is incorrect"
        echo 0 > /logs/verifier/reward.txt
        ;;
    *)
        # No reward file, so Harbor reports a verifier error instead of a wrong answer.
        echo "ERROR: scorer exited with status $status; no reward written" >&2
        exit 1
        ;;
esac

echo "=== SCRIPT FINISHED ==="
exit 0
