# Docker agent orchestrator

This is a small procedural orchestrator for an implementation-and-review workflow.
It runs separate developer and reviewer OpenCode containers against a target Git
repository, stores the reviewer verdict in a host-mounted artifact, and enforces a
bounded correction loop.

## Workflow

1. The developer implements the task from the external runtime task file.
2. Docker-based deterministic quality gates run `pytest` and `git diff --check`.
3. The reviewer independently reviews the read-only workspace and writes a verdict.
4. `CHANGES_REQUESTED` starts a developer correction round using the review artifact.
5. The cycle ends on approval or when the configured review-round limit is reached.

The developer, quality gates, and reviewer are Docker containers. OpenCode is the
agent runtime invoked inside developer and reviewer containers. Model and provider
selection are not configured by this project: they are supplied by your separate
OpenCode configuration files.

## Setup and configuration

Requirements are Docker, Python 3, an image containing OpenCode plus the target
repository's test tooling, and two user-supplied OpenCode JSON configuration files.
The configuration files may select different models/providers for development and
review.

Create a local configuration file, then replace its placeholder values with paths and
an image available on your machine:

```sh
cp .env.example .env
# Edit .env with local values.
./run.sh
```

`run.sh` changes into this repository's directory, requires and loads the local
`.env` file, then starts `python3 main.py`. The `.env` file is ignored by Git.
Run `./run.sh --check` to validate the configuration and task without creating
runtime directories or starting containers. The task must differ from the tracked
`input/task.example.md` template. This check does not write to the target repository.

As an alternative for a shell or CI environment that already manages variables, load
the values yourself and run `python3 main.py` directly.

Required environment variables:

- `ORCHESTRATOR_TARGET_REPO`: absolute path to the repository to modify and review.
- `ORCHESTRATOR_RUNTIME_DIR`: absolute path for per-run input, review, output, and logs.
- `ORCHESTRATOR_DEVELOPER_CONFIG`: absolute path to the developer OpenCode config.
- `ORCHESTRATOR_REVIEWER_CONFIG`: absolute path to the reviewer OpenCode config.
- `ORCHESTRATOR_DOCKER_IMAGE`: image containing OpenCode and required test tooling.

`ORCHESTRATOR_MAX_REVIEW_ROUNDS` is optional and defaults to `4`.

## Files

```text
run.sh                  local launcher that loads .env
main.py                 orchestration flow
config.py               environment-backed configuration
developer.py            developer container invocation
quality_gates.py        Docker pytest and diff checks
reviewer.py             reviewer invocation and strict verdict parsing
prompts/                developer and reviewer instructions
input/task.example.md   tracked task template
.env.example            tracked configuration template
.env                    local configuration (ignored)
```

Set `ORCHESTRATOR_RUNTIME_DIR` to an absolute host path outside this source checkout.
The launcher creates this directory structure:

```text
<runtime>/
  input/task.md
  review/reviewer-feedback.md
  output/
  logs/
```

Create `<runtime>/input/task.md` from the tracked `input/task.example.md` before
running. The task is mounted read-only at `/input/task.md` in both agent containers.
The reviewer directory is mounted read-write for reviewer output and read-only for
developer correction rounds. `output/` and `logs/` reserve external locations for
runtime artifacts; the current workflow does not write files there.

## Review contract and limits

The reviewer must write `/review/reviewer-feedback.md`. Its first line must be
exactly one of:

```text
VERDICT: APPROVED
VERDICT: CHANGES_REQUESTED
```

If a zero-exit reviewer run produces an invalid artifact, the orchestrator retries
the reviewer once with a recovery prompt. A failed recovery requires human
intervention. Repeated `CHANGES_REQUESTED` results also require human intervention
when the maximum review-round limit is reached.

## Security model

The developer receives `/workspace` read-write. During correction rounds it receives
`/review` read-only. The reviewer receives `/workspace` read-only and `/review`
read-write for its feedback artifact. Input and OpenCode config mounts are read-only.
The agent containers are not given the Docker socket. Treat the target repository,
the Docker image, and user-supplied OpenCode configurations as trusted local inputs.
