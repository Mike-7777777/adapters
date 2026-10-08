# HealthBench Professional → Harbor Adapter

**Status: work in progress.** The generator, grader and task layout run end to end locally. Oracle solutions for all tasks, parity experiments and the final README sections are still to come; see [Open questions](#open-questions).

## Overview

[HealthBench Professional](https://arxiv.org/abs/2604.27470) (OpenAI, 2026) evaluates model replies to real conversations from clinicians using ChatGPT for Clinicians. The public set has 525 conversations (115 multi-turn) across consult, research and writing use cases, each with 1-5 physician-written rubric items (1135 in total, 213 of them negative).

- Source: Hugging Face [`openai/healthbench-professional`](https://huggingface.co/datasets/openai/healthbench-professional), MIT license.
- Original harness: [openai/simple-evals](https://github.com/openai/simple-evals) `healthbench_eval.py`; the original evaluation calls a model API directly, with no agent.
- Grading: an LLM judge decides whether each rubric item is met. Task score = sum of met item points / sum of positive points, minus a length adjustment of 0.0147 per 500 characters above 2000. The benchmark score is the mean task score, clipped to [0, 1].

## Task layout

The dataset README asks that examples not be shown in plain text online, so generated tasks contain no conversation, rubric or reference text. Each task stores only its record id and the dataset canary; data is fetched from a pinned revision (`349962fd`) and checked against its sha256 by `hbp_data.py`:

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

The agent writes its reply to `/workspace/response.txt`. The verifier fetches the conversation and rubric with its own copy of `hbp_data.py`, so editing files under `/workspace` cannot change what is graded.

`tests/grader.py` reuses the simple-evals grader prompt verbatim and its scoring (MIT, attribution in the file header). Deliberate differences: bounded retries on unparseable judge output, after which the verifier exits non-zero instead of recording a score; and the rubric and conversation are fetched rather than read from task files.

## Generate tasks

```bash
cd adapters/healthbench-professional
uv run healthbench-professional --output-dir ../../datasets/healthbench-professional
```

Options: `--limit N`, `--task-ids ID ...`, `--overwrite`, `--source PATH` (a local copy of the pinned file; its sha256 is still checked), `--oracle-dir DIR` (`<task_id>.txt` oracle replies for `solution/solve.sh`).

## Run

The verifier calls the judge through the OpenAI Responses API:

```bash
export OPENAI_API_KEY=...
# optional: OPENAI_BASE_URL, GRADER_MODEL (default gpt-5.4-2026-03-05), HF_ENDPOINT
uv run harbor run -p datasets/healthbench-professional -a <agent> -m "<model>"
```

One full grading pass over 525 replies is 1135 judge calls.

## Reward

A task score can be below 0 (negative rubric items, long replies) or above 1 (the length adjustment adds a little for replies under 2000 characters). The paper clips only the mean over tasks to [0, 1]; clipping each task first moves the physician mean from 0.429 to 0.490.

`REWARD_MODE` selects what the verifier writes. The default is set at generation time with `--reward-mode`, and `HBP_REWARD_MODE` overrides it at run time:

| Mode | Verifier output | Benchmark score |
|---|---|---|
| `unclipped` (default) | `reward.txt`: the task score | mean of `reward`, clipped to [0, 1] |
| `both` | `reward.json`: `reward` clipped to [0, 1], `score` unclipped | mean of `score`, clipped to [0, 1] |
| `clipped` | `reward.txt`: the task score clipped to [0, 1] | mean of `reward`; not comparable with the paper |

The default follows merged adapters whose original metric leaves [0, 1]: `sldbench` writes R² clipped to [-1, 1], and `mlgym-bench` writes the improvement over a baseline without bounds. `both` follows `gdb` and `widesearch`, which report several metrics in `reward.json`; Harbor averages each key separately.

## Local checks so far

- Physician reference replies, all 525, graded with this grader's prompt and scoring (judge `gpt-5.4`, reasoning low): length-adjusted mean 0.429; the paper reports 0.437 (section 5.1).
- Harbor 0.24.0, 3 tasks: every per-item verdict from `test.sh` matched a direct call to the simple-evals code.
- Pointer layout, 11 tasks with the oracle agent: the 10 tasks with a rubric-targeted oracle reply scored 1.0 before length adjustment; a scan of the generated task directories found no dataset text.
- Reward modes in Harbor 0.24.0: `unclipped` gives one `mean`; `both` gives separate means for `reward` and `score`; `HBP_REWARD_MODE=both` overrides a task generated as `unclipped`.

## Open questions

1. **Reward range.** The default is the unclipped task score (see [Reward](#reward)). If you prefer rewards in [0, 1], `both` keeps the paper-comparable score next to a clipped reward; `clipped` alone is also available but drifts from the paper.
2. **Oracle solutions.** Physician replies score 0.43 on average, so oracle replies are written against each rubric with AI assistance and re-graded. Where should they live, given that they reveal what each rubric asks for?
3. **Parity.** Proposed: Scenario 2, a claude-code sampler added to a fork of simple-evals, the same judge on both sides, a 150-task subset stratified by use case and type.

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

Adapter: Mike-7777777. Issues and questions: open an issue or ask in `#adapters-spam` on the Harbor Discord.
