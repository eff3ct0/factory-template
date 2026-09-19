# <PROJECT_NAME>

**Language-agnostic** repository template (any language/stack) defining how software development work is
executed, written **for AI agents** and readable by humans. It is intended for use as a **GitHub template
repository**: create a new repo from this structure and fill the `<PLACEHOLDER>` values.

## What it includes
- [`start.py`](start.py) - startup: `python3 start.py` detects repository state (initialize vs. work) and prints the next step. It runs first and remains permanent (it is not auto-cleaned).
- [`AGENT.md`](AGENT.md) - primary entrypoint; operating rules and references.
- [`docs/workflow.md`](docs/workflow.md) - end-to-end workflow.
- [`docs/engineering-handbook.md`](docs/engineering-handbook.md) - engineering standards.
- [`docs/bootstrap.md`](docs/bootstrap.md) - how to initialize a project from this template.
- [`docs/agent-init.md`](docs/agent-init.md) - agent-mode initialization procedure.
- [`docs/org-factory.md`](docs/org-factory.md) - organization layer (`.github` repo and `FACTORY_SPEC` pin).
- [`docs/factory-layout.md`](docs/factory-layout.md) - root allowlist and `.factory/` support-directory contract.
- [`docs/determinism.md`](docs/determinism.md) - repeat-run guarantees, limits, and focused checks.
- [`hooks/README.md`](hooks/README.md) - optional Claude Code, Pi, and OpenCode startup adapters.
- [`templates/`](templates/) - ticket, pull request, Definition of Done, ADR, spec, and agent runbook templates.
- [`.github/`](.github/) - issue and pull request templates for GitHub.
- [`init.py`](init.py) + [`placeholders.json`](placeholders.json) - Python 3 stdlib initializer that fills placeholders; `placeholders.json` is the single source of truth for project-level placeholders.
- [`.github/labels.json`](.github/labels.json) + [`scripts/`](scripts/) - canonical GitHub labels, idempotent synchronization, and PR governance validation.
- [`docs/github-governance.md`](docs/github-governance.md) - the stable required check and the separate `main` branch enforcement settings.

## Immutable creator package

The Node creator package is named `factory-template-creator` until registry
ownership and publish access are verified. The package uses Node.js 20.19 or
newer and pnpm. Its build derives the payload boundary from
`archetype-ownership.json`, preserves `placeholders.json` and
`archetype-ownership.json` as versioned compatibility contracts, and emits a
stable payload manifest with per-file metadata and an aggregate SHA-256 digest.

The package currently exposes only identity inspection:

```sh
factory-template --version
factory-template --version --json
```

Applying the payload, interactive configuration, GitHub provisioning, npm
publication, and disabling GitHub Template mode remain outside this package
unit.

## Quickstart

### Create a repository

Use GitHub's template UI, copy the contents without this template's history, or
run this explicit command after confirming the owner, name, visibility, and
template source:

```sh
gh repo create OWNER/PROJECT --template eff3ct0/factory-template --private --clone
cd PROJECT
```

This is the only repository-creation step. It is an outward action and is not
run by `start.py`, `init.py`, or any harness adapter. Use `--public` or
`--internal` only when that visibility is an intentional project decision.

### Start the first agent session

Inside the new repository, start with the manual fallback:

```sh
python3 start.py
```

In **SETUP** mode, follow [`docs/agent-init.md`](docs/agent-init.md). Review
the proposed changes first with `python3 init.py --dry-run --no-clean`, then
run the chosen initialization command with explicit confirmation and finish
with `python3 init.py --check`.
The agent should ask the owner to confirm project identity, stack and base
commands when unclear, task and secrets providers, persistence language,
branching and CI policy, and approval-gated actions. It may infer existing
choices from repository evidence such as `Cargo.toml`, `package.json`,
`pyproject.toml`, `go.mod`, and existing tracker or CI configuration, but must
show inferred values before writing them. Greenfield choices must be supplied
or confirmed by the owner.

### Optional harness startup

Hooks and plugins are opt-in. Copy only the adapter for the harness you intend
to use; none is installed silently, and every adapter only runs `start.py`.

For **OpenCode**, explicitly enable the local plugin during initialization:

```sh
python3 init.py --set OPENCODE_PLUGIN=true --confirm
```

Then start OpenCode in the repository. For the equivalent paths, follow the
[Claude Code instructions](hooks/README.md#claude-code) or the [Pi
instructions](hooks/README.md#pi). If no adapter is enabled, run
`python3 start.py` manually for every new session.

### One-command decision

The transparent multi-command path remains canonical. A new onboarding
wrapper would combine a non-atomic GitHub mutation with local initialization,
so it cannot provide a safe all-or-nothing rollback.

| Concern | Existing path | Decision |
| --- | --- | --- |
| Idempotency | `start.py` and `init.py --dry-run` are repeatable; `gh repo create` is an explicit create and should not be repeated for an existing target. | Keep separate steps so each state is visible. |
| Dry run | `init.py --dry-run --no-clean` previews local changes; `gh repo create` has no equivalent safe preview. | Never hide repository creation behind a wrapper. |
| Permissions | Creation needs GitHub repository-create permission; local initialization needs filesystem write access; adapters need local files and harness trust. | Request only the permission for the selected step. |
| Rollback | Restore local changes with VCS; deleting a GitHub repository is separate and requires human approval. | Do not claim atomic rollback. |
| Outward action | Only the user-run `gh repo create` creates a repository; hooks and `start.py` do not call the network. | No new wrapper or automatic creation. |

The existing [`factory_bootstrap.py`](factory_bootstrap.py) is a separate,
archetype-only maintainer tool for organization repositories; it is not the
project onboarding command and is removed during initialization. For the full
initializer checklist, see [`docs/bootstrap.md`](docs/bootstrap.md).

## Placeholder convention
`<UPPER_SNAKE>` = value to fill. `<!-- guide: ... -->` = instruction for the person filling it. Sections marked `OPTIONAL` are removed when they do not apply. A correctly initialized project has no unresolved required manifest `<PLACEHOLDER>` values; optional values may remain intentionally empty (see the final checklist in [`docs/bootstrap.md`](docs/bootstrap.md)).

## Persistence language
The agent may converse in any language. All persisted project work uses `<REPO_LANGUAGE>`, which defaults to English and is configured in [`placeholders.json`](placeholders.json).

## GitHub governance
Issues use the forms in `.github/ISSUE_TEMPLATE/` and blank issues are disabled. A pull request must
contain a closing reference such as `Closes #123`, have exactly one `type:*` label, and link an issue
with `status:approved`. The governance workflow validates these rules without assigning approval. The
label may be added by an agent only through the fail-closed delegated-approval protocol: current direct
instruction naming the exact issue and action, target-host maintainer/authorized-approver evidence,
`MAINTAIN` or `ADMIN` actor capability, one exact add attempt, and target-host readback. Otherwise the
human applies it directly. Labels are synchronized with:
`python3 scripts/sync-github-labels.py --repo OWNER/REPO`.

The workflow only validates pull-request metadata. GitHub branch protection separately requires the stable
`validate` check, a human review, and administrator enforcement before `main` can be updated. See
[`docs/github-governance.md`](docs/github-governance.md).

## Delegated delivery
Delegating a task authorizes its routine path without intermediate confirmation: update the tracker,
implement, verify, commit, push, open the PR, and leave evidence. It does not authorize review approval,
merge, production deployment, destructive operations, or release publication. `status:approved` remains
protected and is allowed only through the evidence-based protocol above; this change does not approve
existing work. Check the contract with `python3 scripts/check-delivery-contract.py`.
The offline delegated-approval cases can be run directly with
`python3 scripts/check-delivery-contract.py --approval-self-check`; it performs no GitHub mutation.
