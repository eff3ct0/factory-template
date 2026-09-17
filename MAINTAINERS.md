# MAINTAINERS - archetype self-governance

This repo (`eff3ct0/factory-template`) follows its OWN doctrine (dogfooding).
This layer is concrete and SEPARATE from the **product** (the template content
containing `<PLACEHOLDER>` values).

Release E2E and OpenAI triage automation described below is self-governance for
this repository only. The authoritative ownership contract is
[`archetype-ownership.json`](archetype-ownership.json); `init.py` consumes it
during initialization and removes only entries marked `removed`. Generic
workflows and generated project files remain.

## Ownership boundary

| Category | Lifecycle | Examples and rule |
| --- | --- | --- |
| Archetype governance | Removed after initialization | `init.py`, `factory_bootstrap.py`, this file, and source change records exist only to operate the archetype. |
| Release/template E2E / OpenAI triage | Removed after initialization | The release and template bootstrap workflows, smoke-test procedure, bootstrap/reporter/triage helpers, workflow checker, and their tests run only in `eff3ct0/factory-template`. |
| Initializer inputs | Removed after initialization | `placeholders.json` is consumed before cleanup; it must not be deleted before replacement, validation, or composition. |
| Provider and CI recipes | Removed after initialization | `providers/` and `ci/` are composition inputs. They stay available until bindings and CI are generated, then are removed as a unit. |
| Inherited generic assets | Retained | `start.py`, `AGENT.md`, generic docs, hooks, templates, GitHub forms, governance workflows, and generic checkers belong to every initialized project. |
| Generated outputs | Retained | `docs/bindings.md` and `.github/workflows/ci.yml` are project outputs and must never be added to cleanup. |

### Rules for adding files

1. Register every new tracked path in `archetype-ownership.json` before merging it.
2. Put release-only automation and archetype administration in a `removed` category; do not rely on a naming convention or directory location.
3. Keep provider/CI inputs available until composition completes. Mark generated outputs as `generated`, never `removed`.
4. Run the offline ownership-boundary check. It builds a fresh fixture from the inventory and fails if any registered `removed` path survives cleanup or any retained output disappears.
5. Do not add release E2E or OpenAI triage instructions to downstream-facing docs. Keep those procedures here, where initialization removes them.

## Hard rule
Do NOT run `init.py` on this repo: it would consume itself, fill its
placeholders, and remove its scaffolding. `init.py`, `placeholders.json`,
`providers/`, `ci/`, `factory_bootstrap.py`, and the templates are the PRODUCT,
not this repo's configuration.

## Bindings for this repo
- **Tasks:** GitHub Issues + GitHub Projects (v2) for `eff3ct0/factory-template`.
- **Secrets:** none (public template; no real secrets).
- **Loop contract:** `templates/agent-runbook.md`. **DoD:** `templates/definition-of-done.md`.

## Release bootstrap E2E configuration

The safest trigger for release validation is `release.published`; it verifies
the artifact after GitHub has published it. The workflow also exposes a manual
`workflow_dispatch` with a required `tag_name` for an intentional rerun of a
known release. It never uses the template API: the trusted harness resolves the
tag to a full commit SHA, verifies the fetched and disposable clone `HEAD`, and
tests that exact release revision.

Configure these repository settings before enabling the workflow:

- `BOOTSTRAP_E2E_OWNER` (Actions variable): a dedicated disposable-repository
  owner, preferably an organization used only for these private test repos.
- `BOOTSTRAP_E2E_APP_ID` (Actions secret): the dedicated GitHub App ID.
- `BOOTSTRAP_E2E_PRIVATE_KEY` (Actions secret): the dedicated GitHub App
  private key. The workflow mints a short-lived installation token separately
  in bootstrap and cleanup, restricts it to `BOOTSTRAP_E2E_OWNER`, and grants
  `administration: write`, `contents: write`, and `workflows: write` for the
  bootstrap token; cleanup needs only the first two. Never use a personal or
  long-lived broad-scope token.
- `OPENAI_MODEL` (Actions variable): any configured model identifier. The
  workflow validates that it is a non-empty string of at most 128 characters
  and free of control characters, then fails closed when it is absent or
  malformed.
- `OPENAI_API_KEY` (Actions secret): used only by the isolated advisory triage
  job. It is never passed to bootstrap, cleanup, or reporting.

The short-lived lifecycle credential is available only to create/push/clone/cleanup steps,
is removed from the released subprocess environment before released code
executes, and is never available to the issue reporter. The released subprocess
gets an allowlisted environment, not a copy of the runner environment. The
advisory triage job receives only a bounded sanitized JSON payload, the model
variable, and `OPENAI_API_KEY`; its clean process environment contains no GitHub
token and it has no tools or mutation authority. The reporter uses the workflow
token with `contents: read`, `actions: read`, and `issues: write`; it searches
open and closed issues using stable SHA/case/failure/check fingerprints and
follows `.github/ISSUE_TEMPLATE/bug.yml`.

The Responses API request uses strict JSON Schema output, a 300-token output
bound, a 30-second timeout, and `store: false`. The request payload and response
are bounded in the triage helper. Model output is advisory and independently
 validated before it can appear in an issue; missing, refused, malformed, or
unsafe output uses the deterministic report instead. Sanitized evidence and
triage artifacts are retained for 7 days; raw logs, raw prompts, and raw model
responses are not retained by the workflow.

The matrix is every current key in `ci/recipes.json` and is fail-fast false.
Each case uses a private repository named
`bootstrap-e2e-<run-id>-<case>`, exact-prefix cleanup derived independently from
the numeric run ID in an `if: always()` job, and a 7-day redacted evidence
artifact. The disposable repository is not deleted until released bootstrap
validation completes. Report jobs serialize by resolved release SHA, falling
back to the validated tag when preparation cannot resolve one, and do not cancel
an active run. GitHub API and every git/initializer subprocess have
a 30-second operation timeout; job timeouts are 10 minutes for prepare, triage,
and report, 30 minutes for the matrix, and 15 minutes for cleanup. API failures,
runner loss, forced cancellation, or missing credentials can leave cleanup
pending; restore the credential, inspect the owner, and run
`python3 scripts/bootstrap-e2e.py cleanup --owner "$BOOTSTRAP_E2E_OWNER" --run-id "$GITHUB_RUN_ID"`
with the exact run ID. Do not broaden the prefix or delete unrelated repos.

The template bootstrap workflow is a manual, maintainer-only check. It generates
one disposable repository per recipe from the published template, records a
run-scoped ownership/readback proof before the cold-start and `--no-clean`
validation matrix, and records redacted evidence. Its `if: always()` cleanup
job downloads those proofs and deletes only exact owner/name pairs independently
validated against GitHub. If the runner or API fails, retain the evidence
artifact and rerun exact recovery with:

`BOOTSTRAP_E2E_TOKEN=<short-lived-token> python3 scripts/bootstrap-e2e.py cleanup-template --owner <sandbox-owner> --run-id <run-id> --template eff3ct0/factory-template --evidence-dir <downloaded-evidence> --output cleanup.json`

Never replace the evidence directory with a prefix scan or delete a repository
whose proof is missing or mismatched.

The real-agent user journey is defined in [`docs/real-agent-journey.md`](docs/real-agent-journey.md) and
`.github/workflows/real-agent-journey.yml`. It is initially manual/nightly, not release-triggered. The parent
workflow owns stage ordering, run identity, credential separation, fail-closed aggregation, and bounded evidence.
The supported adapter identifier is `codex-cli`, which selects the pinned
`@openai/codex@0.148.0` runtime without selecting a secret. Configure
`REAL_AGENT_JOURNEY_RUNTIME=codex-cli` only when the dedicated agent API key,
model variable, and GitHub App credentials are available; missing credentials
must fail closed rather than use a mock.

## Cold real-agent journey contract

The `.github/workflows/real-agent-e2e.yml` workflow is a reusable/manual
invocation boundary for issue #89. Provisioning and cleanup are supplied by the
journey owner; this workflow only checks out the already-created repository and
runs the agent helper. Its inputs are `repository`, the fresh checkout's full
`expected_sha`, and an explicit scripted decision fixture. The caller supplies
`AGENT_API_KEY` and a repository-scoped `AGENT_GITHUB_TOKEN`; no lifecycle token,
secrets-manager credential, or source checkout credential is passed to Codex.

The selected runtime is Codex CLI `@openai/codex@0.148.0`, invoked with
`codex exec --json --ephemeral --ignore-user-config --sandbox workspace-write
--ask-for-approval never`. Each run gets a new `CODEX_HOME`; discovery is a
read-only cold turn and execution is a second cold turn. The agent must run
`python3 start.py` first and read `AGENT.md`, `CLAUDE.md`, and
`docs/agent-init.md`. The scripted fixture supplies every required configuration
decision explicitly, then permits exactly one feature issue, feature branch,
implementation, commit, and test flow.

The helper guards `gh` and `git` command paths and fails closed on approval
labels, merge, release, deletion, protected-branch pushes, refusal, timeout,
malformed output, or provider failure. Routine issue creation, branch pushes,
implementation, and verification do not require invented intermediate approval.
Evidence contains only bounded event classifications and redacted identifiers;
raw prompts, responses, credentials, and private paths are not retained.
The CLI contract and safety flags are based on the current official Codex
documentation: https://learn.chatgpt.com/docs/developer-commands#codex-exec and
https://learn.chatgpt.com/docs/agent-approvals-security.

The workflow pins every third-party action to a verified full commit SHA:

- `actions/checkout` v4.2.2: `11bd71901bbe5b1630ceea73d27597364c9af683`
- `actions/upload-artifact` v4.6.2: `ea165f8d65b6e75b540449e92b4886f43607fa02`
- `actions/download-artifact` v4.3.0: `d3f86a106a0bac45b974a628896c90dbdf5c8093`
- `actions/create-github-app-token` v2.2.2: `fee1f7d63c2ff003460e3d139729b119787bc349`

Run `python3 scripts/check-bootstrap-workflow.py` to reject floating, branch,
tag, or non-40-hex action references.

Before closing changes to this workflow, use the Definition of Done and retain
evidence for every matrix case, cleanup success/failure, reporting outcome,
permissions, retention, timeout, rollback, and any manual rerun.

## Ticket types (labels)
The canonical label catalog is [`.github/labels.json`](.github/labels.json); run
`python3 scripts/sync-github-labels.py` to create or update labels idempotently.

- `type:product` - template improvements or changes.
- `type:dx-feedback` - friction found while USING the archetype (see the [DX feedback issue form](.github/ISSUE_TEMPLATE/dx-feedback.yml) and [`docs/smoke-test.md`](docs/smoke-test.md)).
- `type:bug` - defect.
- `status:approved` - protected approval; agents may assign it only through the
  fail-closed delegated-approval protocol in `AGENT.md` and the bound task provider.

## Improvement cycle (one task per session)
1. Take an actionable issue (prioritize `dx-feedback` when it blocks use). Announce `Working #<n>`.
2. In Progress -> minimal change -> **verify**: `python3 init.py --self-check`, `python3 init.py --check`, a happy-path dry-run, the ownership-boundary check, and a coherence audit when structure changes.
3. Meet the DoD -> close the issue with evidence (commit/PR).
4. When a coherent batch lands -> create and push a new tag (`v1.x` / `v2`).

## Improve while using
Every project bootstrapped from the template that finds a gap opens a
`type:dx-feedback` issue here (its `FACTORY_SPEC` records provenance). Usage
feeds the backlog.

## Propagation
`MAINTAINERS.md`, `archetype-ownership.json`, and `docs/smoke-test.md` belong to
THIS repo; the inventory drives their removal from initialized projects. They
do not travel downstream.
