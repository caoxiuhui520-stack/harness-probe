# Writing a task that discriminates

The bundled tasks are the measurement instrument. A task that every model
passes, or that none passes, produces no signal — it costs tokens and returns a
flat line. This document is about avoiding that.

## The two rules

```bash
python .verify/solve.py     # a reference solution makes the suite pass
```

and the inverse: **the untouched workspace must fail.** Both are enforced in CI.

A task failing the second rule is measuring nothing. A task failing the first is
unsolvable and measures only the grader's mood.

## Anatomy

```
tasks/07-your-task/
├── task.json          # id, prompt, grader, budget
├── workspace/         # exactly what the agent sees
│   └── ...
└── tests/             # the specification; the agent cannot see or edit this
    └── test_*.py
```

The test directory lives **outside** the sandbox. It is copied in only at
grading time. An agent that could edit its own tests would pass everything.

## Writing the prompt

State the goal and the constraints. Do not state the solution.

**Good:**
> The pagination helper in `paginate.py` is broken: the web UI never shows the
> first page of results. Run the tests to see the failure, find the cause, and
> fix it. Do not change the public signatures and do not modify the tests.

**Bad:**
> Change `page * per_page` to `(page - 1) * per_page` on line 8.

The second measures instruction-following. The first measures debugging, which
is the thing harnesses differ on.

Three things a prompt should always include:

1. **Where to look** — name the file or module. Not the line.
2. **The constraint** — "don't change signatures", "don't modify the tests".
3. **The verification path** — "run the tests". A harness that cannot run tests
   should be measurably worse, and that only shows up if the prompt points at
   them.

## Writing the tests

Tests are the specification, so they must be precise enough that a correct
solution passes and an incorrect one fails. Two failure modes to avoid:

**Under-specified.** A test that passes for the buggy starting state is not a
test. Check it: run the suite against the untouched workspace.

**Over-specified.** A test that asserts on an implementation detail — exact
variable names, internal call order, log wording — fails correct solutions and
turns the task into a guessing game. Assert on observable behaviour.

A useful check: write the reference solution *twice*, differently. If only one
of them passes, the tests are over-specified.

### Testing structure, not just behaviour

`03-refactor-duplication` is the interesting case: its output is already correct
before the agent touches it. The test asserts on *structure* — that the shared
helper exists and that the duplicated loop is gone:

```python
for src in sources:
    assert "for row in rows" not in src, "the table loop is still duplicated"
```

This is a legitimate use of source inspection, and it is the only way to test a
refactoring. Keep it narrow — assert the thing the task asked for, nothing more.

## Choosing a grader

| type | use when | example |
|---|---|---|
| `pytest` | default; the suite is the spec | most tasks |
| `exact` | output file has one correct form | generated config |
| `diff` | same, but whitespace-tolerant | generated report |
| `regex` | only a fragment of the output matters | log line format |
| `json_assert` | structured output, one field matters | API response |
| `exit_code` | correctness is a command exiting 0 | a build |

All graders run against the **sandbox**, never the transcript. That is not
negotiable: models claim success they did not achieve, and a transcript grader
rewards confident wording.

## Budget and difficulty

`max_steps` should be roughly 2× what the reference solution needs. Too tight
and you measure the budget rather than the capability; too loose and you pay for
a model that wanders.

Difficulty is a guess, and the matrix will correct it — a task labelled `hard`
that every harness passes at 100% is `easy` no matter what the file says.

## Checklist

- [ ] Untouched workspace fails the suite
- [ ] Reference solution passes the suite
- [ ] A second, differently-written solution also passes
- [ ] Tests assert on behaviour, or narrowly on the requested structure
- [ ] Prompt names the file and the constraint, not the fix
- [ ] `max_steps` ≈ 2× the reference solution's step count
- [ ] `python -m harnessprobe validate tasks/07-your-task` is clean
