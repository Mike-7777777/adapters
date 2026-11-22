# HealthBench Professional → Harbor Adapter

## Overview

This adapter turns the 525 public conversations of HealthBench Professional into Harbor tasks. Each task asks the agent to write the assistant's next reply to a clinician conversation; the verifier grades the reply against the physician-written rubric with an LLM judge, using the grader prompt and scoring of simple-evals.

- Source: Hugging Face [`openai/healthbench-professional`](https://huggingface.co/datasets/openai/healthbench-professional), MIT license, pinned to revision `349962fd`.
- Original evaluation: [openai/simple-evals](https://github.com/openai/simple-evals/tree/652c89d0ca9df547706735883097e9537d40dc47) `healthbench_eval.py` at `652c89d`, which calls a model API directly, with no agent. The dataset README calls it the unofficial external implementation; the paper's numbers come from OpenAI's internal implementation.
- Adapted size: 525 tasks.
- Oracle: on Harbor 0.26.0 with the separate verifier, 523 of 525 tasks reach reward 1.0 on the first attempt and the other two on each of three retries; see [Notes & Caveats](#notes--caveats). Parity on the 150-task subset matches: 3 runs per side, and the run ranges overlap; see [Comparison with Original Benchmark](#comparison-with-original-benchmark-parity).
- Links: adapter PR [harbor-framework/adapters#24](https://github.com/harbor-framework/adapters/pull/24); dataset PR to harbor-datasets (pending); parity results on [Hugging Face](https://huggingface.co/datasets/mike-7777777/harbor-parity-experiments/tree/ff1ec151bfb9c092c660e826476bc47e1d30db44/adapters/healthbench-professional); fork for the original side [Mike-7777777/simple-evals@6503101](https://github.com/Mike-7777777/simple-evals/tree/6503101e1b669f7040f27ddd4d063090ad7765e5).

## What is HealthBench Professional?

[HealthBench Professional](https://arxiv.org/abs/2604.27470) (OpenAI, 2026) evaluates model replies to real conversations from clinicians using ChatGPT for Clinicians. The public set has 525 conversations (115 multi-turn) across consult, research and writing use cases, each with 1-5 physician-written rubric items (1135 in total, 213 of them negative).

An LLM judge (`gpt-5.4-2026-03-05`, reasoning low, in the paper and in simple-evals) decides whether each rubric item is met. Task score = sum of met item points / sum of positive points, minus a length adjustment of 0.0147 × (reply characters − 2000) / 500, which adds a little to replies under 2000 characters. The benchmark score is the mean task score, clipped to [0, 1].

## Adapter Features

- Pointer-only tasks. The dataset README asks that examples not be shown in plain text online, so a task holds only its record id and the dataset canary. `hbp_data.py` fetches the data from the pinned revision and checks its sha256; a mismatch stops the build or the grading.
- Separate verifier. Grading runs in its own container, built from `tests/Dockerfile` with the judge client preinstalled. Of the agent's files, Harbor copies in only the declared artifact `/workspace/response.txt` (plus its standard `/logs/artifacts/` directory), and the grader fetches the conversation and rubric with its own copy of `hbp_data.py`, so nothing else the agent does can change what is graded.
- Grader parity. `tests/grader.py` reuses the simple-evals grader prompt verbatim and its scoring (MIT, attribution in the file header). Unparseable judge output is retried a bounded number of times; after that the verifier exits non-zero instead of recording a score.
- Reward in [0, 1] plus the unclipped score, and `metric.py`, a dataset-level metric that reports the benchmark score; see [Notes & Caveats](#notes--caveats).
- Encrypted oracle. `solution/solve.sh` carries the oracle reply encrypted with the dataset's canary string and decrypts it when it runs.

## Generated Task Structure

```
<task_id>/
├── task.toml
├── instruction.md            # asks the agent to read /workspace/conversation.md
├── environment/
│   ├── Dockerfile            # writes /workspace/conversation.md at build time
│   ├── hbp_data.py
│   └── record-<task_id>
├── solution/solve.sh         # oracle reply, encrypted with the canary (needs --oracle-dir)
└── tests/
    ├── Dockerfile            # verifier image (separate from the agent's)
    ├── test.sh
    ├── grader.py             # fetches conversation and rubric itself
    ├── hbp_data.py
    └── record-<task_id>
```

The agent writes its reply to `/workspace/response.txt`. The record id is the name of the `record-<task_id>` file, so the build contexts of two tasks differ only in that name ([harbor#3827](https://github.com/harbor-framework/harbor/issues/3827)).

## Run Evaluation / Harness

Once the datasets are registered (tag `v1.0` requested for both):

```bash
export OPENAI_API_KEY=...
harbor run -d openai/healthbench-professional -a <agent> -m "<model>"
harbor run -d openai/healthbench-professional-parity -a <agent> -m "<model>"   # the 150-task subset
```

From generated task directories, at the repository root:

```bash
export OPENAI_API_KEY=...
# optional: OPENAI_BASE_URL, GRADER_MODEL (default gpt-5.4-2026-03-05), HF_ENDPOINT
harbor run -c src/healthbench-professional/run_healthbench-professional.yaml   # oracle, with metric.py
harbor run -p datasets/healthbench-professional -a <agent> -m "<model>"
harbor trial start -p datasets/healthbench-professional/<task_id> -a <agent> -m "<model>"
```

The job configs add `metric.py` as a uv-script metric, which prints `healthbench_score`, the benchmark score; it needs `uv` on `PATH`. Without it, Harbor reports the mean of `reward` and of `score` per job. One full grading pass over 525 replies is 1135 judge calls.

## Usage: Create Task Directories

```bash
cd src/healthbench-professional
uv run healthbench-professional --output-dir ../../datasets/healthbench-professional --oracle-dir <dir>
```

Options: `--limit N`, `--task-ids ID ...`, `--overwrite`, `--source PATH` (a local copy of the pinned file; its sha256 is still checked), `--oracle-dir DIR` (`<task_id>.txt` oracle replies for `solution/solve.sh`; without it `solve.sh` exits 1), `--split {full,parity}` (`parity`: the 150-task parity subset).

The 525 oracle replies are on Hugging Face, encrypted: from the [parity folder](https://huggingface.co/datasets/mike-7777777/harbor-parity-experiments/tree/ff1ec151bfb9c092c660e826476bc47e1d30db44/adapters/healthbench-professional), download `oracle/oracle_replies.tar.gz.enc` and `scripts/decrypt.py` into the same layout and run `python3 scripts/decrypt.py`; the replies land in `oracle/oracle_replies/`, the directory to pass as `--oracle-dir`. The tasks submitted to harbor-datasets are generated with them, so `solve.sh` there works without this step.

Unit tests: `uv run pytest tests`.

## Comparison with Original Benchmark (Parity)

Scenario 2: a claude-code sampler added to a fork of simple-evals ([Mike-7777777/simple-evals@6503101](https://github.com/Mike-7777777/simple-evals/tree/6503101e1b669f7040f27ddd4d063090ad7765e5), on top of simple-evals `652c89d`). Both sides ran `claude-code` 2.1.322 with `claude-haiku-5-5`, the same instruction and conversation file, and the same judge, `gpt-5.4` at low reasoning, through Harbor's parity API (see [Notes & Caveats](#notes--caveats)). The Harbor side ran on Harbor 0.26.0 with the separate verifier. Run score on each side: the mean unclipped task score, clipped to [0, 1] after averaging.

**Parity subset.** `--split parity` generates 150 tasks chosen by `select_parity_ids` in `adapter.py`. They are stratified by use case and type: each of the 6 strata gets seats in proportion to its size (largest remainder), and within a stratum records are taken in sha256(id) order. The dataset file is pinned by sha256, so the subset is fixed.

| Use case | Type | Records | In subset |
|---|---|---|---|
| consult | good_faith | 143 | 41 |
| consult | red_teaming | 93 | 27 |
| research | good_faith | 140 | 40 |
| research | red_teaming | 7 | 2 |
| writing | good_faith | 51 | 14 |
| writing | red_teaming | 91 | 26 |
| all | | 525 | 150 |

Original side, Python 3.12, with the settings of the parity runs (`HARBOR_PARITY.md` in the fork has the details):

```bash
git clone https://github.com/Mike-7777777/simple-evals simple_evals_hbp
git -C simple_evals_hbp checkout 6503101e1b669f7040f27ddd4d063090ad7765e5
python3.12 -m venv .venv && . .venv/bin/activate
pip install openai==3.48.0 anthropic==2.7.0 pandas==3.1.1 blobfile==3.3.0 jinja2==3.1.6 tqdm==4.70.1 \
  numpy==2.5.4 requests==2.34.2 scipy==1.18.1 tabulate==0.10.0 setuptools==80.10.2
pip install --no-build-isolation "human-eval @ git+https://github.com/openai/human-eval"
export OPENAI_API_KEY=... OPENAI_BASE_URL=... ANTHROPIC_API_KEY=... ANTHROPIC_BASE_URL=...
export GRADER_MODEL=gpt-5.4 CLAUDE_CODE_VERSION=2.1.322
simple_evals_hbp/run_hbp_parity.sh simple_evals_hbp/healthbench_professional_parity_ids.txt 8   # 8 threads
```

Harbor side (the verifier image installs `openai==3.46.0`):

```bash
cd src/healthbench-professional
uv run healthbench-professional --split parity --oracle-dir <dir> \
  --output-dir ../../datasets/healthbench-professional-parity
cd ../..
export OPENAI_API_KEY=... OPENAI_BASE_URL=... ANTHROPIC_API_KEY=... ANTHROPIC_BASE_URL=...
export GRADER_MODEL=gpt-5.4
uvx --from harbor==0.26.0 harbor run -c src/healthbench-professional/healthbench-professional_parity.yaml
```

The fork's `healthbench_professional_parity_ids.txt` holds the same 150 ids as `--split parity`.

| Agent | Model | Metric | Runs | Dataset Size | Original (mean ± SEM) | Harbor (mean ± SEM) |
|---|---|---|---|---|---|---|
| claude-code@2.1.322 | claude-haiku-5-5 | length-adjusted score | 3 | 150 (29% of 525) | 0.6165 ± 0.0171 | 0.5988 ± 0.0040 |

Mean ± sample SEM over runs. Run scores: original 0.5974, 0.6506, 0.6014; Harbor 0.6058, 0.5987, 0.5920. The run ranges overlap. Per task, averaged over the three runs, Harbor minus original is −0.018 on average (standard error 0.016, 150 tasks). Every run graded all 150 tasks. Claude Code exited normally in all 900 trials; in 1 original and 3 Harbor trials it ended without writing a reply, and the empty reply is graded as an empty string, as simple-evals does. Agent cost reported by Claude Code, summed over the three runs: $1.40 original, $1.46 Harbor; the judge costs about $2 to $2.50 per 150-task run (329 judge calls).

Harbor shows the mean of each reward key, `reward` first. The mean per-task clipped `reward` is higher than the benchmark score: original 0.6434 ± 0.0137 (0.6264, 0.6706, 0.6333), Harbor 0.6320 ± 0.0031 (0.6339, 0.6259, 0.6363), and these ranges overlap too.

Results of every attempt, the oracle runs and the aggregation script are on Hugging Face: [mike-7777777/harbor-parity-experiments](https://huggingface.co/datasets/mike-7777777/harbor-parity-experiments/tree/ff1ec151bfb9c092c660e826476bc47e1d30db44/adapters/healthbench-professional), pinned, with a SHA256 manifest; the shared parity-experiments dataset is over Hugging Face's file limit ([#129](https://github.com/harbor-framework/adapters/issues/129)). Agent logs, replies and judge outputs contain conversation and rubric text, so they are encrypted with the dataset's canary string; `scripts/decrypt.py` there unpacks them.

Other checks:

- Physician reference replies, all 525, graded with the simple-evals prompt and scoring (judge `gpt-5.4`, reasoning low, 2026-10-08): length-adjusted mean 0.429; the paper reports 0.437 (section 5.1). Per-task scores: `scores/physician_reference_scores.jsonl` on Hugging Face.
- Generated task directories contain no dataset text (unit test with sentinel records), and no oracle reply text in the clear (all 525 `solve.sh` checked, each decrypts to its reply byte for byte).
- Every Harbor parity trial graded against its own task's rubric, and its agent read its own task's conversation (checked per trial).

## Notes & Caveats

**Reward range.** A task score can be below 0 (negative rubric items, long replies) or above 1 (the length adjustment adds a little for replies under 2000 characters). The paper clips only the mean over tasks to [0, 1]; clipping each task first moves the physician mean from 0.429 to 0.490. The verifier therefore writes `reward.json` with two keys: `reward`, the task score clipped to [0, 1] as the guide requires of `test.sh`, and `score`, the unclipped task score. Harbor averages each key separately; the benchmark score is the mean of `score`, clipped to [0, 1], which `metric.py` reports as `healthbench_score`. Harbor runs `metric.py` from a job config's `metrics` (as in this adapter's configs) or from a registered dataset that lists it.

**Prompt and environment.** The original evaluation sends the conversation to the model as chat messages. Here the agent gets `instruction.md`, which opens with "You are an AI assistant for clinicians.", and reads the conversation from `/workspace/conversation.md`, one `### <role>` heading per message. The agent's container has internet access, Harbor's default, and Claude Code's WebSearch and WebFetch tools stay enabled; the original side of the parity runs used the same instruction, file and tools. A reply file that is not valid UTF-8 is graded with the invalid bytes replaced by U+FFFD.

**Web access and the public dataset.** The dataset with its rubrics is public on Hugging Face; its README asks that examples not be revealed online, to help prevent cheating by models with internet access. All 1370 Claude Code sessions of the parity work (both sides, all attempts, the 10-task checks included) were scanned for network use: 16 sessions made 32 WebSearch calls and 2 WebFetch calls. The search results came from 42 domains, most often ncbi.nlm.nih.gov, researchgate.net and clinicaltrials.gov; the two fetches went to reference.medscape.com and link.springer.com. No call, search result or fetched page mentions huggingface.co, hf.co, hf-mirror.com, HealthBench or simple-evals, and no shell command used curl, wget, pip, git or a Python HTTP client. The per-attempt counts are in `run_logs/network_scan/` on Hugging Face. For runs that must rule this out, Harbor 0.25 and later can restrict the agent with `[agent] network_mode = "allowlist"` and `allowed_hosts` in `task.toml`; Harbor then also disables the agent's web tools.

**Oracle solutions.** Physician replies score 0.43 on average, so they cannot serve as the oracle. Oracle replies are written against each rubric with AI assistance, kept at or under 2000 characters so the length adjustment does not apply, and accepted only after three consecutive full-score gradings. In Harbor 0.25.0, on the earlier task layout without the separate verifier, all 525 reach reward 1.0 (2026-11-04). In Harbor 0.26.0 with the separate verifier, 523 reach 1.0 on the first attempt; in the other two the judge marked a negative rubric item as met (rewards 0.217 and 0.580), and the same replies reach 1.0 in each of three more attempts. The replies are passed in with `--oracle-dir` and embedded in each generated `solution/solve.sh`, encrypted as BrowseComp ships its answers (XOR with the repeated SHA-256 of the canary, then base64); the agent never sees `solve.sh`. They are AI-written text, not dataset records.

**Judge.** Results are only comparable with the paper under `gpt-5.4-2026-03-05`. The parity runs and the Harbor 0.26.0 oracle used Harbor's parity API (`app.ppapi.ai`, [parity API instructions](https://github.com/harbor-framework/harbor/blob/718a64636a8589bff8154a3be20fc364d1937a54/adapters/parity_api_instructions.md)), which serves `gpt-5.4` but not the dated name; the parity configs therefore set `GRADER_MODEL=gpt-5.4`. The physician check and the Harbor 0.25.0 oracle used ModelVerse, another OpenAI-compatible gateway that serves `gpt-5.4`. Neither endpoint reports which snapshot answers. The agent model `claude-haiku-5-5` has no dated form: Anthropic's [models overview](https://platform.claude.com/docs/en/about-claude/models/overview) says that the dateless IDs used from the 4.6 generation on are pinned snapshots, and every Claude Code reply in the parity runs reports `claude-haiku-5-5`.

**Run order.** After the 10-task check on both sides, the two sides started their three runs at the same time (2026-11-21 04:49 UTC). The original side, with no images to build, finished all three by 05:22, before Harbor run 1 finished at 06:22. That first Harbor batch was discarded: in 5 of its 450 trials the verifier graded against another task's rubric, and in 2 the agent read another task's conversation, because image builds reused a previous task's record file ([harbor#3827](https://github.com/harbor-framework/harbor/issues/3827)). Its run scores were 0.5879, 0.6233 and 0.5902. The adapter now keeps the record id in a file name (`6fa4b4e`), and the three Harbor runs in the table ran again (09:52-12:59), after every image was checked to hold its own task. The discarded runs are on Hugging Face as `harbor_parity/discarded-run{1,2,3}.tar.gz.enc`; `scripts/decrypt.py` there unpacks them into `harbor_parity/discarded-run{1,2,3}/`.

**Cost.** The parity work cost about $24, the discarded batch and both 10-task checks included. The API key shows $18.46 for the stretches when only these runs used it (the original runs with the first 129 Harbor trials, and the three reported Harbor runs); the rest of the discarded batch, run while the key was shared, adds about $5.20 at the cost per trial of the reported Harbor runs, and the 10-task checks about $0.25. Agent cost reported by Claude Code is $4.34 of the total; the rest is the judge.

## Installation / Prerequisites

- Docker, and Harbor 0.25 or later; parity ran on 0.26.0, and the representative task and an oracle sample were checked on 0.27.0. Harbor 0.25 clears reward files before each verification; 0.24 does not.
- Network access at build time (Hugging Face) and at verification time (Hugging Face and the judge endpoint).
- An OpenAI API key with access to `gpt-5.4-2026-03-05`.
- `uv` on `PATH` for `metric.py`.

## Troubleshooting

- `sha256 mismatch`: the downloaded file differs from the pinned revision; check `HF_ENDPOINT` or the `--source` file.
- `download failed after 3 attempts`: Hugging Face is unreachable. `HF_ENDPOINT` points the generator and the verifier at a mirror; the image build always downloads from `huggingface.co`.
- Verifier error with `grader failed after 4 attempts`: the judge kept returning unparseable output or errors; check `OPENAI_BASE_URL`, `GRADER_MODEL` and the key.
- `no oracle reply was generated for this task`: the task was generated without `--oracle-dir`.
- `Failed to compute custom metric`: `metric.py` runs with `uv run`; install `uv`, or drop the `uv-script` entry from the job config.

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
