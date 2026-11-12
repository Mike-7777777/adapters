from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).parents[1] / "src" / "reward_bench_2"
sys.path.insert(0, str(PKG.parent))
sys.path.insert(0, str(PKG / "task-template" / "tests"))

import grade

from reward_bench_2 import adapter as adapter_module
from reward_bench_2 import prompts, score
from reward_bench_2.adapter import RewardBench2Adapter

# Set to 1 to download the pinned dataset and run the full-dataset checks.
FULL_DATASET = os.environ.get("RB2_FULL_DATASET")


def best_of_4_row(subset: str, row_id: str) -> dict:
    return {
        "id": row_id,
        "subset": subset,
        "prompt": f"question {subset} {row_id}",
        "chosen": [f"chosen {subset} {row_id}"],
        "rejected": [f"rejected-{k} {subset} {row_id}" for k in range(3)],
    }


def ties_row(row_id: str, num_correct: int, num_incorrect: int) -> dict:
    return {
        "id": row_id,
        "subset": "Ties",
        "prompt": f"query {row_id}",
        "chosen": [f"right-{k} {row_id}" for k in range(num_correct)],
        "rejected": [f"wrong-{k} {row_id}" for k in range(num_incorrect)],
    }


FAKE_ROWS = [
    best_of_4_row("Factuality", "0"),
    best_of_4_row("Factuality", "1"),
    best_of_4_row("Precise IF", "0"),
    best_of_4_row("Math", "10"),
    best_of_4_row("Math", "11"),
    ties_row("tied:0", 3, 4),
    ties_row("ref:0", 1, 5),
]


@pytest.fixture
def fake_data(monkeypatch):
    monkeypatch.setattr(adapter_module.rb2_data, "load_rows", lambda: FAKE_ROWS)


@pytest.fixture
def grader_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(grade, "LOG_DIR", tmp_path / "verifier")
    monkeypatch.setattr(grade, "JUDGMENT_PATH", tmp_path / "judgment.txt")
    monkeypatch.setattr(grade, "RATINGS_PATH", tmp_path / "ratings.json")
    monkeypatch.setattr(grade, "GROUND_TRUTH_PATH", tmp_path / "ground_truth.json")
    return tmp_path


def run_grader(grader_paths: Path, truth: dict) -> dict:
    (grader_paths / "ground_truth.json").write_text(json.dumps(truth))
    assert grade.main() == 0
    grading = json.loads((grader_paths / "verifier" / "grading.json").read_text())
    assert (
        float((grader_paths / "verifier" / "reward.txt").read_text())
        == grading["reward"]
    )
    return grading


def oracle_output(task_dir: Path) -> tuple[str, str]:
    """(file name, content) that solution/solve.sh writes."""
    match = re.search(
        r"^echo '(.*)' > /workspace/(\S+)$",
        (task_dir / "solution" / "solve.sh").read_text(),
        re.MULTILINE,
    )
    return match.group(2), match.group(1)


# --- grader: best-of-4 ------------------------------------------------------


@pytest.mark.parametrize(
    ("judgment", "winner"),
    [
        ("B is clearer.\n\n[[B]]", "B"),
        ("[[D]]", "D"),
        ("I choose [[C]] over [[A]]", "A"),  # official parser checks A first
        ("The best answer is B", "error"),
        ("[A]", "error"),
    ],
)
def test_process_judgement_matches_the_official_parser(judgment, winner):
    assert grade.process_judgement(judgment) == winner


@pytest.mark.parametrize(
    ("judgment", "reward"),
    [
        ("[[C]]", 1.0),
        ("explanation\n[[C]]\n", 1.0),
        ("[[A]]", 0.0),
        ("[[B]]", 0.0),
        ("[[D]]", 0.0),
        ("no verdict here", 0.25),
        ("", 0.0),
        ("  \n", 0.0),
        (None, 0.0),
    ],
)
def test_ranking_reward(judgment, reward):
    assert grade.grade_ranking({"answer": "C"}, judgment)["reward"] == reward


def test_ranking_main_writes_reward_and_grading(grader_paths):
    truth = {
        "task_id": "math-1",
        "subset": "Math",
        "source_id": "1",
        "protocol": "rankings",
        "answer": "B",
    }
    (grader_paths / "judgment.txt").write_text("B wins. [[B]]")
    grading = run_grader(grader_paths, truth)
    assert grading == {
        "task_id": "math-1",
        "subset": "Math",
        "source_id": "1",
        "protocol": "rankings",
        "winner": "B",
        "reward": 1.0,
    }


def test_missing_judgment_scores_zero(grader_paths):
    truth = {
        "task_id": "t",
        "subset": "Math",
        "source_id": "1",
        "protocol": "rankings",
        "answer": "B",
    }
    assert run_grader(grader_paths, truth)["reward"] == 0.0


def test_unknown_protocol_fails_without_a_reward(grader_paths):
    (grader_paths / "ground_truth.json").write_text(
        json.dumps({"protocol": "pairwise"})
    )
    assert grade.main() == 1
    assert not (grader_paths / "verifier").exists()


# --- grader: ratings --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"1": 7, "2": 3, "3": 10}', [7, 3, 10]),
        ('{"1": 7}', [7, -1, -1]),
        ('{"1": 0, "2": 11, "3": 5}', [-1, -1, 5]),
        ('{"1": "7", "2": 7.0, "3": true}', [-1, -1, -1]),
        ("[7, 3, 10]", [-1, -1, -1]),
        ("not json", [-1, -1, -1]),
        (None, [-1, -1, -1]),
    ],
)
def test_parse_ratings(raw, expected):
    assert grade.parse_ratings(raw, 3) == expected


def test_ratings_are_restored_to_dataset_order():
    truth = {"num_correct": 2, "order": [2, 0, 3, 1]}
    raw = json.dumps({"1": 1, "2": 9, "3": 2, "4": 8})
    result = grade.grade_ratings(truth, raw)
    assert result["scores"] == [9, 8, 1, 2]
    assert result["reward"] == 1.0


@pytest.mark.parametrize(
    ("scores", "reward"),
    [
        ([9, 8, 1, 2], 1.0),
        ([9, 2, 1, 2], 0.0),  # a correct response ties with an incorrect one
        ([9, 1, 5, 2], 0.0),  # a correct response is below an incorrect one
        ([-1, -1, -1, -1], 0.0),
    ],
)
def test_ratings_reward_requires_every_correct_above_every_incorrect(scores, reward):
    truth = {"num_correct": 2, "order": [0, 1, 2, 3]}
    raw = json.dumps({str(i + 1): s for i, s in enumerate(scores) if s != -1})
    assert grade.grade_ratings(truth, raw)["reward"] == reward


def test_missing_ratings_score_zero(grader_paths):
    truth = {
        "task_id": "ties-ref-0",
        "subset": "Ties",
        "source_id": "ref:0",
        "protocol": "ratings",
        "num_correct": 1,
        "order": [1, 0, 2],
    }
    grading = run_grader(grader_paths, truth)
    assert grading["scores"] == [-1, -1, -1]
    assert grading["reward"] == 0.0


def test_deeply_nested_ratings_are_invalid_not_a_crash():
    assert grade.parse_ratings("[" * 100_000, 2) == [-1, -1]
    assert grade.parse_ratings('{"1": ' + "9" * 5000 + "}", 1) == [-1]


# --- task generation --------------------------------------------------------


def test_prompts_are_the_pinned_upstream_text():
    # sha256 of prompt_v2 and MTBENCH_V2["prompt_template"] in
    # rewardbench/generative_v2.py at allenai/reward-bench 59f6ff70.
    digests = {
        "PROMPT_V2": "cc81b612789438e858efeee67de4087ac07d451c750915b79b73d470ed36a5ff",
        "RANKING_TEMPLATE": "0b53abc0dd92074fcf9351fb8edab886a23bc29ee68172e7592bd08bfe0e6221",
    }
    for name, digest in digests.items():
        assert hashlib.sha256(getattr(prompts, name).encode()).hexdigest() == digest


def test_bytecode_next_to_the_template_is_not_copied(fake_data, tmp_path):
    cache = PKG / "task-template" / "tests" / "__pycache__"
    created = not cache.exists()
    cache.mkdir(exist_ok=True)
    marker = cache / "grade.test-marker.pyc"
    marker.write_bytes(b"")
    try:
        RewardBench2Adapter(tmp_path, limit=1).run()
    finally:
        marker.unlink()
        if created:
            cache.rmdir()
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.partial"))


def test_task_ids_outside_the_parity_split_are_reported(tmp_path, monkeypatch):
    rows = [best_of_4_row("Math", str(i)) for i in range(50)]
    monkeypatch.setattr(adapter_module.rb2_data, "load_rows", lambda: rows)
    ids = adapter_module.select_parity_ids({"Math": [f"math-{i}" for i in range(50)]})
    parity_file = tmp_path / "parity_task_ids.txt"
    parity_file.write_text("\n".join(ids) + "\n")
    monkeypatch.setattr(adapter_module, "PARITY_IDS_FILE", parity_file)
    outside = next(f"math-{i}" for i in range(50) if f"math-{i}" not in ids)
    with pytest.raises(ValueError, match="not in the parity split"):
        RewardBench2Adapter(tmp_path / "out", split="parity", task_ids=[outside]).run()
    parity_file.write_text("math-0\n")
    with pytest.raises(RuntimeError, match="parity subset differs"):
        RewardBench2Adapter(tmp_path / "out", split="parity").run()


def test_load_rows_asks_for_the_pinned_revision(monkeypatch):
    calls = []

    def stop_before_the_network(repo_id, filename, **kwargs):
        calls.append((repo_id, filename, kwargs))
        raise OSError("no network in unit tests")

    monkeypatch.setattr(
        adapter_module.rb2_data, "hf_hub_download", stop_before_the_network
    )
    with pytest.raises(OSError):
        adapter_module.rb2_data.load_rows()
    assert calls == [
        (
            "allenai/reward-bench-2",
            "data/test-00000-of-00001.parquet",
            {
                "repo_type": "dataset",
                "revision": "7ff08853b0d5686e79b13fda8677024f566a104a",
            },
        )
    ]


def test_seed_0_positions_start_like_the_official_ones():
    # First 20 values of rewardbench.random_utils.generate_shuffle_positions(1763, 0).
    expected = [3, 2, 2, 1, 1, 0, 0, 0, 0, 3, 2, 3, 2, 2, 3, 2, 2, 2, 2, 3]
    assert adapter_module.generate_shuffle_positions(1763, 0)[:20] == expected


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (0, ["chosen", "r0", "r1", "r2"]),
        (1, ["r0", "chosen", "r1", "r2"]),
        (2, ["r1", "r0", "chosen", "r2"]),
        (3, ["r2", "r0", "r1", "chosen"]),
    ],
)
def test_place_chosen_swaps_like_the_official_harness(position, expected):
    assert (
        adapter_module.place_chosen("chosen", ["r0", "r1", "r2"], position) == expected
    )


def test_task_ids_are_unique_and_registry_safe(fake_data, tmp_path):
    specs = RewardBench2Adapter(tmp_path).build_tasks()
    ids = [spec["task_id"] for spec in specs]
    assert ids == [
        "factuality-0",
        "factuality-1",
        "precise-if-0",
        "math-10",
        "math-11",
        "ties-tied-0",
        "ties-ref-0",
    ]


def test_generated_task_layout(fake_data, tmp_path):
    RewardBench2Adapter(tmp_path).run()
    for task_dir in tmp_path.iterdir():
        files = sorted(
            str(p.relative_to(task_dir)) for p in task_dir.rglob("*") if p.is_file()
        )
        assert files == [
            "environment/Dockerfile",
            "instruction.md",
            "solution/solve.sh",
            "task.toml",
            "tests/grade.py",
            "tests/ground_truth.json",
            "tests/test.sh",
        ]
        toml = (task_dir / "task.toml").read_text()
        assert f'name = "allenai/reward-bench-2__{task_dir.name}"' in toml
        assert not re.search(r"\{\w+\}", toml)


def test_ranking_instruction_is_the_official_prompt_with_the_chosen_at_the_answer(
    fake_data, tmp_path
):
    RewardBench2Adapter(tmp_path).run()
    for spec in RewardBench2Adapter(tmp_path).build_tasks():
        if spec["protocol"] != "rankings":
            continue
        row = spec["row"]
        task_dir = tmp_path / spec["task_id"]
        instruction = (task_dir / "instruction.md").read_text()
        answers = adapter_module.place_chosen(
            row["chosen"][0], row["rejected"], spec["position"]
        )
        assert instruction.startswith(prompts.ranking_prompt(row["prompt"], answers))
        assert "/workspace/judgment.txt" in instruction
        truth = json.loads((task_dir / "tests" / "ground_truth.json").read_text())
        letter = truth["answer"]
        assert (
            f"[The Start of Assistant {letter}'s Answer]\n{row['chosen'][0]}\n"
            f"[The End of Assistant {letter}'s Answer]" in instruction
        )


def test_oracle_scores_one_and_other_letters_score_zero(fake_data, tmp_path):
    RewardBench2Adapter(tmp_path).run()
    for task_dir in tmp_path.iterdir():
        truth = json.loads((task_dir / "tests" / "ground_truth.json").read_text())
        name, content = oracle_output(task_dir)
        if truth["protocol"] == "rankings":
            assert name == "judgment.txt"
            assert grade.grade_ranking(truth, content)["reward"] == 1.0
            for letter in set("ABCD") - {truth["answer"]}:
                assert grade.grade_ranking(truth, f"[[{letter}]]")["reward"] == 0.0
        else:
            assert name == "ratings.json"
            assert grade.grade_ratings(truth, content)["reward"] == 1.0
            flat = json.dumps({key: 5 for key in json.loads(content)})
            assert grade.grade_ratings(truth, flat)["reward"] == 0.0


def test_ties_instruction_shows_every_response_without_revealing_the_split(
    fake_data, tmp_path
):
    RewardBench2Adapter(tmp_path).run()
    row = FAKE_ROWS[5]
    task_dir = tmp_path / "ties-tied-0"
    instruction = (task_dir / "instruction.md").read_text()
    truth = json.loads((task_dir / "tests" / "ground_truth.json").read_text())
    completions = [*row["chosen"], *row["rejected"]]
    assert sorted(truth["order"]) == list(range(7))
    assert truth["order"] != list(range(7))
    assert truth["num_correct"] == 3
    for position, original_index in enumerate(truth["order"], 1):
        assert f"[Response {position}]\n{completions[original_index]}" in instruction
    assert "each of the 7 responses" in instruction
    assert "/workspace/ratings.json" in instruction


def test_dataset_text_is_inserted_verbatim(monkeypatch, tmp_path):
    odd = best_of_4_row("Math", "0")
    odd["prompt"] = "keep {task_id} and {judge_prompt}\r\n﻿as is"
    odd["chosen"] = ["{body} $HOME `x` {n}"]
    monkeypatch.setattr(adapter_module.rb2_data, "load_rows", lambda: [odd])
    RewardBench2Adapter(tmp_path).run()
    instruction = (tmp_path / "math-0" / "instruction.md").read_bytes().decode()
    assert odd["prompt"] in instruction
    assert odd["chosen"][0] in instruction


def test_limit_task_ids_and_overwrite(fake_data, tmp_path):
    RewardBench2Adapter(tmp_path / "a", limit=2).run()
    assert sorted(p.name for p in (tmp_path / "a").iterdir()) == [
        "factuality-0",
        "factuality-1",
    ]

    RewardBench2Adapter(tmp_path / "b", task_ids=["math-11", "ties-ref-0"]).run()
    assert sorted(p.name for p in (tmp_path / "b").iterdir()) == [
        "math-11",
        "ties-ref-0",
    ]

    with pytest.raises(ValueError, match="not in the full split"):
        RewardBench2Adapter(tmp_path / "c", task_ids=["math-999"]).run()
    with pytest.raises(ValueError, match="limit must not be negative"):
        RewardBench2Adapter(tmp_path / "c", limit=-1)

    marker = tmp_path / "b" / "math-11" / "instruction.md"
    marker.write_text("edited")
    RewardBench2Adapter(tmp_path / "b", task_ids=["math-11"]).run()
    assert marker.read_text() == "edited"
    RewardBench2Adapter(tmp_path / "b", task_ids=["math-11"], overwrite=True).run()
    assert marker.read_text() != "edited"


def test_generation_is_deterministic(fake_data, tmp_path):
    RewardBench2Adapter(tmp_path / "a").run()
    RewardBench2Adapter(tmp_path / "b").run()
    for path in (tmp_path / "a").rglob("*"):
        if path.is_file():
            other = tmp_path / "b" / path.relative_to(tmp_path / "a")
            assert path.read_bytes() == other.read_bytes()


def test_parity_selection_keeps_all_ties_and_caps_other_subsets():
    by_subset = {
        "Math": [f"math-{i}" for i in range(100)],
        "Focus": [f"focus-{i}" for i in range(10)],
        "Ties": [f"ties-ref-{i}" for i in range(5)]
        + [f"ties-tied-{i}" for i in range(5)],
    }
    chosen = adapter_module.select_parity_ids(by_subset)
    assert (
        sum(i.startswith("math-") for i in chosen) == adapter_module.PARITY_PER_SUBSET
    )
    assert sum(i.startswith("focus-") for i in chosen) == 10
    assert sum(i.startswith("ties-") for i in chosen) == 10
    assert chosen == adapter_module.select_parity_ids(by_subset)


def test_unknown_split_and_misspelled_arguments_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="split must be one of"):
        RewardBench2Adapter(tmp_path, split="lite")
    with pytest.raises(TypeError):
        RewardBench2Adapter(tmp_path, splt="parity")


# --- official aggregation ---------------------------------------------------


def test_ties_score_matches_a_hand_computed_example():
    rows = [
        {"source_id": "ref:0", "num_correct": 1, "scores": [9, 3, 2]},
        {"source_id": "tied:0", "num_correct": 2, "scores": [8, 7, 4]},
        {"source_id": "ref:1", "num_correct": 1, "scores": [5, 5]},
        {"source_id": "tied:1", "num_correct": 2, "scores": [9, 5, 6]},
    ]
    # accuracy: ref 1/2, tied 1/2; margins within correct [1, 4]; correct minus
    # incorrect: tied [3, -1], ref [6, 0]; both "preferred" terms are 1/2.
    margin_term = (math.tanh(3 / 1 - 1) + math.tanh(-1 / 4 - 1)) / 2
    expected = 0.3 * 0.5 + 0.3 * 0.5 + 0.2 * 0.5 + 0.2 * 0.5 + 0.01 * margin_term
    assert score.ties_score(rows) == pytest.approx(expected, abs=1e-12)


def test_ties_score_with_zero_margin_between_correct_answers():
    rows = [
        {"source_id": "ref:0", "num_correct": 1, "scores": [10, 1]},
        {"source_id": "tied:0", "num_correct": 2, "scores": [10, 10, 1]},
    ]
    # 9 / 0 is +inf and tanh(+inf) is 1.
    assert score.ties_score(rows) == pytest.approx(0.3 + 0.3 + 0.2 + 0.2 + 0.01)


def test_score_is_the_unweighted_mean_of_subset_scores():
    gradings = [
        {"task_id": "math-0", "subset": "Math", "reward": 1.0},
        {"task_id": "math-1", "subset": "Math", "reward": 0.0},
        {"task_id": "math-2", "subset": "Math", "reward": 0.25},
        {"task_id": "focus-0", "subset": "Focus", "reward": 1.0},
        {
            "task_id": "ties-ref-0",
            "subset": "Ties",
            "source_id": "ref:0",
            "num_correct": 1,
            "scores": [10, 1],
        },
        {
            "task_id": "ties-tied-0",
            "subset": "Ties",
            "source_id": "tied:0",
            "num_correct": 2,
            "scores": [10, 10, 1],
        },
    ]
    result = score.score(gradings)
    assert result["subsets"]["Math"] == {"score": pytest.approx(1.25 / 3), "tasks": 3}
    assert result["subsets"]["Focus"]["score"] == 1.0
    assert result["subsets"]["Ties"]["score"] == pytest.approx(1.01)
    assert result["average"] == pytest.approx((1.25 / 3 + 1.0 + 1.01) / 3)
    assert result["subsets_in_average"] == 3


def test_score_rejects_a_task_graded_twice():
    gradings = [{"task_id": "math-0", "subset": "Math", "reward": 1.0}] * 2
    with pytest.raises(ValueError, match="more than once"):
        score.score(gradings)


def write_trial(job_dir: Path, name: str, reward, grading: dict | None) -> Path:
    trial = job_dir / name
    (trial / "verifier").mkdir(parents=True)
    result = {
        "verifier_result": {"rewards": {"reward": reward}}
        if reward is not None
        else None
    }
    (trial / "result.json").write_text(json.dumps(result))
    if grading is not None:
        (trial / "verifier" / "grading.json").write_text(json.dumps(grading))
    return trial


def test_load_gradings_reports_trials_without_a_reward_or_grading(tmp_path):
    grading = {"task_id": "math-0", "subset": "Math", "reward": 1.0}
    write_trial(tmp_path, "math-0__abc", 1.0, grading)
    no_grading = write_trial(tmp_path, "math-1__def", 0.0, None)
    # A grading.json without a reward recorded by Harbor is not trusted.
    no_reward = write_trial(
        tmp_path, "math-2__ghi", None, {"task_id": "math-2", "reward": 1.0}
    )
    gradings, ungraded = score.load_gradings(tmp_path)
    assert gradings == [grading]
    assert ungraded == [str(no_grading), str(no_reward)]


def test_load_gradings_rejects_a_grading_that_disagrees_with_harbor(tmp_path):
    write_trial(
        tmp_path,
        "math-0__abc",
        0.0,
        {"task_id": "math-0", "subset": "Math", "reward": 1.0},
    )
    with pytest.raises(ValueError, match="Harbor recorded 0.0"):
        score.load_gradings(tmp_path)


def test_score_leaves_ties_out_without_a_ref_tied_pair():
    gradings = [
        {"task_id": "math-0", "subset": "Math", "reward": 1.0},
        {
            "task_id": "ties-ref-0",
            "subset": "Ties",
            "source_id": "ref:0",
            "num_correct": 1,
            "scores": [10, 1],
        },
    ]
    result = score.score(gradings)
    assert list(result["subsets"]) == ["Math"]
    assert "Ties" in result["skipped"]
    assert result["average"] == 1.0


def test_grader_output_feeds_the_scorer(fake_data, tmp_path, grader_paths, monkeypatch):
    """Oracle answers -> grade.main -> Harbor-style job directory -> score CLI."""
    tasks = tmp_path / "tasks"
    RewardBench2Adapter(tasks).run()
    job = tmp_path / "job"
    for task_dir in sorted(tasks.iterdir()):
        name, content = oracle_output(task_dir)
        (grader_paths / "judgment.txt").unlink(missing_ok=True)
        (grader_paths / "ratings.json").unlink(missing_ok=True)
        (grader_paths / name).write_text(content)
        truth = json.loads((task_dir / "tests" / "ground_truth.json").read_text())
        grading = run_grader(grader_paths, truth)
        write_trial(job, f"{task_dir.name}__x", grading["reward"], grading)
    monkeypatch.setattr(sys, "argv", ["reward-bench-2-score", str(job), "--json"])
    assert score.main() == 0
    result = score.score(score.load_gradings(job)[0])
    assert {name: entry["score"] for name, entry in result["subsets"].items()} == {
        "Factuality": 1.0,
        "Precise IF": 1.0,
        "Math": 1.0,
        "Ties": pytest.approx(1.01),
    }


def test_score_cli_exit_codes(tmp_path, monkeypatch, capsys):
    def run(*args: str) -> int:
        monkeypatch.setattr(sys, "argv", ["reward-bench-2-score", *args])
        return score.main()

    assert run(str(tmp_path / "missing")) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(str(empty)) == 2
    job = tmp_path / "job"
    write_trial(
        job, "math-0__a", 1.0, {"task_id": "math-0", "subset": "Math", "reward": 1.0}
    )
    write_trial(job, "math-1__b", None, None)
    assert run(str(job)) == 1
    assert run(str(job), "--allow-ungraded") == 0
    out = capsys.readouterr().out
    assert "Math" in out
    assert "1 ungraded trial(s) left out" in out
    write_trial(
        job, "math-0__c", 1.0, {"task_id": "math-0", "subset": "Math", "reward": 1.0}
    )
    assert run(str(job), "--allow-ungraded") == 2


# --- full dataset (needs the pinned file) -----------------------------------


@pytest.mark.skipif(not FULL_DATASET, reason="set RB2_FULL_DATASET=1 to download it")
def test_full_dataset_counts_positions_and_parity_list(tmp_path):
    adapter = RewardBench2Adapter(tmp_path)
    specs = adapter.build_tasks()  # also checks the pinned seed-0 positions
    assert len(specs) == 1865
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec["subset"]] = counts.get(spec["subset"], 0) + 1
    assert counts == {
        "Factuality": 475,
        "Precise IF": 160,
        "Math": 183,
        "Safety": 450,
        "Focus": 495,
        "Ties": 102,
    }
    parity = RewardBench2Adapter(tmp_path, split="parity")
    assert len(parity.select(specs)) == 302
