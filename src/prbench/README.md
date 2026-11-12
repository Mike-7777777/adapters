# PRBench → Harbor Adapter

## Overview

This adapter turns the 1,100 public conversations of PRBench (600 Finance, 500 Legal) into Harbor tasks. Each task asks the agent to write the assistant's next reply to a professional finance or legal conversation; the verifier grades the reply against the expert-written weighted rubric with an LLM judge, using the grader prompt and scoring of the original harness.

- Source: Hugging Face [`ScaleAI/PRBench`](https://huggingface.co/datasets/ScaleAI/PRBench), CC-BY-4.0, pinned to revision `01947c6e`.
- Original harness: [scaleapi/PRBench](https://github.com/scaleapi/PRBench) `evals.py` (MIT); the original evaluation calls a model API directly, with no agent.
- Adapted size: 1,100 tasks, every record of the `finance` and `legal` splits. The `finance_hard` (300) and `legal_hard` (250) splits are subsets of those records and are exposed as `--split` choices and a `hard` keyword, not as extra tasks.

## What is PRBench?

[PRBench](https://arxiv.org/abs/2511.11562) (Scale AI, 2025) evaluates open-ended professional reasoning in finance and law. Experts wrote 1,100 conversations (656 single-turn, 444 with 2 to 10 user turns) and 18,692 rubric criteria, 5 to 30 per task. Every criterion carries an integer weight from -10 to 10: positive for what a good reply does, negative for what it must not do.

An LLM judge (`o4-mini` in the paper) decides whether each criterion is met. Task score = sum of met weights / sum of positive weights. The benchmark score, `mean_clipped` in the original code, is the mean task score floored at 0. The original code also reports a min-normalized score that shifts each task by its total negative weight.

## Adapter Features

- Upstream-faithful grading. `tests/grader.py` reuses the PRBench grader prompt verbatim, renders the conversation the same way, sends one judge call per criterion through the Responses API with no sampling parameters, parses verdicts with the upstream rule and computes both upstream scores. Checked against the upstream functions on all 1,100 records.
- Three numbers per task in `reward.json`: `reward`, `score` and `score_normalized` (see Reward range under Notes & Caveats).
- Multi-turn conversations and reference texts. Earlier turns, including the model replies stored in the dataset, are given to the agent in `/workspace/conversation.md`; reference texts are placed in front of the prompt of their turn.
- The verifier grades from its own copy of the conversation and rubric (`tests/task.json`), so editing files under `/workspace` cannot change what is graded. The rubric is never placed where the agent can read it.
- Pinned data: each parquet file is sha256-checked against the pinned revision before tasks are built.
- Dataset canaries are kept: every task carries the `prbench:sample:...` canary strings of its record.

## Generated Task Structure

```
<domain>-<task>/                  # e.g. finance-ea67e314b6c2e8fc70627c19
├── task.toml                     # name scaleai/prbench__<domain>-<task>
├── instruction.md                # asks for the next reply; holds the canaries
├── environment/
│   ├── Dockerfile                # copies conversation.md to /workspace
│   └── conversation.md
├── solution/solve.sh             # oracle reply (see Notes & Caveats)
└── tests/
    ├── test.sh
    ├── grader.py
    └── task.json                 # conversation, rubric with weights, canaries
```

The agent writes its reply to `/workspace/response.txt`.

## Run Evaluation / Harness

Once the dataset is published to the registry:

```bash
harbor run -d scaleai/prbench -a <agent> -m "<model>"
```

From locally generated tasks. The verifier calls the judge through the OpenAI Responses API:

```bash
export OPENAI_API_KEY=...
# optional: OPENAI_BASE_URL, JUDGE_MODEL (default gpt-4.1), JUDGE_REASONING_EFFORT
# (default: not sent, as upstream), JUDGE_CONCURRENCY (default 8)
harbor run -p datasets/prbench -a <agent> -m "<model>"
```

From the repository root with the reference config (oracle agent by default; see [Oracle solutions](#notes--caveats) for `ORACLE_MODE`). This run calls no model and needs no API key:

```bash
ORACLE_MODE=true harbor run -c src/prbench/run_prbench.yaml
```

A single trial:

```bash
harbor trial start -p datasets/prbench/<task_id> -a <agent> -m "<model>"
```

One full grading pass is 18,692 judge calls (9,865 Finance, 8,827 Legal).

## Usage: Create Task Directories

```bash
cd src/prbench
uv run prbench --output-dir ../../datasets/prbench
```

Options: `--limit N`, `--task-ids ID ...`, `--overwrite`, `--split {full,finance,legal,finance_hard,legal_hard,hard,parity}`, `--source-dir DIR` (local copies of the pinned parquet files; their sha256 is still checked), `--oracle-dir DIR` (`<task_id>.txt` oracle replies for `solution/solve.sh`; default: an outline of the positive rubric criteria), `--reference-texts {prepend,omit}`.

Unit tests: `uv run pytest tests`.

## Comparison with Original Benchmark (Parity)

Pending; the plan below is a proposal until agreed with the Harbor team. Scenario 2: the original harness has no agent, so a fork of scaleapi/PRBench gets a runner that produces each reply with the same agent, instruction and `conversation.md` as the Harbor task, then grades the replies through the original `evals.py` with `final_response_source: prefilled`. Both sides use the same judge model. Benchmark score on each side: the mean unclipped task score, floored at 0 after averaging.

**Parity subset.** `--split parity` generates the 200 tasks listed in `src/prbench/parity_task_ids.txt`, 100 per domain, which the original side reads too. Within a domain the subset is stratified by hard-subset membership and topic: each stratum gets seats in proportion to its size (largest remainder), and within a stratum records are taken in sha256 order of `<domain>-<task>`. `select_parity_ids` in `adapter.py` reproduces the list, and generation stops if the two differ.

Harbor side, from the repository root:

```bash
uv run --project src/prbench prbench --split parity --output-dir datasets/prbench-parity
harbor run -c src/prbench/prbench_parity.yaml
```

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|---|---|---|---|---|---|---|
| pending | pending | mean_clipped | 3 | 200 (18% of 1,100) | pending | pending |

Checks so far, none of which call a model:

- Harbor 0.25.0, all 1,100 tasks, oracle agent with `ORACLE_MODE=true`: 1,100 trials, 0 exceptions, reward 1.0 on every task.
- Harbor 0.25.0, all 1,100 tasks, `nop` agent: 1,100 trials, 0 exceptions, reward 0 and `score` 0 on every task.
- The ported functions (conversation building, rubric weights, judge prompt, verdict parsing, both scores) agree with the upstream code on all 1,100 records.
- Unmodified upstream `evals.py` (`final_response_source: prefilled`) and this grader, given the same replies and a deterministic local stand-in for the judge endpoint, produce identical task scores on all 1,100 tasks, and the 18,692 judge prompts are byte-identical on the two sides (with `--reference-texts omit`, the upstream behavior).
- Harbor 0.25.0, 7 tasks, with the stand-in judge: the default oracle outline reaches reward 1.0 through the judge path, and replies that quote only negative criteria get the expected negative `score` and reward 0.

## Notes & Caveats

**Judge model.** The paper's judge, `o4-mini`, was shut down on the OpenAI API on 2026-10-23 ([deprecations](https://developers.openai.com/api/docs/deprecations)). The default is `gpt-4.1`, the other judge the paper validated against human experts (Cohen's kappa 0.605, against 0.603 for `o4-mini`); OpenAI shuts it down on 2027-01-14. Scores are directly comparable with the paper's only under `o4-mini`; `JUDGE_MODEL` selects it on an endpoint that still serves it, or any other model. `grading.json` records the model name the endpoint reports, so parity runs can state the exact snapshot.

**Reward range.** A task score can be negative when negative criteria are met (down to -0.74 on the worst task). The paper floors only the mean over tasks, so the benchmark score is the mean of `score` (the unclipped task score), floored at 0. `reward` floors each task so it stays in [0, 1], and averages slightly higher.

**Reference texts.** 17 tasks (15 Finance, 2 Legal) come with reference texts. The paper says they are prepended to the prompt. Upstream commit `7b2df0a` does not actually prepend them: `process_reference_texts` tests `isinstance(value, list)`, and the column arrives as a NumPy array (verified with `datasets` 5.2.0 and `pandas` 3.1.1). This adapter follows the paper; `--reference-texts omit` reproduces the upstream behavior, and the parity fork carries the one-line fix.

**Judge failures and empty replies.** Two departures from upstream. Upstream counts a criterion as not met when the judge call still fails after five attempts; here the verifier exits non-zero instead, so Harbor records a verifier error rather than a lowered score (a rejected key or unknown model fails on the first attempt). Harbor's job mean counts a trial without a reward as 0, so rerun errored trials before reading it. And a missing or empty reply scores 0 without a judge call, where upstream would send the empty reply to the judge. Unparseable or empty judge output counts as not met, as upstream.

**Oracle solutions.** The dataset has no reference answers: the last turn of every record has no reply, and the `scratchpad` field holds expert notes, not an answer. By default `solution/solve.sh` writes an outline of the task's positive rubric criteria, taken verbatim from the rubric; `--oracle-dir` replaces it with replies written for each task. The oracle run uses `ORACLE_MODE=true`, the approach of the merged `clbench` adapter: the verifier skips the judge and checks that the task data loads and that the oracle wrote a non-empty reply. It validates the image build, the file layout and the verifier wiring on every task; it does not measure reply quality. With about 17 criteria per task, a judged full score needs every criterion judged as intended, so a small share of tasks would fall below 1.0 on any single judge pass even with a strong reply. Agent runs never set `ORACLE_MODE` and always go through the judge.

**Earlier turns.** In multi-turn tasks the earlier assistant turns are replies of the models the experts talked to (`mistral-medium-latest`, `deepseek-v3p1`, `gpt-oss-20b`), exactly as stored in the dataset. One Finance record (`d6f0165d275fdd3b4f9f87bc`) lists 5 turns and holds 4 prompts; it is kept as stored.

**Web access.** The paper's main results are without web search. Harbor agents can browse unless the environment forbids it.

## Installation / Prerequisites

- Docker, and Harbor 0.25 or later (Harbor 0.25 clears `reward.json` before each verification).
- Network access when generating tasks (Hugging Face), when building images (PyPI for the `openai` package) and at verification time (the judge endpoint).
- An API key for the judge model, for agent runs.

## Troubleshooting

- `sha256 mismatch`: the downloaded file differs from the pinned revision; check `HF_ENDPOINT` or the `--source-dir` files.
- `download failed after 3 attempts`: Hugging Face is unreachable; set `HF_ENDPOINT` to a mirror or pass `--source-dir`.
- Verifier error with `Missing credentials`: an agent run needs `OPENAI_API_KEY` for the judge.
- Verifier error with `judge failed after N attempts`: the judge endpoint kept failing or rejected the request; check `OPENAI_BASE_URL`, `JUDGE_MODEL` and the key.
- `task ids not in split`: `--task-ids` takes generated task ids such as `finance-<task>`, and they must belong to the chosen `--split`.

## Citation

```bibtex
@misc{akyurek2025prbench,
  title         = {PRBench: Large-Scale Expert Rubrics for Evaluating High-Stakes Professional Reasoning},
  author        = {Aky{\"u}rek, Afra Feyza and Gosai, Advait and Zhang, Chen Bo Calvin and Gupta, Vipul and Jeong, Jaehwan and Gunjal, Anisha and Rabbani, Tahseen and Mazzone, Maria and Randolph, David and Meymand, Mohammad Mahmoudi and Chattha, Gurshaan and Rodriguez, Paula and Mares, Diego and Singh, Pavit and Liu, Michael and Chawla, Subodh and Cline, Pete and Ogaz, Lucy and Hernandez, Ernesto and Wang, Zihao and Bhatter, Pavi and Ayestaran, Marcos and Liu, Bing and He, Yunzhong},
  year          = {2025},
  eprint        = {2511.11562},
  archivePrefix = {arXiv}
}
```

## Authors & Contributions

Adapter: Qumeng Sun (@Mike-7777777). Issues and questions: open an issue or ask in `#adapters-spam` on the Harbor Discord.
