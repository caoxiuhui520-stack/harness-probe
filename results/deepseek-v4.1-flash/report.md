# Example report — real run, partial coverage

Produced by `harnessprobe run tasks --matrix configs/matrix-core.json
--provider anthropic --model workbuddy-ai/deepseek-v4.1-flash --trials 3`.

Only tasks with a complete 11-harness × 3-trial matrix are included; the
provider went down part-way through, so this is 2 of 6 tasks (66 trials, no
errors). Kept as a worked example of the report format and of what a real
result looks like — including the caveats.

# HarnessProbe report — `workbuddy-ai/deepseek-v4.1-flash`

- baseline harness: **baseline**
- tasks: 2  ·  trials per cell: 3
- overall pass rate: 98.5%
- **Harness Sensitivity Index: 1.7%**  (most sensitive to `drop-old-output`, most robust under `no-shell`)

| harness | pass rate | delta vs baseline | 95% CI | tokens | token delta | steps | flakiness |
|---|---:|---:|---|---:|---:|---:|---:|
| `baseline` (baseline) | 100.0% | +0.0% | [61%, 100%] | 4640 | +0.0% | 11.8 | 0.00 |
| `drop-old-output` | 100.0% | +0.0% | [61%, 100%] | 12418 | +167.6% | 11.7 | 0.00 |
| `loose-budget` | 100.0% | +0.0% | [61%, 100%] | 3408 | -26.5% | 8.7 | 0.00 |
| `minimal-tools` | 100.0% | +0.0% | [61%, 100%] | 3682 | -20.6% | 9.2 | 0.00 |
| `no-reasoning-hint` | 100.0% | +0.0% | [61%, 100%] | 2499 | -46.1% | 6.3 | 0.00 |
| `no-retry-nudge` | 100.0% | +0.0% | [61%, 100%] | 4938 | +6.4% | 13.8 | 0.00 |
| `no-search` | 100.0% | +0.0% | [61%, 100%] | 4239 | -8.6% | 10.5 | 0.00 |
| `terse-tools` | 100.0% | +0.0% | [61%, 100%] | 4469 | -3.7% | 11.8 | 0.00 |
| `tight-budget` | 100.0% | +0.0% | [61%, 100%] | 2914 | -37.2% | 7.2 | 0.00 |
| `verbose-system` | 100.0% | +0.0% | [61%, 100%] | 4103 | -11.6% | 10.0 | 0.00 |
| `no-shell` | 83.3% | -16.7% | [44%, 97%] | 2562 | -44.8% | 5.7 | 0.00 |

## Caveats

- at least one cell has a CI wider than 0.5; the sample is too small to separate harnesses that differ by less than ~30 points
- only 2 task(s) — a single unlucky task dominates the mean

| task | `baseline` | `drop-old-output` | `loose-budget` | `minimal-tools` | `no-reasoning-hint` | `no-retry-nudge` | `no-search` | `no-shell` | `terse-tools` | `tight-budget` | `verbose-system` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-fix-off-by-one` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 67% | 100% | 100% | 100% |
| `02-implement-cli-flag` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
