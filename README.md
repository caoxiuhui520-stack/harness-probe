# HarnessProbe

**Measure how much of a model's coding ability is actually the model — and how
much is the harness wrapped around it.**

[![CI](https://github.com/caoxiuhui520-stack/harness-probe/actions/workflows/ci.yml/badge.svg)](https://github.com/caoxiuhui520-stack/harness-probe/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## The problem

Every coding-agent benchmark reports a number like *"model X scores 62% on
SWE-bench"*. That number is not a property of the model. It is a property of the
**model plus the harness** — the system prompt, the tool descriptions, the step
budget, the context-management policy, whether the agent is told to verify its
work.

Change any one of those and the score moves. Usually nobody measures by how
much. So the field ends up comparing harnesses while believing it is comparing
models, and a model that is genuinely better can lose to one that is merely
better wrapped.

HarnessProbe isolates that variable. Same model, same tasks, same grader —
only the harness changes. What comes out is a **delta**, not a leaderboard.

```bash
harnessprobe run tasks --matrix configs/matrix-core.json \
  --provider anthropic --model claude-opus-5-5 --trials 5
```
```
text
|-------------------|-----------|-------------------|--------------|--------|-------|-----------|
| `baseline`        | 100.0%    | +0.0%             | [61%, 100%]  | 4640   | 11.8  | 0.00      |
| `loose-budget`    | 100.0%    | +0.0%             | [61%, 100%]  | 3408   | 8.7   | 0.00      |
| `no-reasoning-hint` | 100.0%  | +0.0%             | [61%, 100%]  | 2499   | 6.3   | 0.00      |
| `terse-tools`     | 100.0%    | +0.0%             | [61%, 100%]  | 4469   | 11.8  | 0.00      |
| `tight-budget`    | 100.0%    | +0.0%             | [61%, 100%]  | 2914   | 7.2   | 0.00      |
| `no-shell`        | 83.3%     | -16.7%            | [44%, 97%]   | 2562   | 5.7   | 0.00      |

Harness Sensitivity Index: 1.7%
```
That table is a real run — [`results/deepseek-v4.1-flash/`](results/deepseek-v4.1-flash/)
(66 trials, 2 of 6 tasks, provider went down mid-run and the rest was never
re-run). Read it for what it actually says:

**Ten of eleven harness configurations scored identically.** Every prompt and
tool-description change moved nothing. The one that did — `no-shell`, removing
the ability to run tests — cost 16.7 points and nearly halved the token spend.

That is a result worth having: on these tasks, *prompt tuning was noise and
verification access was the whole signal*. A single-pass leaderboard would have
reported "98.5%" and hidden all of it.

The numbers are also a warning about the task set: at 98.5% pass rate the two
tasks are too easy to separate anything but the largest effect. HarnessProbe
prints that ceiling rather than hiding it — see
[Honest limitations](#honest-limitations).

---

## Why this matters

### For model developers

A capability regression and a prompt regression look identical on a benchmark.
If a new checkpoint scores 3 points lower, is the model worse, or did the
harness's step budget stop fitting its longer reasoning traces? Run the matrix:
if the sensitivity *rose*, the harness is the leading suspect, and the scaffold
is where to look first.

HSI is also worth tracking over time — but as a measurement, not a diagnosis. A
model that is highly sensitive to tool descriptions *may* be one that has not
learned to use tools robustly; it may equally be a model whose reasoning traces
are longer than the budget, or a task set sitting at the edge of its ability.
Telling those apart needs a dose-response experiment, not a single delta. See
[docs/metrics.md](docs/metrics.md#what-hsi-is-and-is-not).

### For harness and product developers

Every knob here corresponds to a real decision: how much to write in a tool
description, whether to keep old tool output, whether to nudge a model that
stops calling tools. The matrix tells you which of those decisions actually
moved the number and which were cargo cult.

`no-shell` is the usual surprise. Removing the ability to run tests typically
costs more pass rate than any prompt change gains — which is an argument that
verification loops matter more than prompt engineering.

### For evaluation authors

The report refuses to hide its own uncertainty. Confidence intervals are Wilson
score intervals, `pass@k` is the unbiased estimator, and every report prints a
caveat when the sample is too small to support a conclusion. A tool that
produces confident-looking numbers from three trials is worse than no tool.

---

## How it works

```
bash
   task.json ───▶│   Sandbox    │  fresh copy per trial
   workspace/    │  (isolated)  │
                 └──────┬───────┘
                        │
   ┌────────────────────▼────────────────────┐
   │            Agent loop (runner.py)       │
   │                                         │
   │   system prompt  ◀── HarnessConfig      │
   │   tool schemas   ◀── (the only variable)│
   │   history policy ◀──                    │
   │   step budget    ◀──                    │
   │                                         │
   │   ┌─────────┐   tool calls   ┌───────┐  │
   │   │ Provider│◀──────────────▶│ Tools │  │
   │   └─────────┘                └───────┘  │
   └────────────────────┬────────────────────┘
                        │
                 ┌──────▼───────┐
                 │    Grader    │  runs against the sandbox,
                 │  (pytest…)   │  never against the transcript
                 └──────┬───────┘
                        │
                 ┌──────▼───────┐
                 │   Analysis   │  Wilson CI · pass@k · HSI
                 └──────────────┘
```
Three design decisions carry most of the weight:

**Graders run against the filesystem, not the transcript.** A model that says
"I fixed the bug" without fixing it must fail. Every grader executes in the
trial's sandbox after the loop ends.

**The agent can read the tests, but cannot change the ones it is graded on.** A
read-only copy is staged into the sandbox so the agent can actually run the
suite — otherwise a prompt that says "run the tests" sends it hunting for a
directory it cannot reach, and you measure the prompt's honesty instead of the
model. Grading always reads the pristine copy from the task directory, so
rewriting the staged one changes nothing that is measured.

**Commands run with a rebuilt environment, not the host's.** `shell=True` hands
the model a full shell, so the environment is the only thing between it and the
host's API keys. The isolated sandbox forwards an allowlist of PATH, HOME and
TMPDIR; nothing matching KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL, AUTH or
PROXY survives the filter. `Sandbox(isolated=False)` is available for trusted
local tasks that need the real toolchain, and is not the default.

**Each trial gets a fresh sandbox.** A harness that leaves broken state behind
must not contaminate the next measurement.

### What isolation is *not*

This is environment and filesystem confinement, not a container. A model that
runs a command can still read anything the sandbox user can read and can reach
the network. Treat untrusted task sets as untrusted input: run them in a
container, with no credentials in scope. See
[docs/security.md](docs/security.md).

---

## Install

```bash
git clone https://github.com/caoxiuhui520-stack/harness-probe
cd harness-probe
python -m pip install -e .
```
bash
Python 3.9+. No runtime dependencies for the core and the OpenAI-compatible
provider. `pip install -e ".[anthropic]"` adds the official SDK if you prefer
it over the built-in HTTP adapter.

## Quickstart (no API key)

The whole pipeline runs offline against a deterministic mock provider:

```bash
python -m harnessprobe run tasks \
  --matrix configs/matrix-core.json \
  --provider mock --model mock-1 \
  --mock-script tests/mock_script.json \
  --trials 1 --out .harnessprobe/demo
```
Then look at `.harnessprobe/demo/<run-id>/report.md`.

## Running against a real model

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m harnessprobe run tasks \
  --matrix configs/matrix-core.json \
  --provider anthropic --model claude-opus-5-5 \
  --trials 5 --out .harnessprobe/runs
```
bash
Any OpenAI-compatible endpoint works too:

```bash
export DEEPSEEK_API_KEY=...
python -m harnessprobe run tasks --matrix configs/matrix-core.json \
  --provider deepseek --model deepseek-chat --trials 5
```
Local models:

```bash
python -m harnessprobe run tasks --matrix configs/matrix-core.json \
  --provider ollama --model qwen2.5-coder:32b --trials 3
```
bash
### A note on gateways

If you point `--base-url` at a local gateway, HarnessProbe bypasses system proxy
settings for it. That is deliberate: on a machine with an HTTP proxy configured,
`httpx` and `urllib` will happily route a `127.0.0.1` request through the proxy,
which then answers 502. A loopback gateway must be reached directly. Both
credential styles are supported — `Authorization: Bearer` via
`ANTHROPIC_AUTH_TOKEN`, `x-api-key` via `ANTHROPIC_API_KEY`.

---

## What gets measured

### Harness knobs

Each is a decision a real harness makes. A matrix entry changes **one** of them
from the baseline, so a delta is attributable.

| knob | values | the real-world decision it models |
|---|---|---|
| `system_prompt` | any string | how opinionated the product prompt is |
| `tool_set` | subset of tools | which capabilities to expose |
| `tool_description_style` | `full` / `terse` / `minimal` | how much to document a tool |
| `max_steps` | int | how much rope before you cut the agent off |
| `temperature` | 0–2 | sampling (rejected by some current models; omitted when 0) |
| `history_policy` | `full` / `drop_old_tool_results` / `keep_last_n` | context management |
| `retry_on_error` | bool | nudge a model that stops calling tools |
| `reasoning_hint` | bool | whether to ask for a stated plan before acting |

### Metrics

| metric | meaning |
|---|---|
| **pass rate** | share of trials that pass, with a 95% Wilson interval |
| **delta** | pass rate minus the baseline harness, same model, same tasks |
| **HSI** | mean absolute delta over non-baseline harnesses — the headline |
| **pass@k** | unbiased estimator: at least one of k trials passes (ceiling) |
| **pass^k** | all k trials pass (reliability floor) |
| **flakiness** | `1 - pass^k / pass@k` — same config, same task, different answer |
| **token delta** | cost change relative to baseline |

---

## The bundled tasks

Six tasks, each verified in both directions: the starting workspace fails, and
a reference solution passes. CI enforces both.

| id | what it tests | difficulty |
|---|---|---|
| `01-fix-off-by-one` | locate and fix a classic pagination bug | easy |
| `02-implement-cli-flag` | add a CLI to a library against a test contract | medium |
| `03-refactor-duplication` | deduplicate under a *structural* constraint | medium |
| `04-bug-in-parser` | quote-aware parsing; the bug is in the ordering of operations | hard |
| `05-add-input-validation` | implement validation from a spec, collecting all errors | medium |
| `06-make-tests-pass` | two independent defects across three files | hard |

They are deliberately small. A task that takes an hour to run is a task nobody
runs five times, and five trials is the minimum for a usable interval.

**Add your own** — this is the intended use:

```bash
python -m harnessprobe init tasks/07-your-task
```
See [CONTRIBUTING.md](CONTRIBUTING.md) for the two rules a task must satisfy.

---

## Reading a report

```
bash
├── manifest.json    # provider, model, trials, task ids, version — enough to reproduce
├── results.json     # every TrialResult, full step trace
├── traces.jsonl     # one line per trial, streamed as it runs
└── report.md        # the human-facing report
```
`harnessprobe inspect <run-id> --failed-only` prints the step-by-step trace of
failed trials — usually the fastest way to see *why* a harness hurt.

---

## Honest limitations

- **Six tasks is small.** One unlucky task moves the mean. The report says so.
- **Intervals are wide at low trial counts.** At 3 trials a cell, differences
  under ~30 points are not separable. Use 5+ for anything you intend to publish.
- **One model per run.** Cross-model comparison is a separate run and a separate
  report; HarnessProbe does not average across models, because that would hide
  exactly the interaction this tool exists to measure.
- **The grader is the ceiling.** If a test suite is weak, a passing agent may
  still be wrong. Tasks are only as good as their tests.
- **Token counts come from the provider.** A provider that omits usage on
  streaming responses will under-report.

## Roadmap

- [ ] Concurrency — trials are independent and currently run sequentially
- [ ] `--resume` for interrupted runs
- [ ] Cross-model comparison report (per-model HSI, side by side)
- [ ] Bootstrap intervals alongside Wilson for small-n cells
- [ ] A larger task set, including multi-file and test-writing tasks
- [ ] Adapters for more provider wire formats

## License

MIT — see [LICENSE](LICENSE).
