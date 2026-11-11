from __future__ import annotations

import json
import shutil
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


def test_adapter_defaults_to_both(tmp_path, monkeypatch):
    monkeypatch.setattr(
        adapter_module.hbp_data, "load_rows", lambda source=None: [fake_row("abc")]
    )
    adapter_module.HealthBenchProfessionalAdapter(tmp_path).run()
    toml = (tmp_path / "abc" / "task.toml").read_text()
    assert 'REWARD_MODE = "${HBP_REWARD_MODE:-both}"' in toml


def test_grader_defaults_to_both(log_dir, monkeypatch):
    monkeypatch.delenv("REWARD_MODE", raising=False)
    assert grader.main() == 0
    assert json.loads((log_dir / "reward.json").read_text()) == {
        "reward": 0.0,
        "score": 0.0,
    }


def test_adapter_rejects_an_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="reward_mode"):
        adapter_module.HealthBenchProfessionalAdapter(tmp_path, reward_mode="clip")


def test_bytecode_caches_stay_out_of_generated_tasks(tmp_path, monkeypatch):
    # Importing the template's grader (as this file does) leaves a __pycache__
    # in the template; it must not be copied into tasks.
    template = tmp_path / "template"
    shutil.copytree(adapter_module.TEMPLATE_DIR, template)
    cache = template / "tests" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "grader.cpython-313.pyc").write_bytes(b"\xf3\x0d\x0d\x0a")
    monkeypatch.setattr(adapter_module, "TEMPLATE_DIR", template)
    monkeypatch.setattr(
        adapter_module.hbp_data, "load_rows", lambda source=None: [fake_row("abc")]
    )
    adapter_module.HealthBenchProfessionalAdapter(tmp_path / "out").run()
    leaked = [
        f.relative_to(tmp_path / "out").as_posix()
        for f in (tmp_path / "out").rglob("*")
        if f.name == "__pycache__" or f.suffix == ".pyc"
    ]
    assert leaked == []


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


def test_parity_split_generates_only_the_listed_tasks(tmp_path, monkeypatch):
    rows = strata_rows({("a", "x"): 6, ("b", "y"): 4})
    expected = adapter_module.select_parity_ids(rows, size=5)
    listed = tmp_path / "parity_task_ids.txt"
    listed.write_text("\n".join(expected) + "\n")
    monkeypatch.setattr(adapter_module.hbp_data, "load_rows", lambda source=None: rows)
    monkeypatch.setattr(adapter_module, "PARITY_SIZE", 5)
    monkeypatch.setattr(adapter_module, "PARITY_IDS_FILE", listed)
    out = tmp_path / "out"
    adapter_module.HealthBenchProfessionalAdapter(out, split="parity").run()
    assert sorted(p.name for p in out.iterdir()) == expected

    listed.write_text("\n".join(expected[:-1]) + "\n")
    with pytest.raises(RuntimeError, match="parity subset differs"):
        adapter_module.HealthBenchProfessionalAdapter(
            tmp_path / "o2", split="parity"
        ).run()


def test_committed_parity_list_has_150_unique_ids():
    ids = adapter_module.PARITY_IDS_FILE.read_text().split()
    assert len(ids) == len(set(ids)) == adapter_module.PARITY_SIZE


def test_adapter_rejects_an_unknown_split(tmp_path):
    with pytest.raises(ValueError, match="split"):
        adapter_module.HealthBenchProfessionalAdapter(tmp_path, split="lite")
