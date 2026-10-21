# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-21

First public release. The measurement pipeline is complete end to end: define a
task, run a harness matrix, get a sensitivity report with confidence intervals.

### Added

- **Core engine**
  - `harnessprobe/runner.py` — the agent loop. Every harness knob is applied
    here and nowhere else, so a knob that does not change behaviour is a bug
    rather than a silent no-op.
  - `harnessprobe/spec.py` — `Task`, `HarnessConfig`, `TrialResult` with stable
    JSON round-tripping and a content fingerprint per task.
  - `harnessprobe/tools.py` — sandboxed `read_file`, `write_file`, `list_dir`,
    `search`, `run_command`, `submit`, with path-traversal rejection and
    per-task read-only files.

- **Harness knobs** — `system_prompt`, `tool_set`, `tool_description_style`
  (full/terse/minimal), `max_steps`, `temperature`, `history_policy`
  (full / drop_old_tool_results / keep_last_n), `retry_on_error`,
  `reasoning_hint`.

- **Providers**
  - Anthropic Messages API over raw HTTP, plus an optional SDK path. Speaks
    `Authorization: Bearer` or `x-api-key` explicitly and bypasses system proxy
    settings for loopback gateways.
  - OpenAI-compatible adapter (stdlib only) covering OpenAI, DeepSeek, Moonshot,
    Qwen, OpenRouter, vLLM, LM Studio and Ollama.
  - Deterministic mock provider so the whole pipeline runs offline in CI.

- **Graders** — `pytest`, `exact`, `regex`, `diff`, `exit_code`, `json_assert`.
  All run against the trial sandbox, never the transcript.

- **Analysis** — Wilson score intervals, unbiased `pass@k`, `pass^k`, per-task
  matrices, flakiness, token deltas, and the Harness Sensitivity Index (mean
  absolute pass-rate delta across non-baseline harnesses).

- **Six bundled tasks** covering off-by-one debugging, feature addition,
  refactoring under a structural constraint, quote-aware parsing, validation
  against a test contract, and a two-defect multi-file fix. Each is verified in
  both directions: the starting workspace fails, a reference solution passes.

- **CLI** — `run`, `inspect`, `grade`, `validate`, `init`.

- **CI** — unit tests on Linux/macOS/Windows across Python 3.9/3.11/3.12,
  task validation, reference-solution verification, and an offline end-to-end
  run.

### Known limitations

- The bundled task set is small (6 tasks). A single unlucky task moves the mean
  noticeably; the report says so in its caveats rather than hiding it.
- Results at 1–3 trials per cell have wide confidence intervals. The report
  prints them and warns when they overlap.
- `grade` re-runs analysis only; re-grading with a changed grader requires the
  original sandboxes (`--keep-sandboxes`).
- Token accounting counts what the provider reports. Providers that omit usage
  on streaming responses will under-report.

[Unreleased]: https://github.com/caoxiuhui520-stack/harness-probe/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/caoxiuhui520-stack/harness-probe/releases/tag/v0.1.0
