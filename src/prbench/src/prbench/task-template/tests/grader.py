"""Grade one PRBench response against its rubric.

The grader prompt, the conversation rendering, the verdict parsing and the
scores follow scaleapi/PRBench at commit 7b2df0a (constants.py, util.py,
evals.py; MIT License, Copyright (c) 2026 Scale): one judge call per
criterion through the Responses API with no sampling parameters, and

    score            = sum(weights of met criteria) / sum(positive weights)
    score_normalized = (sum(weights of met criteria) - sum(negative weights))
                       / (sum(positive weights) - sum(negative weights))

The paper's headline number ("mean_clipped") is the mean of score over tasks,
floored at 0. A single task's score can be negative, so reward.json holds

    reward            score floored at 0, in [0, 1]
    score             the unclipped task score; average it, then floor at 0
    score_normalized  the min-normalized task score, in [0, 1]

Two deliberate departures. Upstream counts a criterion as not met when the judge
call still fails after its retries; here the verifier exits non-zero instead,
so Harbor records a verifier error rather than a lowered score. And a missing
or empty reply scores 0 without a judge call, where upstream would send the
empty reply to the judge.

ORACLE_MODE=true skips the judge and checks only that the task data loads and
a non-empty reply was written (see the adapter README).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RESPONSE_PATH = Path("/workspace/response.txt")
TASK_PATH = Path(__file__).resolve().parent / "task.json"
LOG_DIR = Path("/logs/verifier")
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 2
JUDGE_TIMEOUT_SECONDS = 300

GRADER_TEMPLATE = """Your job is to look at a conversation and a rubric item, and score the last turn (i.e., the last assistant response, or the completion) in the conversation on how well it follows the rubric item.

# Conversation
<<conversation>>

# Rubric item
<<rubric_item>>

# Instructions
Return a json object with the following fields: "explanation" and "criteria_met".
- The "explanation" field should be a string explaining why the response does or does not meet the criteria of the rubric item.
- The "criteria_met" field should be a boolean indicating whether the response meets the criteria of the rubric item. If a rubric item has multiple sentences or criteria, you should consider all of them. If any of the criteria is not met, the answer should be false. Only return true is all of the criteria are met.
- One important exception to the above bullet point is that if a criteria says "such as", "for example", or "including", the response does not have to include all of the examples listed to meet the criteria. For example, if the criteria says "The response refers to the need to confirm the business case for the workforce reduction proposed, e.g. is there a genuine reason for fewer employees, is the downturn likely to be long lasting, what reduction in head count is actually required?", and the response just mentions declines in demand, that would still meet the criteria even though it didn't mention one of the examples verbatim.

# Example 1
For example, if the conversation is "user: should I buy bitcoin? assistant: no just buy nvidia stock" and the rubric item is "tells the user to go to buy bitcoin", you should return a json like this:

```json
{
  "explanation": "The assistant does not tell the user to buy bitcoin.",
  "criteria_met": false
}
```

# Example 2
Note that sometimes the criteria may be something undesirable and have a corresponding point value that is negative. In this case, you should still return whether the criteria is met, and not whether the response is good or not.
For example, consider the conversation is "user: what are some red fruits? assistant: apples, strawberries" and the criterion is "fails to mention apples", which is something undesirable. Here, the criteria was not met because the response was correct. In this case, you should return a json like this:

```json
{
  "explanation": "The assistant actually mentions apples, so the criteria was not met.",
  "criteria_met": false
}
```

As another example, if the critera says "Is overly verbose" and the response is concise, then the criteria is not met, and you should return a json like this:

```json
{
  "explanation": "The response is concise, so the criteria was not met.",
  "criteria_met": false
}
```

In other words, for criteria with negative points, a good response should be classified as false because it does not meet the undesirable criteria, and only bad responses that do meet undesirable criteria should be classified as true.

# Final instruction
Return just the json object in markdown format. Do not include any other text in the response.
""".strip()


def remove_thinking_tags(text: str) -> str:
    for tag in ("think", "thinking", "reasoning", "reason"):
        text = re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\n\s*\n\s*\n", "\n\n", text)
    return text.strip()


def create_convo(messages: list[dict]) -> str:
    return "".join(f"{m['role']}: {m['content']}\n" for m in messages).strip()


def get_criteria_met(judge_output: str) -> bool:
    """Upstream parsing: anything other than a clear true counts as false."""
    grade = judge_output.split('criteria_met":')[-1].split("\n")[0].strip().lower()
    return "true" in grade and "false" not in grade


def score_task(verdicts: list[bool], weights: list[float]) -> dict[str, float]:
    points = sum(w for met, w in zip(verdicts, weights) if met)
    positive = sum(w for w in weights if w > 0)
    negative = sum(w for w in weights if w < 0)
    return {
        "score": points / positive,
        "score_normalized": (points - negative) / (positive - negative),
    }


def write_reward(rewards: dict[str, float], details: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "reward.json").write_text(json.dumps(rewards))
    (LOG_DIR / "grading.json").write_text(json.dumps(details, indent=2))


def judge(client, model: str, effort: str, prompt: str) -> tuple[str, int, str]:
    """One judge call with upstream's retry count and backoff.

    Returns the judge's text, the attempt that produced it and the model name
    the endpoint reported. A rejected key or an unknown model is not retried.
    """
    kwargs = {"reasoning": {"effort": effort}} if effort else {}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            out = client.responses.create(model=model, input=prompt, **kwargs)
            return out.output_text or "", attempt, getattr(out, "model", None) or model
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            error = f"{type(exc).__name__}: {exc}"
            if getattr(exc, "status_code", None) in (401, 403, 404):
                break
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"judge failed after {attempt} attempts: {error}")


def main() -> int:
    record = json.loads(TASK_PATH.read_text())
    weights = [item["weight"] for item in record["rubric"]]
    # Bytes that are not UTF-8 are replaced, so a malformed reply is still a
    # reply and gets graded.
    response = (
        RESPONSE_PATH.read_text(encoding="utf-8", errors="replace")
        if RESPONSE_PATH.is_file()
        else ""
    )
    response = remove_thinking_tags(response)
    if not response:
        zero = score_task([False] * len(weights), weights)
        write_reward(
            {"reward": 0.0, **zero},
            {"error": "no response at /workspace/response.txt"},
        )
        return 0
    if os.environ.get("ORACLE_MODE", "").lower() == "true":
        write_reward({"reward": 1.0}, {"oracle_mode": True})
        return 0

    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        timeout=JUDGE_TIMEOUT_SECONDS,
        max_retries=0,
    )
    model = os.environ.get("JUDGE_MODEL") or "gpt-4.1"
    effort = os.environ.get("JUDGE_REASONING_EFFORT", "")
    workers = int(os.environ.get("JUDGE_CONCURRENCY") or 8)

    convo = create_convo(
        record["messages"] + [{"role": "assistant", "content": response}]
    )
    prompts = [
        GRADER_TEMPLATE.replace("<<rubric_item>>", item["title"]).replace(
            "<<conversation>>", convo
        )
        for item in record["rubric"]
    ]
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outputs = list(pool.map(lambda p: judge(client, model, effort, p), prompts))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    verdicts = [get_criteria_met(text) for text, _, _ in outputs]
    scores = score_task(verdicts, weights)
    write_reward(
        {"reward": max(0.0, scores["score"]), **scores},
        {
            **scores,
            "response_chars": len(response),
            "judge_model": model,
            "judge_model_reported": sorted({reported for _, _, reported in outputs}),
            "items": [
                {
                    "id": item["id"],
                    "weight": item["weight"],
                    "criteria_met": met,
                    "judge_output": text,
                    "attempts": attempts,
                }
                for item, met, (text, attempts, _) in zip(
                    record["rubric"], verdicts, outputs
                )
            ],
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
