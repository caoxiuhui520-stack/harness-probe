# Design notes

Why the code looks the way it does. Read this before changing the runner or
adding a knob.

## The one invariant

**The harness is the only thing that varies between runs being compared.**

Everything else — the model, the task, the grader, the sandbox contents, the
trial count — must be held fixed. Any code path that lets something else drift
is a bug, even if it is convenient.

This is why:

- Every `HarnessConfig` field is applied in `runner.py` and nowhere else. If a
  knob changes behaviour somewhere else, a reader cannot tell what a run
  actually measured by reading one file.
- The provider interface is normalised (`providers/base.py`). Adapters translate;
  they do not decide. A provider that retried differently, or truncated history
  differently, would silently become part of the harness.
- Each trial gets a fresh sandbox copy. A harness that leaves broken state
  behind must not change the next measurement.
- The grader sees only the sandbox. Transcript-based grading would let a model's
  confidence leak into the score.

## Why knobs are explicit

`HarnessConfig` has eight fields, each with a small set of legal values. It would
be easier to pass a raw dict through to the provider. It would also mean a
typo'd knob silently does nothing and reports a delta of zero — which looks like
a finding.

So `HarnessConfig.from_dict` rejects unknown keys outright, and
`HARNESS_KNOBS` is the single list both the validator and the merge logic read.

**Adding a knob is a four-place change:** the dataclass, the runner code path
that honours it, a unit test proving the runner honours it, and a matrix entry
that changes only that knob. A knob without the runner change is worse than no
knob.

## Why the grader runs after the loop, in the sandbox

Two failure modes this avoids:

1. **Claiming success.** Models routinely say "the tests now pass" without
   running them, or after a run that failed. Grading the transcript rewards this.
2. **Grading the wrong thing.** If the grader ran against the task's own
   workspace, a trial that never touched the sandbox would still be graded
   against the pristine state.

The grader signature is `(task, result, sandbox) -> TrialResult`. The sandbox is
deleted immediately after, unless `--keep-sandboxes` is set.

## Why history policies are separate functions

`apply_history_policy` is pure: same messages and config in, same messages out.
It is the easiest part of a harness to get subtly wrong — an orphaned
`tool_call_id` makes the API reject the request, and a policy that drops the
original user message makes the model forget the task.

Both are tested directly in `tests/test_runner.py` rather than through a full
run, because a full run would make the test slow and the failure ambiguous.

The policies model real production behaviour:

- `drop_old_tool_results` — the common "elide old output to save context" move.
- `keep_last_n` — a hard window, as in a naive sliding-context implementation.

`keep_last_n` always preserves `messages[0]` (the task statement) and never
starts its window on a tool result. Those two rules are what separate a working
sliding window from one that breaks on turn three.

## Why the provider speaks raw HTTP

The Anthropic adapter originally used the official SDK. Against a loopback
gateway it failed with a 502 on every request while an identical `curl` request
succeeded. The cause was `httpx` honouring system proxy settings for a
`127.0.0.1` URL — the request went to a proxy that could not reach the gateway.

Two lessons, both now encoded:

- **A loopback endpoint must be called directly.** `urllib` is used with an
  empty `ProxyHandler`.
- **Credential style is not universal.** Gateways that proxy the Messages API
  often accept `Authorization: Bearer` and reject `x-api-key`. Both are
  supported explicitly (`ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY`), rather
  than inferred.

The SDK path remains available (`use_sdk=True`) for first-party Anthropic, where
it is the better choice. The wire format is small and stable enough that owning
it is cheap.

## Why the report shows its uncertainty

The tool exists to produce deltas, and deltas at 3 trials per cell are noisy.
Presenting a bare percentage would invite exactly the over-reading the tool is
meant to prevent.

Hence: Wilson intervals on every cell, a warning when any interval is wider than
0.5, a warning when there are fewer than 3 trials, and a warning when there are
fewer than 5 tasks. The report would rather be visibly uncertain than quietly
wrong.

## Deliberate non-goals

- **Not a leaderboard.** No single composite score. Averaging across tasks hides
  which tasks moved, and the per-task matrix is always printed.
- **No cross-model averaging.** Models are run and reported separately. The
  model × harness interaction is the measurement, and averaging destroys it.
- **No significance testing.** At these sample sizes a p-value would be theatre.
  Intervals and overlap are shown instead.
- **No streaming.** Trials are independent and the analysis needs complete
  results; streaming would complicate the adapters for no measurement benefit.
  Concurrency is the roadmap item that matters here, not streaming.
