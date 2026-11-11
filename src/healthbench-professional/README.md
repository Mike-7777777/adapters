# HealthBench Professional → Harbor Adapter

**Status: work in progress.** The generator, grader and task layout run end to end, and the oracle passes on all 525 tasks in Harbor 0.25.0. Parity experiments are still to come; see [Notes & Caveats](#notes--caveats).

## Overview

This adapter turns the 525 public conversations of HealthBench Professional into Harbor tasks. Each task asks the agent to write the assistant's next reply to a clinician conversation; the verifier grades the reply against the physician-written rubric with an LLM judge, using the grader prompt and scoring of the original harness.

- Source: Hugging Face [`openai/healthbench-professional`](https://huggingface.co/datasets/openai/healthbench-professional), MIT license, pinned to revision `349962fd`.
- Original harness: [openai/simple-evals](https://github.com/openai/simple-evals) `healthbench_eval.py`; the original evaluation calls a model API directly, with no agent.
- Adapted size: 525 tasks.

## What is HealthBench Professional?

[HealthBench Professional](https://arxiv.org/abs/2604.27470) (OpenAI, 2026) evaluates model replies to real conversations from clinicians using ChatGPT for Clinicians. The public set has 525 conversations (115 multi-turn) across consult, research and writing use cases, each with 1-5 physician-written rubric items (1135 in total, 213 of them negative).

An LLM judge (`gpt-5.4-2026-03-05`, reasoning low) decides whether each rubric item is met. Task score = sum of met item points / sum of positive points, minus a length adjustment of 0.0147 per 500 characters above 2000. The benchmark score is the mean task score, clipped to [0, 1].

## Adapter Features

- Pointer-only tasks. The dataset README asks that examples not be shown in plain text online, so a task holds only its record id and the dataset canary. `hbp_data.py` fetches the data from the pinned revision and checks its sha256; a mismatch stops the build or the grading.
- Tamper-resistant grading. The verifier fetches the conversation and rubric with its own copy of `hbp_data.py`, so editing files under `/workspace` cannot change what is graded.
- Grader parity. `tests/grader.py` reuses the simple-evals grader prompt verbatim and its scoring (MIT, attribution in the file header). Unparseable judge output is retried a bounded number of times; after that the verifier exits non-zero instead of recording a score.
- Selectable reward range (`REWARD_MODE`), see [Notes & Caveats](#notes--caveats).

## Generated Task Structure

```
<task_id>/
├── task.toml
├── instruction.md            # asks the agent to read /workspace/conversation.md
├── environment/
│   ├── Dockerfile            # writes /workspace/conversation.md at build time
│   └── hbp_data.py
├── solution/solve.sh         # oracle reply (when generated with --oracle-dir)
└── tests/
    ├── test.sh
    ├── grader.py             # fetches conversation and rubric itself
    ├── hbp_data.py
    └── task_id
```

The agent writes its reply to `/workspace/response.txt`.

## Run Evaluation / Harness in Harbor

The verifier calls the judge through the OpenAI Responses API:

```bash
export OPENAI_API_KEY=...
# optional: OPENAI_BASE_URL, GRADER_MODEL (default gpt-5.4-2026-03-05), HF_ENDPOINT, HBP_REWARD_MODE
uv run harbor run -p datasets/healthbench-professional -a <agent> -m "<model>"
```

A single trial:

```bash
uv run harbor trial start -p datasets/healthbench-professional/<task_id> -a <agent> -m "<model>"
```

One full grading pass over 525 replies is 1135 judge calls.

## Usage: Create Task Directories

```bash
cd adapters/healthbench-professional
uv run healthbench-professional --output-dir ../../datasets/healthbench-professional
```

Options: `--limit N`, `--task-ids ID ...`, `--overwrite`, `--source PATH` (a local copy of the pinned file; its sha256 is still checked), `--oracle-dir DIR` (`<task_id>.txt` oracle replies for `solution/solve.sh`), `--reward-mode {unclipped,both,clipped}`, `--split {full,parity}` (`parity`: the 150-task parity subset).

Unit tests: `uv run pytest tests`.

## Comparison with Original Benchmark (Parity)

Pending; runs start once the Harbor team provides keys. Plan reviewed in the adapter PR: Scenario 2, a claude-code sampler added to a fork of simple-evals ([Mike-7777777/simple-evals@harbor-parity](https://github.com/Mike-7777777/simple-evals/tree/harbor-parity)), `claude-code` 2.1.322 with Claude Sonnet 5.5 on both sides, the same judge (`gpt-5.4-2026-03-05`, reasoning low), a 10-task sanity check on both sides, then 3 runs per side on the parity subset. Benchmark score on each side: the mean unclipped task score, clipped to [0, 1] after averaging.

**Parity subset.** `--split parity` generates the 150 tasks listed in `src/healthbench_professional/parity_task_ids.txt`, which the simple-evals side reads too. They are stratified by use case and type: each of the 6 strata gets seats in proportion to its size (largest remainder), and within a stratum records are taken in sha256(id) order. `select_parity_ids` in `adapter.py` reproduces the list, and generation stops if the two differ.

| Use case | Type | Records | In subset |
|---|---|---|---|
| consult | good_faith | 143 | 41 |
| consult | red_teaming | 93 | 27 |
| research | good_faith | 140 | 40 |
| research | red_teaming | 7 | 2 |
| writing | good_faith | 51 | 14 |
| writing | red_teaming | 91 | 26 |
| all | | 525 | 150 |

Original side: see `HARBOR_PARITY.md` in the fork; it runs the same 150 ids.

Harbor side, from this directory:

```bash
uv run healthbench-professional --split parity --oracle-dir <dir> \
  --output-dir datasets/healthbench-professional-parity
harbor run -c healthbench-professional_parity.yaml
```

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|---|---|---|---|---|---|---|
| claude-code | claude-sonnet-5-5 | length-adjusted score | 3 | 150 (29% of 525) | pending | pending |

Checks so far:

- Physician reference replies, all 525, graded with this grader's prompt and scoring (judge `gpt-5.4`, reasoning low): length-adjusted mean 0.429; the paper reports 0.437 (section 5.1).
- Harbor 0.24.0, 3 tasks: every per-item verdict from `test.sh` matched a direct call to the simple-evals code.
- Pointer layout, 11 tasks with the oracle agent: the 10 tasks with a rubric-targeted oracle reply scored 1.0 before length adjustment; the generated task directories contain no dataset text.
- Reward modes in Harbor 0.24.0: `unclipped` gives one `mean`; `both` gives separate means for `reward` and `score`; `HBP_REWARD_MODE` overrides the mode a task was generated with.

## Notes & Caveats

**Reward range.** A task score can be below 0 (negative rubric items, long replies) or above 1 (the length adjustment adds a little for replies under 2000 characters). The paper clips only the mean over tasks to [0, 1]; clipping each task first moves the physician mean from 0.429 to 0.490. `REWARD_MODE` selects what the verifier writes; `--reward-mode` sets the default at generation time and `HBP_REWARD_MODE` overrides it at run time:

| Mode | Verifier output | Benchmark score |
|---|---|---|
| `unclipped` | `reward.txt`: the task score | mean of `reward`, clipped to [0, 1] |
| `both` (default) | `reward.json`: `reward` clipped to [0, 1], `score` unclipped | mean of `score`, clipped to [0, 1] |
| `clipped` | `reward.txt`: the task score clipped to [0, 1] | mean of `reward`; not comparable with the paper |

The default `both` keeps `reward` in [0, 1], as the guide requires of `test.sh`, and keeps the unclipped `score` that the paper's metric is computed from; Harbor averages each `reward.json` key separately. `unclipped` follows merged adapters whose original metric leaves [0, 1] (`sldbench`, `mlgym-bench`).

**Oracle solutions.** Physician replies score 0.43 on average, so they cannot serve as the oracle. Oracle replies are written against each rubric with AI assistance, kept at or under 2000 characters so the length adjustment does not apply, and accepted only after three consecutive full-score gradings. In Harbor 0.25.0 all 525 reach reward 1.0. They are passed in with `--oracle-dir` and embedded in each generated `solution/solve.sh`, which the agent never sees; they are AI-written text, not dataset records. The full set is uploaded with the parity results.

**Judge snapshot.** Results are only comparable with the paper under `gpt-5.4-2026-03-05`. The local checks above used a third-party endpoint that reports the model as `gpt-5.4` without a snapshot.

## Installation / Prerequisites

- Docker, and Harbor 0.25 or later. Harbor 0.25 clears `reward.txt` and `reward.json` before each verification; 0.24 does not, and prefers a leftover `reward.json` over a fresh `reward.txt`.
- Network access at build time (Hugging Face) and at verification time (Hugging Face and the judge endpoint).
- An OpenAI API key with access to `gpt-5.4-2026-03-05`.

## Troubleshooting

- `sha256 mismatch`: the downloaded file differs from the pinned revision; check `HF_ENDPOINT` or the `--source` file.
- `download failed after 3 attempts`: Hugging Face is unreachable from the build or the verifier container; set `HF_ENDPOINT` to a mirror.
- Verifier error with `grader failed after 4 attempts`: the judge kept returning unparseable output or errors; check `OPENAI_BASE_URL`, `GRADER_MODEL` and the key.
- `no oracle reply was generated for this task`: the task was generated without `--oracle-dir`.

## Citation

```bibtex
@misc{healthbenchprofessional2026,
  title         = {HealthBench Professional: Evaluating Large Language Models on Real Clinician Chats},
  author        = {Hicks, Rebecca Soskin and Trofimov, Mikhail and Lim, Dominick and others},
  year          = {2026},
  eprint        = {2604.27470},
  archivePrefix = {arXiv}
}
```

## Authors & Contributions

Adapter: Qumeng Sun (@Mike-7777777). Issues and questions: open an issue or ask in `#adapters-spam` on the Harbor Discord.
