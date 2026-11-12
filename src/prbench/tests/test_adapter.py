from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
import types
from pathlib import Path

import pytest

PKG = Path(__file__).parents[1] / "src" / "prbench"
sys.path.insert(0, str(PKG.parent))
sys.path.insert(0, str(PKG / "task-template" / "tests"))

import grader

from prbench import adapter as adapter_module
from prbench import dataset


def criterion(title: str, weight_class: str, weight: int) -> dict:
    annotations = {
        "criteria_category": "Practical Utility",
        "weight_class": weight_class,
        weight_class.replace(" ", "_") + "_weight": weight,
    }
    return {"id": f"id-{title}", "title": title, "annotations": annotations}


def fake_row(task: str, split: str, topic: str = "Corporate Finance") -> dict:
    return {
        "task": task,
        "topic": topic,
        "canary": f"prbench:sample:{split}:{task}",
        "prompt_0": "FIRST-PROMPT",
        "response_0": "FIRST-RESPONSE",
        "reference_texts_1": ["REFERENCE-A", "REFERENCE-B"],
        "prompt_1": "SECOND-PROMPT",
        "rubric": [
            criterion("mentions X", "critically important", 10),
            criterion("mentions Y", "slightly important", 2),
            criterion("invents a statute", "detrimental", -6),
        ],
    }


@pytest.fixture
def fake_data(monkeypatch):
    """finance: f1 (hard), f2; legal: l1 (hard), l2."""

    def load_rows(split, source_dir=None):
        domain = split.removesuffix("_hard")
        ids = (
            [domain[0] + "1"]
            if split.endswith("_hard")
            else [domain[0] + n for n in "12"]
        )
        return [fake_row(task, split) for task in ids]

    monkeypatch.setattr(adapter_module.dataset, "load_rows", load_rows)


def generate(tmp_path: Path, **kwargs) -> Path:
    out = tmp_path / "out"
    adapter_module.PRBenchAdapter(out, **kwargs).run()
    return out


# --- dataset.py ---------------------------------------------------------------


def test_messages_skip_empty_turns_and_end_with_the_user():
    row = {"prompt_0": "a", "response_0": "b", "prompt_1": None, "response_1": " "}
    row |= {"prompt_2": "c"}
    assert dataset.build_messages(row) == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]


def test_reference_texts_go_in_front_of_their_turn():
    messages = dataset.build_messages(fake_row("f1", "finance"))
    assert messages[2]["content"] == (
        "Reference Text 0:\nREFERENCE-A\n\nReference Text 1:\nREFERENCE-B\n\n"
        "SECOND-PROMPT"
    )
    omitted = dataset.build_messages(fake_row("f1", "finance"), "omit")
    assert omitted[2]["content"] == "SECOND-PROMPT"


def test_rubric_weight_comes_from_the_weight_class_column():
    items = dataset.rubric_items(fake_row("f1", "finance"))
    assert [i["weight"] for i in items] == [10, 2, -6]
    assert items[0]["category"] == "Practical Utility"


def test_load_rows_refuses_data_that_differs_from_the_pin(tmp_path):
    (tmp_path / dataset.filename("finance")).write_bytes(b"not the pinned file")
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        dataset.load_rows("finance", tmp_path)


# --- adapter.py ---------------------------------------------------------------


def test_full_split_writes_every_task_with_registry_names(tmp_path, fake_data):
    out = generate(tmp_path)
    assert sorted(p.name for p in out.iterdir()) == [
        "finance-f1",
        "finance-f2",
        "legal-l1",
        "legal-l2",
    ]
    toml = tomllib.loads((out / "finance-f1" / "task.toml").read_text())
    assert toml["task"]["name"] == "scaleai/prbench__finance-f1"
    assert toml["task"]["authors"][0] == {"name": "Afra Feyza Akyürek"}
    assert toml["metadata"] == {
        "difficulty": "hard",
        "category": "finance",
        "source_id": "f1",
        "topic": "Corporate Finance",
        "turns": 2,
        "hard_subset": True,
    }
    assert "hard" in toml["task"]["keywords"]
    easy = tomllib.loads((out / "finance-f2" / "task.toml").read_text())
    assert easy["metadata"]["difficulty"] == "medium"


@pytest.mark.parametrize(
    ("split", "expected"),
    [
        ("finance", ["finance-f1", "finance-f2"]),
        ("legal_hard", ["legal-l1"]),
        ("hard", ["finance-f1", "legal-l1"]),
    ],
)
def test_domain_and_hard_splits(tmp_path, fake_data, split, expected):
    out = generate(tmp_path, split=split)
    assert sorted(p.name for p in out.iterdir()) == expected


def test_task_files_hold_the_conversation_rubric_and_canaries(tmp_path, fake_data):
    task = generate(tmp_path) / "finance-f1"
    conversation = (task / "environment" / "conversation.md").read_text()
    assert conversation.startswith("===== USER (message 1 of 3) =====\n\nFIRST-PROMPT")
    assert "===== ASSISTANT (message 2 of 3) =====\n\nFIRST-RESPONSE" in conversation
    assert "REFERENCE-B\n\nSECOND-PROMPT" in conversation
    record = json.loads((task / "tests" / "task.json").read_text())
    assert [m["role"] for m in record["messages"]] == ["user", "assistant", "user"]
    assert [i["weight"] for i in record["rubric"]] == [10, 2, -6]
    canaries = ["prbench:sample:finance:f1", "prbench:sample:finance_hard:f1"]
    assert record["canaries"] == canaries
    instruction = (task / "instruction.md").read_text()
    assert all(f"<!-- canary: {c} -->" in instruction for c in canaries)
    # The rubric must stay out of everything the agent can read.
    visible = instruction + conversation + (task / "task.toml").read_text()
    assert "mentions X" not in visible and "invents a statute" not in visible


def test_task_ids_limit_and_overwrite(tmp_path, fake_data, capsys):
    out = generate(tmp_path, task_ids=["legal-l2", "finance-f2"], limit=1)
    assert [p.name for p in out.iterdir()] == ["finance-f2"]
    marker = out / "finance-f2" / "marker"
    marker.write_text("kept")
    capsys.readouterr()
    adapter_module.PRBenchAdapter(out, task_ids=["finance-f2"]).run()
    assert marker.exists()
    assert "wrote 0 tasks, skipped 1 existing" in capsys.readouterr().out
    adapter_module.PRBenchAdapter(out, task_ids=["finance-f2"], overwrite=True).run()
    assert not marker.exists()


def test_bytecode_caches_in_the_template_are_not_copied(
    tmp_path, fake_data, monkeypatch
):
    template = tmp_path / "task-template"
    shutil.copytree(
        adapter_module.TEMPLATE_DIR,
        template,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    cache = template / "tests" / "__pycache__"
    cache.mkdir()
    (cache / "grader.cpython-313.pyc").write_bytes(b"cache")
    (template / "tests" / "stray.pyc").write_bytes(b"cache")
    monkeypatch.setattr(adapter_module, "TEMPLATE_DIR", template)

    out = generate(tmp_path, task_ids=["finance-f2"])

    leaked = [
        p for p in out.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc"
    ]
    assert leaked == []
    assert (out / "finance-f2" / "tests" / "grader.py").is_file()


def run_solve(task_dir: Path, workspace: Path) -> str:
    script = (task_dir / "solution" / "solve.sh").read_text()
    script = script.replace("/workspace", str(workspace))
    subprocess.run(["bash", "-c", script], check=True)
    return (workspace / "response.txt").read_text()


def test_oracle_reply_round_trips_through_solve_sh(tmp_path, fake_data):
    oracle_dir = tmp_path / "oracle"
    oracle_dir.mkdir()
    reply = "It's a reply with 'quotes', $VARS and\nnew lines — ünïcode.\n"
    (oracle_dir / "finance-f1.txt").write_text(reply)
    out = generate(tmp_path, oracle_dir=oracle_dir)
    assert run_solve(out / "finance-f1", tmp_path / "w1") == reply


def test_default_oracle_is_an_outline_of_the_positive_criteria(tmp_path, fake_data):
    out = generate(tmp_path)
    reply = run_solve(out / "finance-f2", tmp_path / "w2")
    assert "- mentions X\n- mentions Y\n" in reply
    assert "invents a statute" not in reply
    assert reply.endswith("End of outline.\n")


def strata_tasks(sizes: dict[tuple[bool, str], int]) -> list[dict]:
    return [
        {"id": f"{'h' if hard else 'b'}-{topic}-{n}", "hard": hard, "topic": topic}
        for (hard, topic), count in sizes.items()
        for n in range(count)
    ]


def test_parity_seats_follow_stratum_sizes():
    tasks = strata_tasks(
        {(False, "a"): 30, (False, "b"): 7, (True, "a"): 50, (True, "b"): 13}
    )
    keys = adapter_module.select_parity_ids(tasks, size=20)
    counts: dict[str, int] = {}
    for key in keys:
        stratum = key.rsplit("-", 1)[0]
        counts[stratum] = counts.get(stratum, 0) + 1
    # Quotas 6.0, 1.4, 10.0, 2.6: the last seat goes to the largest remainder.
    assert counts == {"b-a": 6, "b-b": 1, "h-a": 10, "h-b": 3}
    assert keys == adapter_module.select_parity_ids(list(reversed(tasks)), size=20)


def test_parity_split_generates_only_the_listed_tasks(tmp_path, fake_data, monkeypatch):
    listed = tmp_path / "parity_task_ids.txt"
    monkeypatch.setattr(adapter_module, "PARITY_SIZE_PER_DOMAIN", 1)
    monkeypatch.setattr(adapter_module, "PARITY_IDS_FILE", listed)
    tasks = adapter_module.PRBenchAdapter(tmp_path).load_tasks()
    expected = [
        key
        for domain in adapter_module.DOMAINS
        for key in adapter_module.select_parity_ids(
            [t for t in tasks if t["domain"] == domain], size=1
        )
    ]
    listed.write_text("\n".join(expected) + "\n")
    out = generate(tmp_path, split="parity")
    assert sorted(p.name for p in out.iterdir()) == sorted(expected)

    listed.write_text(expected[0] + "\n")
    with pytest.raises(RuntimeError, match="parity subset differs"):
        adapter_module.PRBenchAdapter(tmp_path / "o2", split="parity").run()


def test_committed_parity_list_has_100_unique_tasks_per_domain():
    keys = adapter_module.PARITY_IDS_FILE.read_text().split()
    assert len(keys) == len(set(keys))
    for domain in ("finance", "legal"):
        in_domain = [k for k in keys if k.startswith(domain + "-")]
        assert len(in_domain) == adapter_module.PARITY_SIZE_PER_DOMAIN


def test_unknown_task_ids_are_reported(tmp_path, fake_data):
    with pytest.raises(ValueError, match="finance-zz"):
        generate(tmp_path, task_ids=["finance-f1", "finance-zz"])


# --- tests/grader.py ----------------------------------------------------------


@pytest.mark.parametrize(
    ("judge_output", "met"),
    [
        ('```json\n{\n  "explanation": "ok",\n  "criteria_met": true\n}\n```', True),
        ('{"explanation": "no", "criteria_met": false}', False),
        ('"criteria_met": True', True),
        ('"criteria_met": true or false', False),
        ("I cannot grade this.", False),
        ("", False),
    ],
)
def test_verdict_parsing(judge_output, met):
    assert grader.get_criteria_met(judge_output) is met


def test_scores_use_positive_weights_as_the_denominator():
    weights = [10, 2, -6]
    assert grader.score_task([True, True, False], weights) == {
        "score": 1.0,
        "score_normalized": 1.0,
    }
    worst = grader.score_task([False, False, True], weights)
    assert worst == {"score": -0.5, "score_normalized": 0.0}
    mixed = grader.score_task([True, False, True], weights)
    assert mixed["score"] == pytest.approx(4 / 12)
    assert mixed["score_normalized"] == pytest.approx(10 / 18)


def test_thinking_tags_are_removed_before_grading():
    text = "<think>plan</think>Answer\n\n\n\nmore <REASONING>x\ny</REASONING>"
    assert grader.remove_thinking_tags(text) == "Answer\n\nmore"


def test_conversation_is_rendered_as_upstream_does():
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a\n"},
    ]
    assert grader.create_convo(messages) == "user: q\nassistant: a"


class FakeJudge:
    """Stands in for the OpenAI client: a criterion is met when the reply quotes it."""

    def __init__(self, fail_times: int = 0):
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []
        self.fail_times = fail_times
        self.responses = self

    def create(self, model, input, **kwargs):
        self.prompts.append(input)
        self.kwargs.append({"model": model, **kwargs})
        if self.fail_times:
            self.fail_times -= 1
            raise TimeoutError("judge unavailable")
        item = input.split("# Rubric item\n")[1].split("\n\n# Instructions")[0]
        reply = input.split("# Conversation\n")[1].split("\n\n# Rubric item")[0]
        met = item in reply.rsplit("assistant: ", 1)[-1]
        text = json.dumps({"explanation": "fake", "criteria_met": met}, indent=2)
        return types.SimpleNamespace(
            output_text=f"```json\n{text}\n```", model=f"{model}-snapshot"
        )


@pytest.fixture
def graded_task(tmp_path, fake_data, monkeypatch):
    task = generate(tmp_path) / "finance-f1"
    judge = FakeJudge()
    monkeypatch.setattr(grader, "TASK_PATH", task / "tests" / "task.json")
    monkeypatch.setattr(grader, "RESPONSE_PATH", tmp_path / "response.txt")
    monkeypatch.setattr(grader, "LOG_DIR", tmp_path / "verifier")
    monkeypatch.setattr(grader.time, "sleep", lambda seconds: None)
    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=lambda **kwargs: judge)
    )
    for name in ("ORACLE_MODE", "JUDGE_MODEL", "JUDGE_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    return types.SimpleNamespace(
        judge=judge,
        response=tmp_path / "response.txt",
        reward=tmp_path / "verifier" / "reward.json",
        grading=tmp_path / "verifier" / "grading.json",
    )


def test_full_score_when_every_positive_criterion_is_met(graded_task):
    graded_task.response.write_text("<think>draft</think>mentions X and mentions Y")
    assert grader.main() == 0
    assert json.loads(graded_task.reward.read_text()) == {
        "reward": 1.0,
        "score": 1.0,
        "score_normalized": 1.0,
    }
    prompt = graded_task.judge.prompts[0]
    assert (
        "user: FIRST-PROMPT\nassistant: FIRST-RESPONSE\nuser: Reference Text 0:"
        in prompt
    )
    assert prompt.count("assistant: mentions X and mentions Y") == 1
    assert "<think>" not in prompt
    # Upstream sends the judge no sampling or reasoning parameters.
    assert graded_task.judge.kwargs[0] == {"model": "gpt-4.1"}
    grading = json.loads(graded_task.grading.read_text())
    assert [i["criteria_met"] for i in grading["items"]] == [True, True, False]
    assert grading["judge_model_reported"] == ["gpt-4.1-snapshot"]


def test_negative_score_keeps_its_sign_in_score_and_floors_in_reward(graded_task):
    graded_task.response.write_text("invents a statute")
    assert grader.main() == 0
    assert json.loads(graded_task.reward.read_text()) == {
        "reward": 0.0,
        "score": -0.5,
        "score_normalized": 0.0,
    }


def test_empty_response_scores_zero_without_calling_the_judge(graded_task):
    graded_task.response.write_text("<think>only thoughts</think>\n")
    assert grader.main() == 0
    rewards = json.loads(graded_task.reward.read_text())
    assert rewards["reward"] == 0.0 and rewards["score"] == 0.0
    assert rewards["score_normalized"] == pytest.approx(6 / 18)
    assert graded_task.judge.prompts == []


def test_reply_that_is_not_utf8_is_still_graded(graded_task):
    graded_task.response.write_bytes(b"mentions X \xff\xfe and mentions Y")
    assert grader.main() == 0
    assert json.loads(graded_task.reward.read_text())["reward"] == 1.0


def test_judge_model_and_effort_come_from_the_environment(graded_task, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("JUDGE_REASONING_EFFORT", "low")
    graded_task.response.write_text("mentions X")
    assert grader.main() == 0
    assert graded_task.judge.kwargs[0] == {
        "model": "gpt-5.6-terra",
        "reasoning": {"effort": "low"},
    }


def test_transient_judge_errors_are_retried(graded_task):
    graded_task.judge.fail_times = grader.MAX_RETRIES - 1
    graded_task.response.write_text("mentions X and mentions Y")
    assert grader.main() == 0
    assert json.loads(graded_task.reward.read_text())["reward"] == 1.0


def test_empty_judge_output_counts_as_not_met_as_upstream(graded_task):
    graded_task.judge.create = lambda model, input, **kwargs: (
        graded_task.judge.prompts.append(input)
        or types.SimpleNamespace(output_text="", model=model)
    )
    graded_task.response.write_text("mentions X and mentions Y")
    assert grader.main() == 0
    assert json.loads(graded_task.reward.read_text())["score"] == 0.0
    assert len(graded_task.judge.prompts) == 3


def test_rejected_key_or_model_is_not_retried(graded_task):
    def reject(model, input, **kwargs):
        graded_task.judge.prompts.append(input)
        error = RuntimeError("model not found")
        error.status_code = 404
        raise error

    graded_task.judge.create = reject
    graded_task.response.write_text("mentions X")
    assert grader.main() == 1
    assert not graded_task.reward.exists()
    assert len(graded_task.judge.prompts) == 3


def test_exhausted_judge_retries_fail_the_verifier_without_a_reward(graded_task):
    graded_task.judge.fail_times = 10**6
    graded_task.response.write_text("mentions X")
    assert grader.main() == 1
    assert not graded_task.reward.exists()


def test_oracle_mode_checks_the_reply_without_calling_the_judge(
    graded_task, monkeypatch
):
    monkeypatch.setenv("ORACLE_MODE", "true")
    assert grader.main() == 0
    assert json.loads(graded_task.reward.read_text())["reward"] == 0.0
    graded_task.response.write_text("any reply")
    assert grader.main() == 0
    assert json.loads(graded_task.reward.read_text()) == {"reward": 1.0}
    assert graded_task.judge.prompts == []
