# Docker agent orchestrator

This project runs a deterministic developer, quality-gate, and reviewer loop against
a target Git repository. The Python orchestrator owns role permissions, task and
prompt selection, quality gates, verdict parsing, and the bounded correction loop.
`agent_runtime.py` owns runtime-specific commands, environment, configuration/state
mounts, networking, and backend validation. `docker/Dockerfile.agent` and
`image.sh` own the shared agent image definition and its build and smoke-check steps.

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
`opencode` for existing installations. A single image is built for the selected
backend and reused for developer, reviewer, and deterministic quality-gate
containers. `docker/Dockerfile.agent` installs only that selected agent CLI.

### Image baseline and target dependencies

The image provides Debian Bookworm userspace, bash, git, Python 3.14, and pytest
8.4.2. This is the initial target baseline: Python/pytest projects. It does not
include arbitrary target-project dependencies by default. To include them, set
`ORCHESTRATOR_PYTHON_REQUIREMENTS` to an absolute path to a pinned requirements
file before building. The build passes it to BuildKit as a temporary build secret,
so its contents are not copied into the build context or image layers. Agent and
quality-gate invocations do not install packages. Projects using another dependency
format or requiring system libraries need a deliberate image extension.

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
configuration. It does not inspect or build the image, start containers,
authenticate, or modify the target:

```sh
./run.sh --check
ORCHESTRATOR_ENV_FILE=/workspace/config/orchestrator.env ./run.sh --check
```

Image setup is a separate operation. With Docker installed, `image.sh build`
builds `ORCHESTRATOR_DOCKER_IMAGE` from the repository-owned Dockerfile. If that
setting is absent, the predictable default tag is
`orchestrator-agent:<backend>-py314-v1` (for example,
`orchestrator-agent:codex-py314-v1`). The selected backend comes from the same
environment file as `run.sh`. Set `ORCHESTRATOR_DOCKER_IMAGE` to the matching tag
when changing backends. The Docker base tags track Python 3.14 and Node 24 patch
security updates; agent CLI versions and pytest are pinned in the Dockerfile.
The image build passes the local host UID/GID as build arguments and creates a
matching non-root `agent` account, so host-owned writable mounts work without
changing their ownership or mode.

```sh
./image.sh build
./image.sh check "$ORCHESTRATOR_DOCKER_IMAGE"
./image.sh mount-check "$ORCHESTRATOR_DOCKER_IMAGE"
```

`check` verifies common tools and the selected CLI. Codex validation runs the
actual non-inference parser with the workflow's global `--ask-for-approval` flag
before `exec`, and checks reviewer `--add-dir` and model options. OpenCode
validation uses only `--version` and `--help`. `mount-check` uses temporary host
directories, left owned by the host user, to verify developer write access,
reviewer read-only target and writable feedback, read-only input access,
quality-gate read-only target, and (for Codex) writable state. It also checks the
container UID/GID against the host UID/GID.
These commands do not authenticate or run an agent task. They are separate from
`run.sh --check`, which remains a host configuration preflight.

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

Codex CLI 0.156.1 matches the verified host CLI version. OpenCode uses the pinned
`opencode-ai` package release 2.0.22; OpenCode is not installed on the host here,
so that package pin could not be compared against a local executable. The base
image tags intentionally receive upstream security patch updates. Authentication
is not tested, and the automated tests do not run Codex inference. OpenCode still
requires its two host config files even though Codex does not. The image build and
validation lifecycle is now repository-owned;
bootstrap automation that creates workspaces and configuration remains future work.
