"""Unit tests for the OTelBench adapter. They run offline against a fake upstream tree."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from otel_bench import adapter
from otel_bench.adapter import (
    EXCLUDED_TASKS,
    READINESS_WAIT_TASK_IDS,
    TASK_IDS,
    UPSTREAM_COMMIT,
    OTelBenchAdapter,
    render_task_toml,
)

UPSTREAM_TASK_TOML = """version = "1.0"

[metadata]
author_name = "Ada Example"
author_email = "ada@example.com"
difficulty = "medium"
category = "programming"
tags = ["opentelemetry", "go"]

[verifier]
timeout_sec = 600.0

[agent]
timeout_sec = 900.0

[environment]
build_timeout_sec = 600.0
cpus = 2
memory_mb = 4096
storage_mb = 10240"""

UPSTREAM_SOLVE = "#!/bin/bash\nset -e\necho upstream\n"


def make_upstream_task(root: Path, task_id: str, with_solution: bool = True) -> Path:
    task = root / task_id
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "task.toml").write_text(UPSTREAM_TASK_TOML)
    (task / "instruction.md").write_text("Instrument the service.\n")
    (task / "environment" / "Dockerfile").write_text("FROM ubuntu:22.04\n")
    (task / "tests" / "test.sh").write_text(
        "#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n"
    )
    (task / "SUMMARY-some-model.md").write_text("run notes, not part of the task\n")
    if with_solution:
        (task / "solution").mkdir()
        (task / "solution" / "solve.sh").write_text(UPSTREAM_SOLVE)
    return task


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    root = tmp_path / "upstream"
    for task_id in TASK_IDS:
        make_upstream_task(root, task_id, with_solution=task_id != "ruby-microservices")
    return root


def generate(upstream: Path, tmp_path: Path, **kwargs) -> Path:
    output = tmp_path / "out"
    OTelBenchAdapter(output_dir=output, source_dir=upstream).run(**kwargs)
    return output


def test_task_ids_are_unique_and_registry_safe():
    assert len(TASK_IDS) == len(set(TASK_IDS)) == 25
    assert not set(TASK_IDS) & set(EXCLUDED_TASKS)
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", task_id) for task_id in TASK_IDS)
    assert set(READINESS_WAIT_TASK_IDS) <= set(TASK_IDS)


def test_task_toml_gains_task_table_and_keeps_upstream_values(upstream: Path):
    rendered = tomllib.loads(
        render_task_toml("go-http-tracing", upstream / "go-http-tracing" / "task.toml")
    )
    source = tomllib.loads(UPSTREAM_TASK_TOML)

    assert rendered["version"] == "1.0"
    assert rendered["task"] == {
        "name": "quesma/go-http-tracing",
        "authors": [{"name": "Ada Example", "email": "ada@example.com"}],
        "keywords": ["opentelemetry", "go"],
    }
    assert rendered["metadata"]["source_commit"] == UPSTREAM_COMMIT
    assert rendered["metadata"]["tags"] == source["metadata"]["tags"]
    for table in ("verifier", "agent", "environment"):
        assert rendered[table] == source[table]


def test_unknown_upstream_field_is_an_error_not_a_silent_drop(tmp_path: Path):
    task_toml = tmp_path / "task.toml"
    task_toml.write_text(UPSTREAM_TASK_TOML + "\ngpus = 1\n")

    with pytest.raises(ValueError, match="gpus"):
        render_task_toml("go-http-tracing", task_toml)


def test_instruction_environment_and_tests_are_copied_unchanged(
    upstream: Path, tmp_path: Path
):
    output = generate(upstream, tmp_path)

    assert sorted(path.name for path in output.iterdir()) == sorted(TASK_IDS)
    for task_id in TASK_IDS:
        for relative in ("instruction.md", "environment/Dockerfile", "tests/test.sh"):
            assert (output / task_id / relative).read_bytes() == (
                upstream / task_id / relative
            ).read_bytes()
        assert not (output / task_id / "SUMMARY-some-model.md").exists()


def test_upstream_oracle_without_fixes_is_copied_unchanged(
    upstream: Path, tmp_path: Path
):
    output = generate(upstream, tmp_path, task_ids=["go-http-tracing"])

    assert (
        output / "go-http-tracing" / "solution" / "solve.sh"
    ).read_text() == UPSTREAM_SOLVE


def test_task_without_upstream_oracle_gets_the_adapter_oracle(
    upstream: Path, tmp_path: Path
):
    output = generate(upstream, tmp_path, task_ids=["ruby-microservices"])

    solve = (output / "ruby-microservices" / "solution" / "solve.sh").read_text()
    assert solve.startswith("#!/bin/bash\n")
    assert "opentelemetry-sdk" in solve
    assert "wait_for_port.py" in solve


def test_oracle_fixes_wrap_the_upstream_script(upstream: Path, tmp_path: Path):
    output = generate(
        upstream,
        tmp_path,
        task_ids=[
            "php-distributed-context-propagation",
            "java-simple",
            "net-microservices",
        ],
    )

    php = (
        output / "php-distributed-context-propagation" / "solution" / "solve.sh"
    ).read_text()
    assert php.startswith("#!/bin/bash\n# Added by the Harbor adapter.")
    assert php.index("apt-get install -y python3") < php.index("echo upstream")

    java = (output / "java-simple" / "solution" / "solve.sh").read_text()
    assert java.startswith(UPSTREAM_SOLVE)
    assert "jar xf" in java

    net = (output / "net-microservices" / "solution" / "solve.sh").read_text()
    assert net.startswith(UPSTREAM_SOLVE)
    assert "wait_for_port.py" in net


def test_readiness_wait_is_limited_to_the_slow_starting_services(
    upstream: Path, tmp_path: Path
):
    output = generate(upstream, tmp_path)

    waiting = {
        task_id
        for task_id in TASK_IDS
        if "wait_for_port.py"
        in (output / task_id / "solution" / "solve.sh").read_text()
    }
    assert waiting == set(READINESS_WAIT_TASK_IDS)


def test_adapter_oracle_refuses_to_shadow_a_new_upstream_solution(tmp_path: Path):
    upstream = tmp_path / "upstream"
    make_upstream_task(upstream, "ruby-microservices", with_solution=True)

    with pytest.raises(FileExistsError, match="ruby-microservices"):
        generate(upstream, tmp_path, task_ids=["ruby-microservices"])


def test_existing_task_is_kept_unless_overwrite(upstream: Path, tmp_path: Path):
    output = generate(upstream, tmp_path, task_ids=["cpp-simple"])
    marker = output / "cpp-simple" / "marker"
    marker.write_text("local edit")

    generate(upstream, tmp_path, task_ids=["cpp-simple"])
    assert marker.exists()

    generate(upstream, tmp_path, task_ids=["cpp-simple"], overwrite=True)
    assert not marker.exists()


def test_limit_and_unknown_task_ids(upstream: Path, tmp_path: Path):
    output = generate(upstream, tmp_path, limit=2)
    assert sorted(path.name for path in output.iterdir()) == sorted(TASK_IDS[:2])

    with pytest.raises(ValueError, match="no-such-task"):
        generate(upstream, tmp_path, task_ids=["no-such-task"])


def test_missing_required_file_fails_generation(upstream: Path, tmp_path: Path):
    (upstream / "cpp-simple" / "tests" / "test.sh").unlink()

    with pytest.raises(FileNotFoundError, match="tests/test.sh"):
        generate(upstream, tmp_path, task_ids=["cpp-simple"])


def test_every_oracle_directory_belongs_to_a_task():
    names = {path.name for path in adapter.ORACLE_DIR.iterdir() if path.is_dir()}
    assert names - {"_shared"} <= set(TASK_IDS)


def test_excluded_task_is_refused_with_its_reason(upstream: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="idna_adapter"):
        generate(upstream, tmp_path, task_ids=["rust-distributed-context-propagation"])
