# Transactional project creator

The package CLI uses the immutable payload produced by #105 as its only source
of template files. It does not make network calls or mutate the payload.

```sh
factory-template plan --target ./new-project --config answers.json --non-interactive
factory-template dry-run --target ./new-project --config answers.json --non-interactive
factory-template apply --target ./new-project --config answers.json --non-interactive
factory-template verify --target ./new-project --config answers.json --non-interactive
factory-template doctor --target ./new-project --config answers.json --non-interactive
```

Every command emits one versioned JSON envelope on stdout. Human diagnostics
and prompts are written to stderr. `plan` and `dry-run` are read-only. An
`apply` stages bytes under `.factory-template-creator/.staging`, verifies each
staged digest and mode, and commits only after the complete stage is valid.
Creator state is stored in `.factory-template-creator/state.json`; it records
the payload identity, configuration digest, and bounded output ownership
digests. It does not copy the payload manifest or become a second payload
source of truth.

The creator never overwrites an unknown file. An unchanged rerun is `noop`.
Changed configuration produces an explicit update plan, while an externally
drifted owned file is a conflict requiring recovery rather than an implicit
overwrite. `doctor` reports interrupted staging, payload mismatch, ownership
drift, unknown files, and incomplete configuration.

`--failure-after N` and `--interrupt-after N` are deterministic failure-injection
options used by the focused tests. The former must roll back creator-owned
changes; the latter intentionally leaves staging for `doctor` to report.
