# HarnessProbe report — `workbuddy-ai/deepseek-v4.1-flash`

- baseline harness: **baseline**
- tasks: 3  ·  trials per cell: 1
- overall pass rate: 98.6%
- **Harness Sensitivity Index: 1.7%**  (largest drop under `no-shell`, largest gain under `drop-old-output`)
- **Max degradation: -16.7%** under `no-shell`

HSI is a mean over harnesses and hides a single large regression; read it together with max degradation. Both are descriptive measurements under this task set, harness matrix and budget — not properties of the model.

| harness | pass rate | delta vs baseline | 95% CI | paired delta (task-level bootstrap) | tokens | token delta | steps |
|---|---:|---:|---|---|---:|---:|---:|
| `baseline` (baseline) | 100.0% | +0.0% | [68%, 100%] | +0.0% (n/a: <2 tasks) | 4097 | +0.0% | 10.2 |
| `drop-old-output` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 12418 | +203.1% | 11.7 |
| `loose-budget` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 3408 | -16.8% | 8.7 |
| `minimal-tools` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 3682 | -10.1% | 9.2 |
| `no-reasoning-hint` | 100.0% | +0.0% | [65%, 100%] | +0.0% [+0%, +0%] | 2539 | -38.0% | 6.3 |
| `no-retry-nudge` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 4938 | +20.6% | 13.8 |
| `no-search` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 4239 | +3.5% | 10.5 |
| `terse-tools` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 4469 | +9.1% | 11.8 |
| `tight-budget` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 2914 | -28.9% | 7.2 |
| `verbose-system` | 100.0% | +0.0% | [61%, 100%] | +0.0% [+0%, +0%] | 4103 | +0.2% | 10.0 |
| `no-shell` | 83.3% | -16.7% | [44%, 97%] | -16.7% [-33%, +0%] | 2562 | -37.5% | 5.7 |

Flakiness is not reported: it needs k > 1 trials per cell, and this run has one. Run with `--trials 3` or more to measure it.

## Caveats

- only 1 trial(s) per cell — confidence intervals are very wide; use --trials 3 or more before drawing conclusions
- at least one cell has a CI wider than 0.5; the sample is too small to separate harnesses that differ by less than ~30 points
- only 3 task(s) — a single unlucky task dominates the mean

| task | `baseline` | `drop-old-output` | `loose-budget` | `minimal-tools` | `no-reasoning-hint` | `no-retry-nudge` | `no-search` | `no-shell` | `terse-tools` | `tight-budget` | `verbose-system` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-fix-off-by-one` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 100% |
| `02-implement-cli-flag` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| `03-refactor-duplication` | 100% | - | - | - | 100% | - | - | - | - | - | - |

## Diagnosis (hypotheses, not conclusions)

Each entry names the knob most likely responsible and the experiment that would confirm it. A drop is evidence that a configuration is associated with a failure, not evidence of why — at least three mechanisms produce the same delta.

### `no-shell`  (-16.7%)

- **Knob:** `tool_set (shell access)`
- **Hypothesis:** the agent cannot verify its own work, so it cannot detect or recover from its own mistakes
- **To confirm:** compare no-shell against a harness that keeps the shell but hides its output; if the drop persists, the loss is verification, not execution
- **How trials ended:** submittedx 6
- **Evidence:**
  - no supporting or contradicting evidence beyond the delta itself
