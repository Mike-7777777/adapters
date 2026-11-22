from __future__ import annotations

import base64
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

# Importing grader must not leave a bytecode cache in the task template.
sys.dont_write_bytecode = True
PKG = Path(__file__).parents[1] / "src" / "healthbench_professional"
sys.path.insert(0, str(PKG / "task-template" / "tests"))
sys.path.insert(0, str(PKG))  # hbp_data.py, copied into tests/ at generation time

import grader

from healthbench_professional import adapter as adapter_module


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(grader, "LOG_DIR", tmp_path / "verifier")
    monkeypatch.setattr(grader, "RESPONSE_PATH", tmp_path / "response.txt")
    return tmp_path / "verifier"


@pytest.mark.parametrize(("value", "clipped"), [(-0.3, 0.0), (0.6, 0.6), (1.02, 1.0)])
def test_reward_json_holds_clipped_reward_and_unclipped_score(log_dir, value, clipped):
    grader.write_reward(value, {})
    assert json.loads((log_dir / "reward.json").read_text()) == {
        "reward": clipped,
        "score": value,
    }


@pytest.fixture
def judge(log_dir, tmp_path, monkeypatch):
    """Replace the OpenAI client; the last output repeats once the rest are used."""
    record = {
        "conversation": {"messages": [{"role": "user", "content": "question"}]},
        "rubric_items": [
            {"criterion_text": "good", "points": 5},
            {"criterion_text": "bad", "points": -2},
        ],
    }
    (tmp_path / "record-abc").write_text("abc\n")
    monkeypatch.setattr(grader, "TESTS", tmp_path)
    monkeypatch.setattr(
        grader, "load_record", lambda task_id: record if task_id == "abc" else None
    )
    calls = []

    def install(*outputs):
        def create(**request):
            calls.append(request)
            return SimpleNamespace(
                output_text=outputs[min(len(calls), len(outputs)) - 1]
            )

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        monkeypatch.setitem(
            sys.modules, "openai", SimpleNamespace(OpenAI=lambda **kwargs: client)
        )
        return calls

    return install


def test_empty_response_is_judged_as_simple_evals_does(log_dir, judge):
    # simple-evals grades whatever the sampler returns, "" included: no item
    # met gives 0 - 0.0147 * ((0 - 2000) / 500).
    calls = judge('{"criteria_met": false}')
    assert grader.main() == 0
    assert len(calls) == 2
    assert calls[0]["reasoning"] == {"effort": "low"}
    assert calls[0]["input"][0]["content"].count("user: question\n\nassistant: \n") == 1
    assert json.loads((log_dir / "reward.json").read_text()) == pytest.approx(
        {"reward": 0.0588, "score": 0.0588}
    )


def test_reply_that_is_not_utf8_is_still_graded(log_dir, judge):
    (log_dir.parent / "response.txt").write_bytes(b"ok \xff\xfe" + b"x" * 1995)
    calls = judge('{"criteria_met": true}')
    assert grader.main() == 0
    assert "ok \ufffd\ufffd" in calls[0]["input"][0]["content"]
    details = json.loads((log_dir / "grading.json").read_text())
    assert details["response_chars"] == 2000


@pytest.mark.parametrize("bad", ['{"criteria_met": 1}', "[]", "null", "not json"])
def test_verdicts_that_are_not_booleans_are_retried(log_dir, judge, bad):
    (log_dir.parent / "response.txt").write_text("x" * 2000)
    judge(bad, '{"criteria_met": true}')
    assert grader.main() == 0
    items = json.loads((log_dir / "grading.json").read_text())["items"]
    assert [item["attempts"] for item in items] == [2, 1]
    assert json.loads((log_dir / "reward.json").read_text())["score"] == 0.6


def fake_row(task_id: str) -> dict:
    return {
        "id": task_id,
        "use_case": "consult",
        "type": "good_faith",
        "specialty": "cardiology",
        "difficulty": "typical",
        "canary_string": "canary",
    }


def test_generated_task_holds_no_record_text(tmp_path, monkeypatch):
    row = fake_row("abc") | {
        "conversation": {
            "messages": [{"role": "user", "content": "CONVERSATION-TEXT-SENTINEL"}]
        },
        "rubric_items": [{"criterion_text": "RUBRIC-TEXT-SENTINEL", "points": 5}],
        "physician_response": "PHYSICIAN-TEXT-SENTINEL",
    }
    monkeypatch.setattr(adapter_module.hbp_data, "load_rows", lambda source=None: [row])
    adapter_module.HealthBenchProfessionalAdapter(tmp_path).run()
    files = [f for f in (tmp_path / "abc").rglob("*") if f.is_file()]
    blob = "\n".join(f.read_text() for f in files)
    for sentinel in (
        "CONVERSATION-TEXT-SENTINEL",
        "RUBRIC-TEXT-SENTINEL",
        "PHYSICIAN-TEXT-SENTINEL",
    ):
        assert sentinel not in blob
    assert "canary" in (tmp_path / "abc" / "instruction.md").read_text()


def run_solve(task: Path, workspace: Path) -> subprocess.CompletedProcess:
    script = (task / "solution" / "solve.sh").read_text()
    return subprocess.run(
        ["bash", "-c", script.replace("/workspace", str(workspace))],
        check=False,
        capture_output=True,
        text=True,
    )


def test_oracle_reply_is_encrypted_and_solve_sh_restores_it(tmp_path, monkeypatch):
    reply = 'ORACLE-TEXT-SENTINEL: it\'s "quoted" ünïcode\n$HOME `x`\n'.encode()
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    (oracle / "abc.txt").write_bytes(reply)
    row = fake_row("abc") | {"canary_string": "test canary 'quoted' GUID"}
    monkeypatch.setattr(adapter_module.hbp_data, "load_rows", lambda source=None: [row])
    adapter_module.HealthBenchProfessionalAdapter(
        tmp_path / "out", oracle_dir=oracle
    ).run()
    task = tmp_path / "out" / "abc"
    blob = "\n".join(f.read_text() for f in task.rglob("*") if f.is_file())
    assert "ORACLE-TEXT-SENTINEL" not in blob
    assert base64.b64encode(reply).decode() not in blob
    done = run_solve(task, tmp_path / "ws")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "ws" / "response.txt").read_bytes() == reply


def test_solve_sh_without_an_oracle_reply_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        adapter_module.hbp_data, "load_rows", lambda source=None: [fake_row("abc")]
    )
    adapter_module.HealthBenchProfessionalAdapter(tmp_path / "out").run()
    done = run_solve(tmp_path / "out" / "abc", tmp_path / "ws")
    assert done.returncode == 1
    assert "no oracle reply" in done.stderr


def test_generated_task_grades_in_a_separate_verifier(tmp_path, monkeypatch):
    monkeypatch.setattr(
        adapter_module.hbp_data, "load_rows", lambda source=None: [fake_row("abc")]
    )
    adapter_module.HealthBenchProfessionalAdapter(tmp_path).run()
    task = tmp_path / "abc"
    config = tomllib.loads((task / "task.toml").read_text())
    assert config["verifier"]["environment_mode"] == "separate"
    # The reply is the only file the verifier needs from the agent's container.
    assert config["artifacts"] == [str(grader.RESPONSE_PATH)]
    assert (task / "tests" / "Dockerfile").is_file()


def test_build_contexts_differ_between_tasks_only_in_a_file_name(tmp_path, monkeypatch):
    # The image build can reuse its previous copy of a file with the same name,
    # size and modification time, so a same-size per-task file (an id written
    # in the same clock tick) can reach the image stale. Only the record file's
    # name may differ between tasks.
    monkeypatch.setattr(
        adapter_module.hbp_data,
        "load_rows",
        lambda source=None: [fake_row("a" * 32), fake_row("b" * 32)],
    )
    adapter_module.HealthBenchProfessionalAdapter(tmp_path).run()
    for sub in ("environment", "tests"):
        contexts = []
        for task_id in ("a" * 32, "b" * 32):
            ctx = tmp_path / task_id / sub
            assert sorted(p.name for p in ctx.glob("record-*")) == [f"record-{task_id}"]
            contexts.append(
                {
                    p.relative_to(ctx).as_posix(): p.read_bytes()
                    for p in ctx.rglob("*")
                    if p.is_file() and not p.name.startswith("record-")
                }
            )
        assert contexts[0] == contexts[1], sub


@pytest.mark.parametrize("names", [[], ["record-a", "record-b"]])
def test_record_id_needs_exactly_one_record_file(tmp_path, names):
    for name in names:
        (tmp_path / name).touch()
    with pytest.raises(SystemExit, match="record-"):
        adapter_module.hbp_data.record_id(tmp_path)


def strata_rows(sizes: dict[tuple[str, str], int]) -> list[dict]:
    return [
        fake_row(f"{use_case}-{kind}-{n}") | {"use_case": use_case, "type": kind}
        for (use_case, kind), count in sizes.items()
        for n in range(count)
    ]


def test_parity_seats_follow_stratum_sizes():
    rows = strata_rows(
        {
            ("a", "x"): 143,
            ("a", "y"): 93,
            ("b", "x"): 140,
            ("b", "y"): 7,
            ("c", "x"): 51,
            ("c", "y"): 91,
        }
    )
    ids = adapter_module.select_parity_ids(rows)
    counts = {}
    for i in ids:
        key = tuple(i.split("-")[:2])
        counts[key] = counts.get(key, 0) + 1
    # 4/7 remainders tie for the last seat; the earlier stratum key wins.
    assert counts == {
        ("a", "x"): 41,
        ("a", "y"): 27,
        ("b", "x"): 40,
        ("b", "y"): 2,
        ("c", "x"): 14,
        ("c", "y"): 26,
    }
    assert ids == adapter_module.select_parity_ids(list(reversed(rows)))


def test_parity_split_generates_only_the_selected_tasks(tmp_path, monkeypatch):
    rows = strata_rows({("a", "x"): 6, ("b", "y"): 4})
    expected = adapter_module.select_parity_ids(rows, size=5)
    monkeypatch.setattr(adapter_module.hbp_data, "load_rows", lambda source=None: rows)
    monkeypatch.setattr(adapter_module, "PARITY_SIZE", 5)
    out = tmp_path / "out"
    adapter_module.HealthBenchProfessionalAdapter(out, split="parity").run()
    assert sorted(p.name for p in out.iterdir()) == expected


def test_adapter_rejects_an_unknown_split(tmp_path):
    with pytest.raises(ValueError, match="split"):
        adapter_module.HealthBenchProfessionalAdapter(tmp_path, split="lite")


@pytest.mark.parametrize(
    ("second", "healthbench_score"),
    [({"reward": 0.0, "score": -0.3}, 0.36), ({"reward": 1.0, "score": 1.4}, 1.0)],
)
def test_dataset_metric_clips_the_mean_score(tmp_path, second, healthbench_score):
    # Harbor's uv-script metric contract: -i rewards.jsonl (one reward.json per
    # line, null for a trial without one), -o metric.json.
    rewards = [{"reward": 1.0, "score": 1.02}, second, None]
    (tmp_path / "in.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rewards))
    subprocess.run(
        [
            sys.executable,
            str(PKG / "metric.py"),
            "-i",
            str(tmp_path / "in.jsonl"),
            "-o",
            str(tmp_path / "out.json"),
        ],
        check=True,
    )
    assert json.loads((tmp_path / "out.json").read_text()) == pytest.approx(
        {
            "trials": 2,
            "trials_without_reward": 1,
            "healthbench_score": healthbench_score,
            "score_mean": (1.02 + second["score"]) / 2,
            "reward_mean": (1.0 + second["reward"]) / 2,
        }
    )
