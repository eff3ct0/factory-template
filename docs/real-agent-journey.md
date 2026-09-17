# Real-agent Journey Operations

The reusable workflow `.github/workflows/real-agent-journey-assertions.yml`
contains only independent readback and reporting. Provisioning and agent
invocation remain upstream responsibilities.

## Runtime Adapter Contract

`REAL_AGENT_JOURNEY_RUNTIME` must be the identifier `codex-cli`. It selects the
pinned `@openai/codex@0.148.0` executable used by
`scripts/real-agent-journey-agent.py`; it is not a secret, model name, or
credential selector. Missing, malformed, and unsupported values fail in
`prepare` before repository provisioning.

The four adapters are executable entry points, not placeholders:

- `scripts/real-agent-journey-provision.py` creates and reads back the exact
  `real-agent-journey-<run-id>` repository.
- `scripts/real-agent-journey-agent.py` runs the existing cold Codex helper.
- `scripts/real-agent-journey-assert.py` independently reads GitHub and the
  generated checkout.
- `scripts/real-agent-journey-cleanup.py` verifies and deletes only the exact
  run-scoped repository.

Every adapter emits one bounded `real-agent-journey/v1` envelope. Set
`REAL_AGENT_JOURNEY_RUNTIME=codex-cli` only after the required hosted settings
are available.

## Inputs from the journey

The provisioning/invocation jobs publish two bounded JSON artifacts and call
the reusable workflow with their names:

- `metadata-artifact` contains exactly one `real-agent-journey/v1` object.
- `cleanup-artifact` contains the run-scoped cleanup object with `owner`,
  `run_id`, `status`, `deleted`, and `failures`.

The metadata object contains the source template and immutable `source_sha`,
the generated repository and default branch, the positive feature issue
number, the implementation branch and full commit SHA, explicit `bindings`,
ordered `ci_jobs`, documented check names/statuses, and the bounded
`test_command` string. It must not contain prompts, model responses, or
credentials. The generated repository name must be
`real-agent-journey-<run-id>-<case>` so ownership is independently checkable.

The assertion job uses the least-privilege `JOURNEY_READ_TOKEN` only for
GitHub readback and a fresh generated-repository checkout. It executes the
allowlisted documented checks and the supplied test command itself; metadata
claims cannot turn a failed check green. A feature issue must match
`.github/ISSUE_TEMPLATE/task.yml`, contain observable checkbox acceptance
criteria, and have no approval label.

## Evidence

The job publishes `real-agent-journey-evidence-<run-id>` for seven days. The
artifact contains only validated repository, run, issue, branch, commit,
checkout, check, test, workflow, and cleanup identifiers. Diagnostics are
bounded and redacted. The earliest failed boundary is recorded in
`source-readback`, `initialization`, `feature-issue`, `implementation`,
`checkout`, `test`, or `cleanup`; cleanup status is retained even when an
earlier assertion fails. Missing, malformed, timed-out, or mismatched
readback is a failure, never a successful report.
## Parent real-agent user journey contract

This is the parent orchestration contract for issue #87. It proves the user
journey only when the generated repository is used by a cold real agent. The
workflow is intentionally manual/nightly at first; it is not a release gate.

## Quick path

1. Dispatch **Real-agent user journey** or wait for its nightly schedule.
2. The workflow creates a run-scoped private repository from the GitHub
   Template mechanism and passes it through the four stage interfaces below.
3. Read the bounded artifact and GitHub/checkout readback before treating the
   run as successful.

## Stage interfaces

The parent owns ordering, identity, credential boundaries, failure semantics,
and evidence aggregation. Child issues implement the stage adapters; the
parent does not choose their provider or runtime.

| Stage | Required adapter | Allowed responsibility | Success handoff |
| --- | --- | --- | --- |
| `provision` | `scripts/real-agent-journey-provision.py` | Create/read back the template repository and a fresh checkout | `provision.json` with owner, repository, template, default branch, and revision |
| `agent` | `scripts/real-agent-journey-agent.py` | Start cold, read the required contracts, receive explicit decisions, create the feature issue, implement, test, and commit | `agent.json` with bounded interaction and implementation identifiers |
| `assert` | `scripts/real-agent-journey-assert.py` | Independently read GitHub and checkout state and verify the feature result | `assert.json` with deterministic checks and outcomes |
| `cleanup` | `scripts/real-agent-journey-cleanup.py` | Delete only the current run's verified repository | `cleanup.json` with owner proof, considered target, and cleanup status |

Each adapter receives `JOURNEY_CONTRACT_VERSION=real-agent-journey/v1`,
`JOURNEY_RUN_ID`, `JOURNEY_REPOSITORY`, and `JOURNEY_STAGE`. It writes one
bounded JSON object with this minimum shape:

```json
{
  "schema_version": "real-agent-journey/v1",
  "stage": "agent",
  "run_id": "123",
  "repository": "sandbox/real-agent-journey-123",
  "status": "passed",
  "failure_code": "",
  "identifiers": {
    "issue": "12",
    "branch": "feature/12-example",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "tests": "passed"
  }
}
```

Passing adapters must provide these bounded identifiers: `provision` provides
`source_template`, `default_branch`, and `revision`; `agent` provides `issue`,
`branch`, `commit`, and `tests`; `assert` provides `checkout_head`; and
`cleanup` provides `owner` and `target`. Revision and checkout values are full
40-character commit SHAs. The parent aggregates these identifiers but does not
trust them as proof; the assertion adapter must independently verify them.

`status` is one of `passed`, `failed`, `blocked`, or `inconclusive`. A
non-passing result requires a lowercase bounded `failure_code`. Identity,
schema, size, and status mismatches fail closed. A child claim never overrides
GitHub, git, test, cleanup, or checkout readback.

## Explicit decision boundary

The scripted user must provide non-empty decisions for project, stack,
task tracker, secrets manager, code intelligence, CI, persistence language,
branching, testing, and approval gates. Existing text and defaults are
proposals, not consent. The contract validates that decisions are explicit but
does not select a task provider, secrets provider, code-intelligence provider,
agent runtime, model, or framework. The selected bindings remain those chosen
by the cold agent under `docs/agent-init.md`.

The agent and scripted user may perform routine issue/branch/commit/test work.
They MUST NOT fabricate `status:approved`, merge, publish a release, or delete
an unrelated repository. Human approval remains a real external gate.

## Credential boundaries

- Provisioning and cleanup receive separate short-lived lifecycle credentials.
- The agent receives only its explicitly required task/repository credential;
  it never receives the lifecycle credential or the runner's environment.
- Assertions receive read-only GitHub/readback access.
- Reporting receives no lifecycle, agent, or model secret.
- Checkout operations disable persisted credentials and remove temporary
  askpass material before agent code runs.

The workflow reuses the existing immutable action pins and disposable-owner
pattern from the release/template bootstrap checks. `BOOTSTRAP_E2E_OWNER` and
the dedicated GitHub App credentials are the lifecycle configuration.
`REAL_AGENT_JOURNEY_RUNTIME=codex-cli` is required for a hosted run and selects
the reviewed pinned adapter. The runtime value never selects a credential.

## Hosted Run Setup

Configure these repository settings:

- Actions variable `BOOTSTRAP_E2E_OWNER`: disposable owner for the private
  generated repository.
- Actions variable `REAL_AGENT_JOURNEY_RUNTIME`: exactly `codex-cli`.
- Actions variable `OPENAI_MODEL`: an available bounded model identifier.
- Actions secret `BOOTSTRAP_E2E_APP_ID` and
  `BOOTSTRAP_E2E_PRIVATE_KEY`: dedicated GitHub App credentials used to mint
  separate provisioning, agent, readback, and cleanup tokens.
- Actions secret `REAL_AGENT_JOURNEY_API_KEY`: OpenAI credential passed only to
  the agent adapter.

Dispatch **Real-agent user journey** with `confirm=RUN`. The workflow checks
out the trusted workflow revision, provisions the exact generated repository,
runs the cold agent on `main`, checks out its implementation branch for
independent readback, aggregates bounded evidence, and always attempts cleanup.

If a run stops before cleanup, restore the dedicated App credentials and rerun
the cleanup adapter for the exact numeric run ID and configured owner. Never
use a prefix scan or delete a repository whose owner/name readback does not
match `real-agent-journey-<run-id>`.

## Evidence and cleanup

`scripts/real-agent-journey.py collect` accepts only the four stage envelopes
and emits `real-agent-journey/v1` evidence containing run, repository, runtime,
stage status, failure code, cleanup status, and workflow URL. It does not retain
raw prompts, transcripts, credentials, private paths, or model output. Evidence
is bounded to 64 KiB and retained for 7 days by the workflow.

Cleanup is an always-on job. It may target only
`<configured-owner>/real-agent-journey-<numeric-run-id>` after independent
owner and repository readback. It never lists by a broad prefix, guesses an
owner, or deletes a candidate from another run. If cleanup is inconclusive, the
journey is failed and the artifact records the exact run-scoped recovery target.

## Scope boundary

Repository provisioning (#88) and real-agent invocation (#89) remain separate
adapters. The assertion and reporting behavior described above implements the
cross-system assertions/reporting boundary for #90. No provider-specific
shortcut or mock success path may be added to make the parent workflow green.
