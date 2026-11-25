# PRBench → Harbor Adapter

## Overview

This adapter turns the 1,100 public conversations of PRBench (600 Finance, 500 Legal) into Harbor tasks. Each task asks the agent to write the assistant's next reply to a professional finance or legal conversation; the verifier grades the reply against the expert-written weighted rubric with an LLM judge, using the grader prompt and scoring of the original harness.

- Source: Hugging Face [`ScaleAI/PRBench`](https://huggingface.co/datasets/ScaleAI/PRBench/tree/01947c6ef913df518e6e52626a7b29f132b12d4f), CC-BY-4.0, pinned to revision `01947c6e`.
- Original harness: [scaleapi/PRBench@7b2df0a](https://github.com/scaleapi/PRBench/tree/7b2df0a28bf99ec51263c41b90dd510a740a7711) `evals.py` (MIT); the original evaluation calls a model API directly, with no agent.
- Adapted size: 1,100 tasks, every record of the `finance` and `legal` splits. The `finance_hard` (300) and `legal_hard` (250) splits are subsets of those records and are exposed as `--split` choices and a `hard` keyword, not as extra tasks.

## What is PRBench?

[PRBench](https://arxiv.org/abs/2511.11562) (Scale AI, 2025) evaluates open-ended professional reasoning in finance and law. Experts wrote 1,100 conversations (656 single-turn, 444 with 2 to 10 user turns). The public release holds 18,692 rubric criteria, 5 to 30 per task (the paper reports 19,356). Every criterion carries an integer weight from -10 to 10: positive for what a good reply does, negative for what it must not do.

An LLM judge (`o4-mini` in the paper) decides whether each criterion is met. Task score = sum of met weights / sum of positive weights. The benchmark score, `mean_clipped` in the original code, is the mean task score floored at 0. The original code also reports a min-normalized score that shifts each task by its total negative weight.

## Adapter Features

- Upstream-faithful grading. `tests/grader.py` reuses the PRBench grader prompt verbatim, renders the conversation the same way, sends one judge call per criterion through the Responses API with no sampling parameters, parses verdicts with the upstream rule and computes both upstream scores. Checked against the upstream functions on all 1,100 records.
- Three numbers per task in `reward.json`: `reward`, `score` and `score_normalized` (see Reward range under Notes & Caveats).
- Multi-turn conversations and reference texts. Earlier turns, including the model replies stored in the dataset, are given to the agent in `/workspace/conversation.md`; reference texts are placed in front of the prompt of their turn.
- Separate verifier. Grading runs in its own container, built from `tests/Dockerfile`, which holds the grader, the judge client and the verifier's copy of the conversation and rubric (`tests/task.json`). The agent's container holds only the conversation, and the reply (`/workspace/response.txt`) is the only file passed to the verifier, as an artifact.
- Pinned data: each parquet file is sha256-checked against the pinned revision before tasks are built.
- Dataset canaries are kept: every task carries the `prbench:sample:...` canary strings of its record.

## Generated Task Structure

```
<domain>-<task>/                  # e.g. finance-ea67e314b6c2e8fc70627c19
├── task.toml                     # name scaleai/prbench__<domain>-<task>; separate verifier, reply as artifact
├── instruction.md                # asks for the next reply; holds the canaries
├── environment/
│   ├── Dockerfile                # agent image: conversation.md in /workspace
│   └── conversation.md
├── solution/solve.sh             # oracle reply (see Notes & Caveats)
└── tests/
    ├── Dockerfile                # verifier image: grader, judge client, task.json
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

The 200-task parity subset is published under the `parity` tag (see Parity):

```bash
harbor run -d scaleai/prbench@parity -a <agent> -m "<model>"
```

From locally generated tasks. The verifier calls the judge through the OpenAI Responses API:

```bash
export OPENAI_API_KEY=...
# optional: OPENAI_BASE_URL, JUDGE_MODEL (default gpt-5.6-terra, the parity judge), JUDGE_REASONING_EFFORT
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

Scenario 2: the original harness has no agent, so a [fork of scaleapi/PRBench](https://github.com/Mike-7777777/PRBench/tree/e7b4453a1e4bb543f8726efced94355087cea6fc) (commit `e7b4453`) gets a runner that produces each reply with the same agent, instruction and `conversation.md` as the Harbor task, then grades the replies with the fork's `evals.py` and `final_response_source: prefilled`. The fork changes no scoring code; [`HARBOR_PARITY.md`](https://github.com/Mike-7777777/PRBench/blob/55ffed087ca9fc55af0391fe5fbc3a9d31a837db/HARBOR_PARITY.md) (updated after the runs; the code is at `e7b4453`) lists its changes (dataset revision pin, task-id filter, no sampling arguments for prefilled replies, exit on failed judge calls, the reference-text fix below). Both sides use the same judge model. Benchmark score on each side: the mean unclipped task score, floored at 0 after averaging.

**Parity subset.** `--split parity` generates the 200 tasks listed in `src/prbench/src/prbench/parity_task_ids.txt`, 100 per domain, which the original side reads too. Within a domain the subset is stratified by hard-subset membership and topic: each stratum gets seats in proportion to its size (largest remainder), and within a stratum records are taken in sha256 order of `<domain>-<task>`. `select_parity_ids` in `adapter.py` reproduces the list, and generation stops if the two differ. 5 of the 200 tasks carry reference texts.

Harbor side, from the repository root, with the config the reported runs used:

```bash
uv run --project src/prbench prbench --split parity --output-dir datasets/parity200
export ANTHROPIC_API_KEY=... OPENAI_API_KEY=...   # and ANTHROPIC_BASE_URL / OPENAI_BASE_URL if not the vendors'
JUDGE_MODEL=gpt-5.6-terra uvx --from harbor==0.26.0 harbor run -c src/prbench/prbench_parity.yaml
```

Original side, from the fork at `e7b4453` ([`HARBOR_PARITY.md`](https://github.com/Mike-7777777/PRBench/blob/55ffed087ca9fc55af0391fe5fbc3a9d31a837db/HARBOR_PARITY.md)), one `--trial` per run:

```bash
CLAUDE_CODE_VERSION=2.1.322 python harbor_parity/run_agent.py --model claude-haiku-5-5 --trial 1 --out harbor_runs/run1 --judge_model gpt-5.6-terra --num_threads 12
python evals.py harbor_runs/run1/config_finance.yaml
python evals.py harbor_runs/run1/config_legal.yaml
```

Two runs per side (2026-11-21), on adapter commit `4691d49`; later commits change no file the agent sees (the generated `instruction.md`, `conversation.md` and agent image are byte-identical). They change the grader's handling of `ORACLE_MODE` and of judge requests the endpoint rejects (HTTP 400), neither of which occurred in these runs, the default `JUDGE_MODEL` (now the parity judge), the `difficulty` label and the docs. Claude Code 2.1.322 with `claude-haiku-5-5` and the judge `gpt-5.6-terra` on both sides, Harbor 0.26.0. Scores are the benchmark score in percent, mean ± sample SEM over runs.

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|---|---|---|---|---|---|---|
| claude-code@2.1.322 | claude-haiku-5-5 | mean_clipped, both domains | 2 | 200 (18% of 1,100) | 47.35 ± 0.06 | 47.37 ± 0.33 |
| claude-code@2.1.322 | claude-haiku-5-5 | mean_clipped, Finance | 2 | 100 | 48.43 ± 0.66 | 48.25 ± 0.10 |
| claude-code@2.1.322 | claude-haiku-5-5 | mean_clipped, Legal | 2 | 100 | 46.27 ± 0.78 | 46.49 ± 0.76 |

Per-run scores, original / Harbor: both domains 47.29, 47.41 / 47.70, 47.04; Finance 49.09, 47.78 / 48.15, 48.35; Legal 45.49, 47.05 / 47.25, 45.73. The run ranges overlap in all three rows.

Paired per task (each task's two runs averaged on each side), Harbor minus original: +0.0002 ± 0.0059 (SE, n = 200) in task score, that is +0.02 ± 0.59 points; Finance -0.0018 ± 0.0083, Legal +0.0022 ± 0.0084. The two sides' per-task means correlate at 0.92 overall and in each domain.

Every task was graded on both sides: no Harbor exceptions, no failed judge calls, no agent errors. One original-side agent run (run 1, `legal-740a326c927c1ce16ffee6b8`) ended without writing a reply. Upstream sends the empty reply to the judge, which marked one criterion (weight 5) as met, so that run scored 5/53 = 0.094; the Harbor grader scores an empty reply 0 without a judge call (see Notes & Caveats), so the same outcome on the Harbor side would score 0. It is kept as upstream graded it. Cost of the two runs on both sides, agent and judge: at most $202 (the endpoint's total usage over the two runs), of which the agent is $5.15 by Claude Code's own report.

Links: original benchmark [scaleapi/PRBench@7b2df0a](https://github.com/scaleapi/PRBench/tree/7b2df0a28bf99ec51263c41b90dd510a740a7711); parity fork [Mike-7777777/PRBench@e7b4453](https://github.com/Mike-7777777/PRBench/tree/e7b4453a1e4bb543f8726efced94355087cea6fc); replies, agent logs and grading results of both runs on both sides, the oracle runs, every earlier attempt and the scripts (`scripts/aggregate_parity.py` for the table, `scripts/paired_parity.py` for the paired numbers), for [harborframework/parity-experiments discussion #290](https://huggingface.co/datasets/harborframework/parity-experiments/discussions/290) (`adapters/prbench/`); that dataset is over the Hugging Face limit of 1,000,000 files ([#129](https://github.com/harbor-framework/adapters/issues/129)), so until the upload goes through, the same folder is at [mike-7777777/harbor-parity-experiments](https://huggingface.co/datasets/mike-7777777/harbor-parity-experiments/tree/9fbf606835507587e205b4fc96ed034e076fa60f/adapters/prbench), pinned to a commit, with SHA256 checksums in [`manifests/prbench.sha256`](https://huggingface.co/datasets/mike-7777777/harbor-parity-experiments/tree/9fbf606835507587e205b4fc96ed034e076fa60f/manifests) at the root of that dataset; adapter PR [#83](https://github.com/harbor-framework/adapters/pull/83); dataset PR [harbor-framework/harbor-datasets#295](https://github.com/harbor-framework/harbor-datasets/pull/295).

Checks without the agent; only the representative-task jobs call a model:

- Harbor 0.27.0, this revision, all 1,100 tasks, oracle agent with `ORACLE_MODE=true`: 1,100 trials, 0 exceptions, reward 1.0 on every task (every reply matched its stored sha256).
- Harbor 0.27.0, all 1,100 tasks, `nop` agent: 1,100 trials, 0 exceptions, reward 0 and `score` 0 on every task.
- Harbor 0.27.0, `ORACLE_MODE=true` on a reply that is not the task's oracle reply: reward 0.
- Harbor 0.27.0, one task (`finance-70666a55b21ddb48de74e555`: 13 positive and 3 negative criteria), judged by `gpt-5.6-terra` (the default): the oracle outline met 13 of 13 positive and 0 of 3 negative criteria (reward 1.0); an off-topic reply met none of the 16 (reward 0); a reply written to meet the negative criteria met 3 of 3 negative and 0 of 13 positive criteria (`score` -0.33, reward 0). The three jobs cost at most $0.23 (the key's total usage over the whole check run).
- Harbor 0.27.0, three more tasks judged by `gpt-5.6-terra`, one per remaining surface the grader renders differently: `legal-566a1b0806389774b84c4704` (Legal, one turn), `finance-1b6663ec74dfe188082ee4d0` (two turns) and `finance-16fef4bc7d75cb9c36f8fd25` (two turns, reference texts prepended), the parity-subset tasks with the fewest criteria on their path. The oracle outline scored 0.50 (3 of 4 positive and 1 of 1 negative criteria met), 0.77 (7 of 10) and 0 (0 of 11): it lists what a reply should contain rather than answering, and the judge's explanations say so, which is why the full-set oracle run checks the reply's sha256 under `ORACLE_MODE` instead of calling the judge. An off-topic reply met none of the criteria on each (reward 0); every verdict parsed and no request was rejected (`oracle/harbor-0.27.0/representative-4/` in the parity upload).
- Harbor 0.26.0, adapter commit `4691d49` (the parity revision): oracle 1,100 × reward 1.0 and `nop` 1,100 × reward 0, 0 exceptions; on the same task the oracle outline met 13 of 13 positive and 0 of 3 negative criteria (reward 1.0) and an off-topic reply met none (reward 0).
- The ported functions (conversation building, rubric weights, judge prompt, verdict parsing, both scores) agree with the upstream code on all 1,100 records (`scripts/upstream_equiv.py` in the parity upload, rerun on this revision).
- The fork's `evals.py` at `e7b4453` (the original side of parity, `final_response_source: prefilled`) and this grader, given the same seeded replies for all 1,100 tasks and a deterministic local stand-in for the judge endpoint, produce identical task scores (largest per-task difference 0, `score` and `score_normalized`), and the 18,692 judge prompts are byte-identical on the two sides (`scripts/equiv_r2.sh` in the parity upload, rerun on this revision). An earlier run showed the same for unmodified upstream `evals.py` with `--reference-texts omit`, the upstream behavior.

## Notes & Caveats

**Judge model.** The paper's judge, `o4-mini`, was shut down on the OpenAI API on 2026-10-23 ([deprecations](https://developers.openai.com/api/docs/deprecations)). The default is `gpt-5.6-terra`, the judge of the parity runs and OpenAI's named replacement; the paper did not validate it against human experts. The paper validated `gpt-4.1` too (Cohen's kappa 0.605, against 0.603 for `o4-mini`); OpenAI shuts it down on 2027-01-14. It is not the default because the parity endpoint refused it (HTTP 403). Scores are directly comparable with the paper's only under `o4-mini`; `JUDGE_MODEL` selects it on an endpoint that still serves it, or any other model. `grading.json` records the model name the endpoint reports for every call; in the parity runs that was the undated `gpt-5.6-terra`.

**Reward range.** A task score can be negative when negative criteria are met (down to -0.74 on the worst task). The paper floors only the mean over tasks, so the benchmark score is the mean of `score` (the unclipped task score), floored at 0. `reward` floors each task so it stays in [0, 1], and averages slightly higher.

**Reference texts.** 17 tasks (15 Finance, 2 Legal) come with reference texts. The paper says they are prepended to the prompt. Upstream commit `7b2df0a` does not actually prepend them: `process_reference_texts` tests `isinstance(value, list)`, and the column arrives as a NumPy array (verified with `datasets` 5.2.0 and `pandas` 3.1.1). This adapter follows the paper; `--reference-texts omit` reproduces the upstream behavior, and the parity fork carries the fix (a few lines in `process_reference_texts`), so both parity sides prepend them.

**Judge failures and empty replies.** Upstream counts a criterion as not met when the judge call still fails after five attempts. Here that holds when the endpoint rejected the request itself (HTTP 400, for example a reply too long for the judge's context): the criterion is not met and `grading.json` records the error and `rejected_judge_requests`. Any other failure (timeouts, 5xx) makes the verifier exit non-zero, so Harbor records a verifier error rather than a score lowered by an outage; a rejected key or unknown model fails on the first attempt. Harbor's job mean counts a trial without a reward as 0, so rerun errored trials before reading it. One departure from upstream: a missing or empty reply scores 0 without a judge call, where upstream sends the empty reply to the judge (one parity task shows the difference; see Parity). Unparseable or empty judge output counts as not met, as upstream.

**Oracle solutions.** The dataset has no reference answers: the last turn of every record has no reply, and the `scratchpad` field holds expert notes, not an answer. By default `solution/solve.sh` writes an outline of the task's positive rubric criteria, taken verbatim from the rubric; `--oracle-dir` replaces it with replies written for each task. The oracle run uses `ORACLE_MODE=true`, after the merged `clbench` adapter: the verifier skips the judge and gives reward 1.0 only when the reply that reached it is byte for byte the reply `solution/solve.sh` writes, whose sha256 `tests/task.json` holds. It validates the image build, the file layout, the artifact hand-over and the verifier wiring on every task; it does not measure reply quality, which the representative tasks and parity cover. `ORACLE_MODE` is read from the host environment, so leave it unset for agent runs: an agent reply under `ORACLE_MODE=true` gets reward 0 and a message on stderr, never a free 1.0.

**Earlier turns.** In multi-turn tasks the earlier assistant turns are replies of the models the experts talked to (`mistral-medium-latest`, `deepseek-v3p1`, `gpt-oss-20b`), exactly as stored in the dataset. One Finance record (`d6f0165d275fdd3b4f9f87bc`) lists 5 turns and holds 4 prompts; it is kept as stored.

**Web access.** The paper's main results are without web search. Harbor agents can browse unless the environment forbids it; in the parity runs Claude Code had its WebSearch and WebFetch tools on both sides. Scores of agents with web access are not comparable with the paper's leaderboard.

## Installation / Prerequisites

- Docker, and a Harbor release with separate verifier environments (checked on Harbor 0.26.0 and 0.27.0).
- Network access when generating tasks (Hugging Face), when building images (PyPI for the `openai` package) and at verification time (the judge endpoint).
- An API key for the judge model, for agent runs.

## Troubleshooting

- `sha256 mismatch`: the downloaded file differs from the pinned revision; check `HF_ENDPOINT` or the `--source-dir` files.
- `download failed after 3 attempts`: Hugging Face is unreachable; set `HF_ENDPOINT` to a mirror or pass `--source-dir`.
- Verifier error with `Missing credentials`: an agent run needs `OPENAI_API_KEY` for the judge.
- Every criterion not met, reward 0, no verifier error, and `rejected_judge_requests` equal to the number of criteria in `grading.json`: the endpoint rejected every judge request (HTTP 400). One cause is `JUDGE_REASONING_EFFORT` set for a judge model that takes no reasoning effort; unset it.
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

Adapter: Qumeng Sun (@Mike-7777777). Issues and questions: open an issue in this repository.

## Acknowledgement

API inference compute for running parity tests is generously supported by [2077AI](https://www.2077ai.com/) (https://www.2077ai.com/).
