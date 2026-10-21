# Metrics reference

Every number in a HarnessProbe report, what it means, and how it is computed.

## Why the headline metric is not a pass rate

A pass rate answers "how often did this work". It does not answer "did it work
because of the model". The second question needs a comparison across harnesses,
which is what the delta and the HSI provide.

---

## Pass rate and its interval

For a cell (one task, one harness) with `n` trials and `c` passes:

```
pass_rate = c / n
```

Reported with a **95% Wilson score interval**:

```
        p̂ + z²/2n          z                        p̂(1-p̂)   z²
centre = ─────────    ±  ───────── · sqrt( ─────────── + ────── )
         1 + z²/n        1 + z²/n              n         4n²
```

Wilson rather than the normal approximation because cells are small (3–5
trials) and rates sit near 0 or 1, where the normal approximation produces
intervals that extend past `[0, 1]` and understate uncertainty.

**Reading it:** a cell at 3/5 is `60% [19%, 90%]`. That is not a measurement of
60% — it is a measurement of "somewhere between one in five and nine in ten".
Any conclusion that needs more precision than that needs more trials.

---

## Delta

```
delta(harness) = pass_rate(harness) − pass_rate(baseline)
```

Same model, same tasks, same trial count. The baseline is whatever the matrix
file names as `baseline`; every other entry changes exactly one knob from it, so
the delta is attributable to that knob.

Deltas are **absolute percentage points**, not relative. A drop from 60% to 30%
is `-30%`, not `-50%`.

---

## Harness Sensitivity Index (HSI)

```
HSI = mean( |delta(h)| ) over all non-baseline harnesses h
```

The mean absolute deviation of measured quality across harness configurations.

### Read it with max degradation, never alone

HSI is a mean, so it hides a single large regression. Ten harnesses at 0 pp
plus one at -16 pp gives an HSI of 1.7%, which reads as "not sensitive" — and
is exactly the case where the harness mattered most. Always report both:

```
HSI:              1.7%
Max degradation:  -16.7% under `no-shell`
```

### What HSI is and is not

HSI is **not a property of the model**. It is a measurement of one model
against one task distribution, one harness matrix and one experiment budget.
Change any of the three and the number changes. Two models are only comparable
when all three match, and even then the comparison is of *measurements*, not of
intrinsic model attributes.

The table below is a reading aid, not a validated scale. The thresholds are
conventions chosen for this tool, not calibrated cut points with established
error rates behind them.

| HSI | a reasonable reading |
|---|---|
| < 5% | little movement across this matrix |
| 5–15% | harness choices are visible but not dominant |
| 15–30% | harness choices are a large share of the measured score |
| > 30% | the score is mostly the harness under this matrix |

### The claim to avoid

"A high tool-description sensitivity means the model has not learned to use
tools robustly, and is pattern-matching the interface."

That is a **hypothesis**, not a finding. HSI measures a correlation between a
configuration change and a score change. It does not identify the mechanism,
and at least three mechanisms produce the same number: the model may be
brittle to interface variation, the task may be near the edge of its ability so
any perturbation tips it, or the changed knob may have altered the effective
difficulty (a tighter step budget can make a solvable task unsolvable for a
model that reasons at length, with no brittleness involved at all).

Separating those needs a dose-response experiment — vary one knob across
several values and watch the curve — not a single delta.


The report also names `hsi_worst` (the harness that helped most) and
`hsi_best` (the harness that hurt most), which is usually more actionable than
the aggregate.

---

## pass@k

The unbiased estimator from Chen et al. (2021), "Evaluating Large Language
Models Trained on Code":

```
pass@k = 1 − C(n − c, k) / C(n, k)
```

Probability that **at least one** of `k` independent trials passes. This is the
capability *ceiling* — what the model can do when it gets lucky.

`pass@1` equals the plain pass rate.

## pass^k

```
pass^k = C(c, k) / C(n, k)
```

Probability that **all k** trials pass. This is the reliability *floor* — what
you can depend on.

The pair is more informative than either alone:

| pass@k | pass^k | reading |
|---|---|---|
| high | high | reliable capability |
| high | low | capable but flaky — unusable in production |
| low | low | not capable |

## Flakiness

```
flakiness = 1 − pass^k / pass@k
```

Computed per task and averaged over tasks. `0` means every trial of that task
gave the same answer. `1` means the outcome is entirely luck.

High flakiness at `temperature > 0` is expected. High flakiness at
`temperature = 0` means the harness is non-deterministic in a way it should not
be — sandbox state leaking between trials, an ordering dependency, or a timeout
landing near the boundary.

The default `k` for the flakiness column is 1, which makes it identically zero;
pass `k` explicitly (via `analyze(..., k=N)`) to get a meaningful number.

---

## Token delta

```
token_delta_pct = (mean_tokens(harness) − mean_tokens(baseline)) / mean_tokens(baseline) × 100
```

Mean tokens per trial, input plus output. A harness that gains 5 points of pass
rate for 80% more tokens is a different proposition from one that gains 5 points
for free, and the report should not let you conflate them.

---

## What the report deliberately does not do

- **No overall leaderboard score.** Averaging across tasks hides which tasks
  moved; the per-task matrix is always printed for that reason.
- **No significance stars.** With 3–5 trials per cell, a p-value would be
  theatre. The interval is shown instead and the reader judges overlap.
- **No cross-model averaging.** Models are run and reported separately, because
  the model × harness interaction is the thing being measured.
