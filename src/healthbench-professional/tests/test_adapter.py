from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).parents[1] / "src" / "healthbench_professional"
sys.path.insert(0, str(PKG.parent))
sys.path.insert(0, str(PKG / "task-template" / "tests"))
sys.path.insert(0, str(PKG))  # hbp_data.py, copied into tests/ at generation time

import grader
from healthbench_professional import adapter as adapter_module


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(grader, "LOG_DIR", tmp_path / "verifier")
    monkeypatch.setattr(grader, "RESPONSE_PATH", tmp_path / "response.txt")
    return tmp_path / "verifier"


@pytest.mark.parametrize("value", [-0.3, 0.6, 1.02])
def test_unclipped_writes_the_task_score(log_dir, value):
    grader.write_reward(value, {}, "unclipped")
    assert float((log_dir / "reward.txt").read_text()) == value
    assert not (log_dir / "reward.json").exists()


@pytest.mark.parametrize(("value", "clipped"), [(-0.3, 0.0), (0.6, 0.6), (1.02, 1.0)])
def test_clipped_writes_the_score_in_unit_range(log_dir, value, clipped):
    grader.write_reward(value, {}, "clipped")
    assert float((log_dir / "reward.txt").read_text()) == clipped


@pytest.mark.parametrize(("value", "clipped"), [(-0.3, 0.0), (1.02, 1.0)])
def test_both_writes_clipped_reward_and_unclipped_score(log_dir, value, clipped):
    grader.write_reward(value, {}, "both")
    assert json.loads((log_dir / "reward.json").read_text()) == {
        "reward": clipped,
        "score": value,
    }
    assert not (log_dir / "reward.txt").exists()


def test_unknown_mode_fails_before_grading(log_dir, monkeypatch):
    monkeypatch.setenv("REWARD_MODE", "clip")
    assert grader.main() == 1
    assert not log_dir.exists()


def test_empty_response_scores_zero_in_the_selected_mode(log_dir, monkeypatch):
    monkeypatch.setenv("REWARD_MODE", "both")
    assert grader.main() == 0
    assert json.loads((log_dir / "reward.json").read_text()) == {
        "reward": 0.0,
        "score": 0.0,
    }


def fake_row(task_id: str) -> dict:
    return {
        "id": task_id,
        "use_case": "consult",
        "type": "good_faith",
        "specialty": "cardiology",
        "difficulty": "typical",
        "canary_string": "canary",
    }


@pytest.mark.parametrize("mode", ["unclipped", "both", "clipped"])
def test_adapter_writes_the_default_mode_into_task_toml(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(
        adapter_module.hbp_data, "load_rows", lambda source=None: [fake_row("abc")]
    )
    adapter_module.HealthBenchProfessionalAdapter(tmp_path, reward_mode=mode).run()
    toml = (tmp_path / "abc" / "task.toml").read_text()
    assert f'REWARD_MODE = "${{HBP_REWARD_MODE:-{mode}}}"' in toml


def test_adapter_rejects_an_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="reward_mode"):
        adapter_module.HealthBenchProfessionalAdapter(tmp_path, reward_mode="clip")


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
