# Contributing to HarnessProbe

The most useful contribution is **a task that discriminates** — one where
different harnesses, or different models, produce different pass rates.

## Setup

```bash
git clone https://github.com/caoxiuhui520-stack/harness-probe
cd harness-probe
python -m pip install -e ".[dev]"
python -m pytest tests -q
```

No API key is needed for the test suite or for an offline run:

```bash
python -m harnessprobe run tasks --matrix configs/matrix-core.json \
  --provider mock --model mock-1 --mock-script tests/mock_script.json \
  --trials 1 --out .harnessprobe/ci
```

## Adding a task

```bash
python -m harnessprobe init tasks/07-my-task
```

Then fill in three things:

1. **`workspace/`** — the starting state the agent sees. Minimal, with the
   failure mode obvious once you look.
2. **`tests/`** — the specification. The agent cannot see or edit these; they
   run against the sandbox after the loop ends.
3. **`task.json`** — the prompt and the grader.

### The two rules a task must satisfy

Both are enforced in CI:

```bash
python .verify/solve.py      # every task has a reference solution that passes
```

and the inverse — the untouched workspace must fail. A task that passes before
the agent does anything measures nothing.

### Writing a prompt

State the goal and the constraints, not the solution. Name the file to change.
If the tests define an exact contract, say so and let the agent read them.

Avoid: "fix the bug on line 12" — that measures instruction-following, not
debugging.

### Choosing a grader

| type | use when |
|---|---|
| `pytest` | the default. A test suite is the specification. |
| `exact` / `diff` | the output is a file with a known correct form |
| `regex` | the output is a file and only a fragment matters |
| `json_assert` | the output is structured and one field matters |
| `exit_code` | correctness is best expressed as a command that exits 0 |

Graders run against the **sandbox**, never the transcript. A model that says
"I fixed it" without fixing it must fail.

## Adding a harness knob

A knob belongs in `HarnessConfig` only if it changes behaviour in
`harnessprobe/runner.py`. A setting the runner ignores is worse than no
setting — it produces a delta of zero that looks like a finding.

Add it in four places:

1. `HARNESS_KNOBS` and the dataclass in `spec.py`
2. the code path in `runner.py` that honours it
3. a unit test in `tests/test_runner.py`
4. an entry in `configs/matrix-core.json` that changes **only** that knob

## Adding a provider

Implement `Provider.complete` in `harnessprobe/providers/`, translate to and
from the internal message format, and register it in
`harnessprobe/providers/__init__.py`. Do not put provider-specific behaviour in
the runner — the harness must be identical across models or the comparison
means nothing.

## Reporting a result

Include the run directory (`manifest.json`, `results.json`, `report.md`) and
the exact command. Results without a manifest cannot be reproduced and will be
asked for one.

## Code style

`ruff check` and `ruff format` must pass. Keep functions small, prefer a clear
name over a comment, and comment *why* rather than *what*.
