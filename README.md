# Docker agent orchestrator

This project runs a deterministic developer, quality-gate, and reviewer loop against
a target Git repository. The Python orchestrator owns role permissions, task and
prompt selection, quality gates, verdict parsing, and the bounded correction loop.
`orchestrator/agent_runtime.py` owns runtime-specific commands, environment, configuration/state
mounts, networking, and backend validation. `docker/Dockerfile.agent` and
`image.sh` own the shared agent image definition and its build and smoke-check steps.

The Python implementation lives in the `orchestrator/` package. The repository
root keeps the shell launchers, Docker assets, prompts, task template, and tests;
the launchers change to the repository root before invoking the package, so they
work from any current directory.

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

The image provides Debian Bookworm userspace, bash, git, Python 3.14, and the
pytest version pinned in `toolchain.json`. This is the initial target baseline: Python/pytest projects. It does not
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
loads `.env` in this checkout. Bootstrap-generated configuration lives outside
the orchestrator source checkout, for example at
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
when changing backends. The Docker base tags and exact CLI/pytest pins are
centralized in `toolchain.json`; normal builds always use those pins.
`./agent toolchain check` is read-only and queries registries for their latest
stable releases. `./agent toolchain update [codex|opencode|pytest|all]` is the
explicit update command. Registry latest/dist-tags remain authoritative: a
higher candidate is an update, an equal one is current, and a lower one is
reported as `PIN AHEAD` (the pin is newer than the registry tag) and never
applied as a downgrade. Higher exact stable pins are written before unit and
image smoke validation. If validation fails, those candidate pins remain visible
in `toolchain.json` for review, and the command reports validation did not
complete. Prerelease pins are rejected; SemVer build metadata is allowed and
ignored when comparing precedence.
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

## Top-level agent CLI

`./agent` is the convenient interface; `bootstrap.sh`, `run.sh`, and `image.sh`
remain supported compatibility launchers. Codex is the recommended backend;
OpenCode remains supported as a local/experimental backend.

```sh
./agent --help
./agent bootstrap --workspace /absolute/path/agent-workspace \
  --target-repo /path/to/target.git --backend codex \
  --developer-model developer-model-id --reviewer-model reviewer-model-id
cd /absolute/path/agent-workspace/orchestrator
./agent auth status
./agent auth login
./agent doctor
# Edit /absolute/path/agent-workspace/projects/<target>/runtime/input/task.md
./agent check   # safe preflight; never starts an agent
./agent run     # starts developer/reviewer agents and uses inference
```

Per-role Codex model IDs are opaque and optional. Omit either option to use the
Codex CLI/account default. The Codex auth lifecycle stores state only in the
configured workspace state directory; it does not copy `~/.codex`. Treat this
persistent state as sensitive. `auth status` is non-destructive; login/logout
run inside the repository-owned image. OpenCode reports Codex auth as not
applicable. Bootstrap, `check`, `doctor`, image checks, and toolchain inspection
do not run inference. Bootstrap does not authenticate.
Doctor reports task readiness separately, so it can pass workspace
infrastructure before a task is authored. `./agent check` and normal workflow
execution still require a non-template task.

When `ORCHESTRATOR_ENV_FILE` is unset, `agent` discovers the sibling workspace
config only when the checkout has the bootstrap workspace layout. An explicit
environment file always takes precedence.

## Reproducible workspace bootstrap

`./agent bootstrap` prepares a separate workspace from a Git target source. It requires
Linux/WSL, Python 3.10 or newer, Git, and Docker with daemon access as the current
non-root user. Bootstrap does not install host software or create a host virtual
environment; the orchestrator host code uses only the Python standard library.

```sh
./bootstrap.sh --workspace /absolute/path/agent-workspace \
  --target-repo /path/to/target.git --backend codex
```

For OpenCode, pass existing role configuration files with `--developer-config`
and `--reviewer-config`. Optional `--target-ref`, `--target-name`, `--image`,
`--python-requirements`, `--codex-state`, and `--force-config` customize the
workspace. Requirements paths may be absolute or relative to the cloned target.
Bootstrap clones the current orchestrator commit by default and clones the target
only when its destination is absent. Existing repositories are verified and left
alone; generated configuration is preserved when equivalent and otherwise needs
`--force-config` to replace.

The workspace keeps source, runtime files, configuration, and Codex state apart:

```text
<workspace>/orchestrator/
<workspace>/projects/<target>/repo/
<workspace>/projects/<target>/runtime/{input,review,output,logs}/
<workspace>/config/orchestrator.env
<workspace>/state/codex/                 # Codex default only
```

Bootstrap writes an external, private `orchestrator.env`, builds the repository
image, runs its non-inference `check` and mount checks, and performs `run.sh
--check` against a temporary bootstrap-only task. `--skip-image-build` skips
only the build and still checks the selected image. Codex authentication is not
copied or tested; the configured state directory may still need authentication
before real agent use.
Without `--python-requirements`, the image contains the baseline Python 3.14 and
pytest setup only. Other dependency formats or system libraries require a
deliberate image extension.

Rerunning the same command verifies repository identity and revision, preserves
runtime and state contents, and reuses equivalent configuration. A different
configuration requires `--force-config`. The image is rebuilt on each normal
run so Dockerfile and host UID/GID changes are applied; use `--skip-image-build`
to check an existing image instead. The generated config and config directory
are restricted to the current user; the default Codex state directory and its
parent are mode `0700`. Supplied external Codex state is referenced without
changing its permissions.

Bootstrap's `run.sh --check` is a safe configuration preflight, not an agent
workflow. After bootstrap, create/edit
`<workspace>/projects/<target>/runtime/input/task.md` and run the preflight from
the cloned orchestrator:

```sh
cd /absolute/path/agent-workspace/orchestrator
ORCHESTRATOR_ENV_FILE=/absolute/path/agent-workspace/config/orchestrator.env ./run.sh --check
```

Only after reviewing the edited task and configuration, start the actual agent
workflow explicitly with `./run.sh` from that same orchestrator directory.

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

The canonical OpenCode npm package used by this project is `opencode-ai`.
OpenCode still requires its two host config files even though Codex does not.
The image, bootstrap, and local CLI lifecycle are repository-owned. Automated
tests use temporary Codex state and do not log in or run inference.
