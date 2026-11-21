# RewardBench 2 → Harbor Adapter

## Overview

This adapter turns the 1,865 rows of RewardBench 2 into Harbor tasks, one task per row. A task shows a user prompt and candidate responses and asks the agent to judge them; the verifier compares the judgment with the benchmark's answer key. No model is called during verification.

- Source: Hugging Face [`allenai/reward-bench-2`](https://huggingface.co/datasets/allenai/reward-bench-2) (ODC-BY), `test` split, pinned to commit `7ff08853`.
- Original harness: [allenai/reward-bench](https://github.com/allenai/reward-bench) (Apache-2.0), `scripts/run_generative_v2.py` at commit `59f6ff70`. It calls a model API directly, with no agent.
- Adapted size: 1,865 tasks, the full split. 1,763 best-of-4 tasks (Factuality 475, Precise IF 160, Math 183, Safety 450, Focus 495) and 102 Ties tasks.
- This is the AI2 benchmark for reward models and LLM judges. It is unrelated to Meta's Multimodal RewardBench 2.

> **Differences from the official harness.** This adapter departs from `allenai/reward-bench` in two places. Details are in [Notes & Caveats](#notes--caveats).
>
> 1. **Ties protocol.** The harness rates each Ties response in a separate model call ([`generative_v2.py` L492-L493][ties-loop]). Here the agent rates all responses of a row (11 to 38) in one session and sees them side by side. Ties scores, and the overall score that includes Ties, are not directly comparable with the paper or the leaderboard.
> 2. **Missing output.** In the harness a judge that returns nothing scores 0.25, like any output without a verdict ([`process_judgement`][parse], [`run_generative_v2.py` L303-L308][quarter]). Here a missing or empty `judgment.txt` scores 0. A judgment without a verdict keeps the harness's 0.25.

## What is RewardBench 2?

[RewardBench 2](https://arxiv.org/abs/2506.01937) (Malik et al., 2025) measures how well a reward model or an LLM judge picks the better response. In five subsets a row holds one prompt, one correct response and three incorrect ones, and the judge scores 1 when it picks the correct response (random baseline 25%). The Ties subset holds 51 prompt pairs with several equally correct responses; it checks that a judge puts every correct response above every incorrect one without spreading the correct ones far apart.

Scores are computed per subset: accuracy for the five best-of-4 subsets and a combined accuracy and margin score for Ties. The benchmark score is the unweighted mean of the six subset scores.

## Adapter Features

- Official judge prompt. A best-of-4 task carries the harness's four-way prompt verbatim, and the correct response sits at the position the harness draws for that row with its default seed 0. A check against the upstream functions found all 1,763 instructions and answer letters identical.
- Deterministic verification in a separate container. `tests/grade.py` uses the harness's verdict parser and reward mapping and runs on the Python standard library alone. It runs in Harbor's [separate verifier](https://www.harborframework.com/docs/tasks/separate-verifier), which receives only the agent's output file. The scorer accepts a trial's `grading.json` only when its reward equals the one Harbor recorded.
- Official aggregation. `reward-bench-2-score` reads a Harbor job directory and prints the six subset scores and their mean, using a port of the harness's Ties scoring that matches the upstream function on 2,000 random inputs and on a published run.
- Pinned data. The parquet file is fetched from a fixed commit of the dataset; the answer positions are checked against a pinned digest of the official seed-0 positions.
- Parity subset. `--split parity` generates 302 tasks: all Ties tasks and 40 tasks per best-of-4 subset.

## Generated Task Structure

```
reward-bench-2/
├── <task_id>/                  # factuality-0, precise-if-0, ties-tied-3, ties-ref-3, ...
│   ├── task.toml
│   ├── instruction.md          # judge prompt with the candidate responses
│   ├── environment/
│   │   └── Dockerfile          # python:3.13-slim, no extra packages
│   ├── solution/
│   │   └── solve.sh            # writes the correct verdict or ratings
│   └── tests/
│       ├── test.sh
│       ├── grade.py            # writes reward.txt and grading.json
│       └── ground_truth.json   # answer key
```

A best-of-4 task asks for a judgment ending in a verdict such as `[[B]]`, saved to `/workspace/judgment.txt`. A Ties task asks for a 1-10 rating of every response, saved to `/workspace/ratings.json`.

The verifier runs in its own container (`environment_mode = "separate"` in `task.toml`), a fresh copy of the task image with `tests/` uploaded. The output file is listed under `artifacts`, which is how Harbor copies it from the agent's container.

Task ids are `<subset>-<row id>`. Row ids repeat across subsets in the source data, so the subset is part of the id.

## Run Evaluation / Harness

Once the dataset is published, run it from the registry:

```bash
# Oracle
harbor run -d allenai/reward-bench-2

# An agent and model
harbor run -d allenai/reward-bench-2 -a <agent> -m "<model>"
```

With locally generated tasks (see the next section), from the repository root:

```bash
# Reference config, oracle by default
harbor run -c src/reward-bench-2/run_reward-bench-2.yaml

# Or a dataset path
harbor run -p datasets/reward-bench-2 -a <agent> -m "<model>"

# A single task
harbor trial start -p datasets/reward-bench-2/<task_id> -a <agent> -m "<model>"
```

Harbor reports the mean reward over tasks. For the benchmark's own numbers, pass the job directory of one run to the scorer:

```bash
uv run --project src/reward-bench-2 reward-bench-2-score jobs/<job_name>
```

It prints the score of each subset and their unweighted mean; `--json` gives the same as JSON.

## Usage: Create Task Directories

```bash
cd src/reward-bench-2
uv run reward-bench-2 --output-dir ../../datasets/reward-bench-2
```

Options:

- `--output-dir`: where to write the tasks (default: `datasets/reward-bench-2` at the repository root).
- `--limit N`: only the first N tasks in dataset order.
- `--task-ids ID ...`: only these tasks.
- `--overwrite`: replace task directories that already exist.
- `--split {full,parity}`: `parity` writes the 302-task parity subset.

Unit tests: `uv run pytest tests`. Set `RB2_FULL_DATASET=1` to include the full-dataset test, which downloads the data.

## Comparison with Original Benchmark (Parity)

Setup (Scenario 2): the fork [Mike-7777777/reward-bench, branch `harbor-parity`](https://github.com/Mike-7777777/reward-bench/tree/harbor-parity) adds `scripts/run_generative_v2_agent.py`, which loads the data, draws the seed-0 answer positions, builds the four-way prompt, parses verdicts and computes the Ties score with the harness's own functions. The model call is replaced by Claude Code running in a container with the instruction text of the Harbor task, launched the way Harbor's `claude-code` agent launches it. Both sides ran Claude Code 2.1.330 with `claude-haiku-5-5`: a 10-task sanity check, then three runs per side on the parity subset (Harbor 0.26.0, separate verifier). Every run finished all 302 tasks with no trial exceptions and no failed agent runs.

**Parity subset.** `--split parity` generates the 302 tasks listed in `src/reward_bench_2/parity_task_ids.txt`: all 102 Ties tasks, because the Ties score is defined over every ref/tied pair, and 40 tasks from each of the five best-of-4 subsets, taken in sha256(task id) order. The list is committed because the original side reads the same file; `select_parity_ids` in `adapter.py` reproduces it, and generation stops if the two differ.

| Subset | Tasks | In parity subset |
|---|---|---|
| Factuality | 475 | 40 |
| Precise IF | 160 | 40 |
| Math | 183 | 40 |
| Safety | 450 | 40 |
| Focus | 495 | 40 |
| Ties | 102 | 102 |
| all | 1,865 | 302 |

Harbor side, from the repository root:

```bash
uv run --project src/reward-bench-2 reward-bench-2 --split parity --output-dir datasets/reward-bench-2-parity
harbor run -p datasets/reward-bench-2-parity -a claude-code -m anthropic/claude-haiku-5-5 --ak version=2.1.330
uv run --project src/reward-bench-2 reward-bench-2-score jobs/<job_name>
```

Original side, in the fork (needs Docker and `uv`):

```bash
uv sync
export ANTHROPIC_API_KEY=... CLAUDE_CODE_VERSION=2.1.330
uv run python scripts/run_generative_v2_agent.py \
  --model claude-haiku-5-5 --ids scripts/harbor_parity_ids.txt --num_threads 8
```

Scores are mean ± sample SEM over three runs per side, in percent; per-run scores are in `parity_experiment.json`.

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|---|---|---|---|---|---|---|
| claude-code@2.1.330 | claude-haiku-5-5 | average over six subsets | 3 | 302 (16% of 1,865) | 83.08 ± 0.4531 | 82.52 ± 1.2146 |
| claude-code@2.1.330 | claude-haiku-5-5 | Factuality | 3 | 40 | 76.04 ± 0.7507 | 75.83 ± 3.0046 |
| claude-code@2.1.330 | claude-haiku-5-5 | Precise IF | 3 | 40 | 76.67 ± 1.6667 | 72.50 ± 1.4434 |
| claude-code@2.1.330 | claude-haiku-5-5 | Math | 3 | 40 | 87.71 ± 0.2100 | 89.17 ± 0.8333 |
| claude-code@2.1.330 | claude-haiku-5-5 | Safety | 3 | 40 | 86.67 ± 1.6667 | 85.83 ± 0.8333 |
| claude-code@2.1.330 | claude-haiku-5-5 | Focus | 3 | 40 | 79.17 ± 0.8333 | 79.17 ± 2.2048 |
| claude-code@2.1.330 | claude-haiku-5-5 | Ties | 3 | 102 | 92.23 ± 0.6755 | 92.61 ± 0.7818 |

The run ranges overlap for the average and for every subset. Parity cost: $4.53 of agent cost reported by Claude Code, including the sanity runs.

Links: [original benchmark](https://github.com/allenai/reward-bench), [parity fork](https://github.com/Mike-7777777/reward-bench/tree/harbor-parity), [adapter PR](https://github.com/harbor-framework/adapters/pull/84), [parity and oracle results](https://huggingface.co/datasets/mike-7777777/harbor-parity-experiments/tree/0fdc8d523e2466bc1153771907754650a4c9d738/adapters/reward-bench-2) (Hugging Face dataset pinned to commit `0fdc8d5`; [SHA256 manifest](https://huggingface.co/datasets/mike-7777777/harbor-parity-experiments/blob/0fdc8d523e2466bc1153771907754650a4c9d738/manifests/reward-bench-2.sha256), whose own SHA256 is `188dafd38d19cef7eb9324382a0d8ae9764ec93e701a62bc79aeb2ae4761183f`), [Hugging Face parity PR](https://huggingface.co/datasets/harborframework/parity-experiments/discussions/289) (the shared dataset is over the Hugging Face limit of 1,000,000 files, see [#129](https://github.com/harbor-framework/adapters/issues/129); the same files go there once the limit allows); the dataset PR is added when it is opened.

Checks so far:

- Oracle, Harbor 0.26.0 with the separate verifier, all 1,865 tasks: reward 1.0 on every task.
- No-op agent, all 1,865 tasks: reward 0.0 on every task.
- Wrong answers on the 302 parity tasks (the next letter for best-of-4, equal ratings for Ties): reward 0.0 on every task.
- Prompt and answer key: for all 1,763 best-of-4 tasks, `instruction.md` equals the output of the harness's `format_judge_answers` on the harness's seed-0 answer order plus the closing line that names the output file, and the answer letter equals the harness's `winner_text`.
- Aggregation: `reward-bench-2-score` applied to the per-row scores of a published run (`eval-set-scores/openai/gpt-4.1-2025-04-14.json`) reproduces the five best-of-4 subset scores of that run and gives the same Ties score as the harness's `process_single_model`.

## Notes & Caveats

**Benchmark score versus mean reward.** The mean reward that Harbor prints weights every task equally. The benchmark score is the unweighted mean of six subset scores, and the Ties score is not a mean over tasks. Use `reward-bench-2-score` for the benchmark's own aggregation. The five best-of-4 subset scores follow the harness's default protocol; the Ties score and the average carry the Ties difference described below.

**Best-of-4 protocol.** The harness offers two ways to score a generative judge on the best-of-4 subsets: the four-way prompt (its default) and separate 1-10 ratings (`--score_w_ratings`). This adapter implements the default. The paper reports, per model, the better of the two, so a leaderboard entry of an API model can come from the ratings protocol; the published `gpt-4.1-2025-04-14` results, for example, contain the one-third credits that only ratings produce.

**Instruction text.** The harness sends the judge prompt as a system message and the question with the four responses as a user message. The task joins them with a blank line, as the harness does for models without a system role (`--no_system_prompt`), and adds one closing sentence that names the output file. Every agent gets this prompt; the harness's separate prompt for Gemini models (`prompt_v2_gemini`, which asks for the verdict alone) is not used.

**Ties (differs from the harness).** The harness always scores Ties with ratings ([`run_generative_v2.py` L251-L252][ties-ratings]) and rates each response in a separate model call ([loop][ties-loop], [prompt][ties-prompt]). One Harbor task holds one row, so the agent sees all responses of a prompt (11 to 38) and rates them in one session. A judge that sees the responses side by side can rate them relative to each other, so Ties scores from this adapter, and the overall score that includes them, are not directly comparable with the paper or the leaderboard. The task prompt is the harness's Ties rating prompt adjusted for several responses: the request for an explanation before the rating is dropped, and the ratings are written as JSON. Responses are shown in sha256 order of task id and index, because the dataset lists the correct ones first. The reward of a Ties task is the prompt's accuracy term: 1 if every correct response is rated above every incorrect one, else 0. The other terms of the Ties score compare prompts within the subset, and the scorer computes them from the ratings in `grading.json`. The harness's formula tops out at 1.01 (weights 0.3, 0.3, 0.2, 0.2 plus a 0.01 margin term), which is what the oracle ratings score. Parity runs apply the same Ties task on both sides.

**Unparseable and missing output (missing output differs from the harness).** A judgment without a verdict scores 0.25, as in the harness: [`process_judgement`][parse] returns `error`, which [maps to 0.25][quarter]. A missing or empty `judgment.txt` scores 0, so an agent that does nothing gets no credit; in the harness a judge that returns nothing, or whose API call fails ([`$ERROR$`][api-error]), scores 0.25 like any other output without a verdict. A Ties rating that is missing or not an integer from 1 to 10 counts as -1, as in the harness ([`get_single_rating`][invalid-rating]).

**Verdict parsing.** The harness checks `[[A]]`, `[[B]]`, `[[C]]`, `[[D]]` in that order and takes the first one found anywhere in the judgment. The verifier does the same, so a judgment should contain only one bracketed verdict.

**Safety subset.** Safety rows include harmful requests and responses that comply with them. An agent that refuses to judge such a row is scored by the rules above.

**Source ids.** `metadata.source_id` in `task.toml` keeps the row id of the dataset. The 40 ids that appear in more than one subset are distinct rows.

## Installation / Prerequisites

- Docker, and Harbor 0.26 or later.
- `uv` for generating tasks and running the scorer; Python 3.13 is fetched by `uv` if needed.
- Network access to Hugging Face when generating tasks (about 7 MB; `HF_ENDPOINT` selects a mirror). Running tasks needs no network beyond what the agent itself uses.
- API keys for the chosen agent.

## Troubleshooting

- Download errors from `huggingface_hub`: Hugging Face is unreachable; set `HF_ENDPOINT` to a mirror.
- `numpy produced answer positions that differ from the pinned official seed-0 positions`: the installed numpy draws a different stream; use the version in `uv.lock`.
- `trial(s) have no reward or grading.json`: those trials failed before or during verification; rerun them, or pass `--allow-ungraded` to score the rest.
- `task ... was graded more than once`: the job holds several attempts of a task; the scorer takes one run at a time.

## Citation

```bibtex
@misc{malik2025rewardbench2,
  title         = {RewardBench 2: Advancing Reward Model Evaluation},
  author        = {Malik, Saumya and Pyatkin, Valentina and Land, Sander and Morrison, Jacob and Smith, Noah A. and Hajishirzi, Hannaneh and Lambert, Nathan},
  year          = {2025},
  eprint        = {2506.01937},
  archivePrefix = {arXiv}
}
```

## Authors & Contributions

Adapter: Qumeng Sun (@Mike-7777777). Issues and questions: open an issue in this repository or ask in `#adapters-spam` on the Harbor Discord.

[ties-ratings]: https://github.com/allenai/reward-bench/blob/59f6ff7005a9f9484727ca27f5383420875e5dbf/scripts/run_generative_v2.py#L251-L252
[ties-loop]: https://github.com/allenai/reward-bench/blob/59f6ff7005a9f9484727ca27f5383420875e5dbf/rewardbench/generative_v2.py#L492-L493
[ties-prompt]: https://github.com/allenai/reward-bench/blob/59f6ff7005a9f9484727ca27f5383420875e5dbf/rewardbench/generative_v2.py#L301-L317
[parse]: https://github.com/allenai/reward-bench/blob/59f6ff7005a9f9484727ca27f5383420875e5dbf/rewardbench/generative_v2.py#L167-L177
[quarter]: https://github.com/allenai/reward-bench/blob/59f6ff7005a9f9484727ca27f5383420875e5dbf/scripts/run_generative_v2.py#L303-L308
[api-error]: https://github.com/allenai/reward-bench/blob/59f6ff7005a9f9484727ca27f5383420875e5dbf/rewardbench/generative_v2.py#L531-L549
[invalid-rating]: https://github.com/allenai/reward-bench/blob/59f6ff7005a9f9484727ca27f5383420875e5dbf/rewardbench/generative_v2.py#L349-L391
