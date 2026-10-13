# Docker agent orchestrator

This project runs a deterministic developer, quality-gate, and reviewer loop against
a target Git repository. The Python orchestrator owns role permissions, task and
prompt selection, quality gates, verdict parsing, and the bounded correction loop.
`agent_runtime.py` owns runtime-specific commands, environment, configuration/state
mounts, networking, and backend validation.

## Workflow

1. A developer agent receives the task and a read-write target repository.
2. Docker quality gates run `pytest` and `git diff --check` with a read-only target.
3. A reviewer receives a read-only target repository and writes its feedback artifact.
4. `CHANGES_REQUESTED` starts a developer correction round with read-only review feedback.
5. The loop ends on `APPROVED` or requires human intervention at the configured limit.

The outer orchestrator makes every decision. Each developer or reviewer invocation
is one non-interactive backend process inside a Docker container; deterministic
quality gates and the correction loop remain in Python.

## Backend selection

`ORCHESTRATOR_AGENT_BACKEND` accepts `opencode` or `codex`. If unset, it defaults to
`opencode` for existing installations. Both backends use the configured common
Docker image, which must contain the selected CLI and target repository test tools.

The tracked `.env.example` documents common and backend-specific settings. Copy it
to `.env` for local development, or use an external environment file:

```sh
cp .env.example .env
# Edit .env with local values.
./run.sh --check
./run.sh

ORCHESTRATOR_ENV_FILE=/workspace/config/orchestrator.env ./run.sh --check
ORCHESTRATOR_ENV_FILE=/workspace/config/orchestrator.env ./run.sh
```

`ORCHESTRATOR_ENV_FILE`, when set, selects the environment file. Otherwise `run.sh`
loads `.env` in this checkout. Future bootstrap-generated configuration should live
outside the orchestrator source checkout, for example at
`<workspace>/config/orchestrator.env`. The launcher loads the selected file before
starting Python.

Preflight validates common paths and task, then only the selected backend's
configuration. It does not start containers, authenticate, or modify the target:

```sh
./run.sh --check
ORCHESTRATOR_ENV_FILE=/workspace/config/orchestrator.env ./run.sh --check
```

The task must exist at `<runtime>/input/task.md` and differ from the tracked
`input/task.example.md` template.

## OpenCode setup

OpenCode remains the default backend. Configure separate JSON files for developer
and reviewer roles:

- `ORCHESTRATOR_DEVELOPER_CONFIG`
- `ORCHESTRATOR_REVIEWER_CONFIG`

Each file is mounted read-only at `/config/opencode.json` in its respective agent
container and selected with `OPENCODE_CONFIG`. Model/provider selection remains in
those user-supplied files. OpenCode containers use host networking to preserve
setups that connect to host-local model services.

## Codex setup

Set `ORCHESTRATOR_AGENT_BACKEND=codex` and configure
`ORCHESTRATOR_CODEX_STATE_DIR` to an existing absolute host directory containing
the Codex state used for authentication and configuration. The directory must be
outside both the orchestrator checkout and target repository. The installed CLI
uses `CODEX_HOME` to locate its config and authentication state; containers set it
to `/codex-state` and bind-mount the configured host directory there. The mount is
read-write so the CLI can maintain its state. The directory is mounted only in
developer and reviewer containers when Codex is selected. Keep it private and do
not place it in the source repository or image.

`ORCHESTRATOR_DEVELOPER_MODEL` and `ORCHESTRATOR_REVIEWER_MODEL` are optional.
When set, each is passed to Codex's documented `--model` option for that role;
otherwise Codex uses its configured default. Codex runs through `codex exec` with
`--sandbox workspace-write` and `--ask-for-approval never`. Docker mount permissions
remain the outer boundary: the developer's target mount is read-write, the reviewer's
target mount is read-only, and reviewer feedback is writable at `/review` using the
CLI's `--add-dir /review` option. Codex containers use normal Docker networking.

Preflight checks that the Codex state directory exists and is outside protected
repositories. It does not authenticate or probe the image. The configured image
must include a compatible Codex CLI; this cannot currently be verified by preflight.

## Common configuration

The host needs Docker and Python 3. The selected agent CLI and the target
repository's test tools must be available in `ORCHESTRATOR_DOCKER_IMAGE`.

- `ORCHESTRATOR_TARGET_REPO`: absolute target repository directory.
- `ORCHESTRATOR_RUNTIME_DIR`: absolute runtime directory outside this source checkout.
- `ORCHESTRATOR_DOCKER_IMAGE`: image containing the selected agent CLI and test tooling.
- `ORCHESTRATOR_AGENT_BACKEND`: `opencode` or `codex`; defaults to `opencode`.
- `ORCHESTRATOR_MAX_REVIEW_ROUNDS`: optional positive integer, defaults to `4`.

The runtime directory contains `input/task.md`, reviewer feedback, and reserved
output/log directories. The launcher creates these directories for normal workflow
startup; preflight does not create them.

The reviewer writes `/review/reviewer-feedback.md`. Its first line must be exactly
`VERDICT: APPROVED` or `VERDICT: CHANGES_REQUESTED`. If the output contract is
invalid after a reviewer run, the orchestrator retries once with a recovery prompt;
a second failure requires human intervention. Reaching the configured review-round
limit without approval also requires human intervention.

## Permission boundaries and limitations

- Developer: target read-write, task read-only, and correction feedback read-only.
- Reviewer: target and task read-only, reviewer output read-write.
- Quality gates: target read-only; no agent runtime state or backend configuration.
- No container receives the Docker socket, the host home directory, or unrelated repositories.
- Agent containers have no fixed names, so separate orchestrator runs do not collide
  on singleton container names.

Codex CLI/image compatibility and actual agent execution require Docker and a
repository-owned image, which this milestone does not add. Preflight validates host
configuration only. Authentication is not tested, and no Codex inference is run by
the automated tests. OpenCode requires its two config files even though Codex does
not. The project does not yet provide bootstrap automation or role-specific images.
