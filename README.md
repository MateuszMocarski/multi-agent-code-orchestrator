# Docker agent orchestrator

This is a small procedural orchestrator for an implementation-and-review workflow.
It runs separate developer and reviewer OpenCode containers against a target Git
repository, stores the reviewer verdict in a host-mounted artifact, and enforces a
bounded correction loop.

## Workflow

1. The developer implements the task from `input/task.md`.
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

Copy `.env.example` to a local `.env`, replace the placeholder paths, then export it
in your shell. The orchestrator intentionally does not load `.env` itself.

```sh
set -a
. ./.env
set +a
python main.py
```

Required environment variables:

- `ORCHESTRATOR_TARGET_REPO`: absolute path to the repository to modify and review.
- `ORCHESTRATOR_DEVELOPER_CONFIG`: absolute path to the developer OpenCode config.
- `ORCHESTRATOR_REVIEWER_CONFIG`: absolute path to the reviewer OpenCode config.
- `ORCHESTRATOR_DOCKER_IMAGE`: image containing OpenCode and required test tooling.

`ORCHESTRATOR_MAX_REVIEW_ROUNDS` is optional and defaults to `4`.

## Files

```text
main.py                 orchestration flow
config.py               environment-backed configuration
developer.py            developer container invocation
quality_gates.py        Docker pytest and diff checks
reviewer.py             reviewer invocation and strict verdict parsing
prompts/                developer and reviewer instructions
input/task.example.md   tracked task template
input/task.md           local runtime task (ignored)
review/                 local reviewer output (ignored)
```

Create `input/task.md` from `input/task.example.md` before running. It is mounted at
`/input/task.md` in the agent containers.

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
