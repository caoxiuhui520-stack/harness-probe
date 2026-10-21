# FAQ

## Why not just run SWE-bench?

SWE-bench measures a model against a fixed harness. That is the right tool for
"which model is better at this task family". It is the wrong tool for "how much
does the harness matter", because the harness is held constant and never varied.

HarnessProbe is complementary: it takes a small task set and varies the harness
instead of the model. The two answer different questions and a serious
evaluation should use both.

## Isn't this just prompt engineering?

Prompting is one of eight knobs. The others — tool set, tool description
verbosity, step budget, context policy, retry behaviour, verification access —
typically move the number more than prompt wording does, and they are the ones
that get silently changed when a product ships a new agent version.

The tool's contribution is making the whole set measurable and comparable rather
than anecdotal.

## Why is my HSI zero?

Every harness scored the same. Three usual causes:

1. **The task is too easy or too hard.** If every configuration passes or every
   one fails, nothing can move. Check the per-task matrix.
2. **Too few trials.** With one trial per cell, a 1-in-5 difference is invisible.
3. **The knobs do not bite.** If the model never approaches the step limit,
   `max_steps` cannot matter. If it never searches, `no-search` cannot hurt.

The last one is worth taking seriously: a knob that does nothing on your task set
is information about the task set, not a bug in the tool.

## Why are the confidence intervals so wide?

Because the samples are small, and the interval is telling the truth. A 3/5 cell
is `60% [19%, 90%]`. Reporting `60%` alone would be a lie of precision.

Use 5+ trials for anything you intend to publish, and treat differences smaller
than the interval overlap as unresolved rather than as findings.

## Can I run this in CI without an API key?

Yes — the mock provider exercises the whole pipeline deterministically:

```bash
python -m harnessprobe run tasks --matrix configs/matrix-core.json \
  --provider mock --model mock-1 --mock-script tests/mock_script.json \
  --trials 1 --out .harnessprobe/ci
```

That is what the bundled CI workflow does. It catches pipeline regressions, not
model regressions.

## How do I stop an agent from editing the tests?

It can't affect the score. A read-only copy is staged into the sandbox so the
agent can run the suite, but grading reads the pristine suite from the task
directory. Rewriting the staged copy changes nothing that is measured.

## The run is slow. What can I tune?

- `--concurrency N` — trials are independent; 4–8 is usually safe. The
  bottleneck is provider latency, not CPU.
- `max_steps` in the task file — a task with a 40-step budget spends it when the
  model wanders.
- `--trials` — halving trials halves the time and widens the intervals.

Do not reduce `max_steps` just to go faster: that changes what you measure.

## Can I compare two models?

Run the same matrix twice with different `--model`, then:

```bash
python -m harnessprobe compare .harnessprobe/runs/run-a .harnessprobe/runs/run-b
```

The comparison is only valid if the matrix, tasks and trial counts match. The
command does not enforce that, so check the manifests.

## Does a passing trial mean the code is good?

No. It means the task's tests pass. A weak test suite passes weak solutions —
this is the ceiling on any task-based evaluation, including this one.

Write tasks whose tests assert on behaviour, and prefer several small tasks over
one large one so a single lenient suite cannot dominate.

## Why raw HTTP instead of the SDK for Anthropic?

Two environment-specific failures, both documented in `docs/design.md`: system
proxy settings breaking loopback gateways, and gateways accepting
`Authorization: Bearer` while rejecting `x-api-key`.

`use_sdk=True` is available when you are talking to first-party Anthropic and
prefer the official client.
