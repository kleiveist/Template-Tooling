<!-- AUTO-GENERATED:backlink START -->
[← Tooling documentation](../index.md)
<!-- AUTO-GENERATED:backlink END -->

# Development

Development work must preserve the portable boundary: the runtime may observe an arbitrary
target project, but it owns only its payload, its external state, and narrowly allowlisted
structured fields. New technology-specific behavior belongs behind an adapter selected by the
active profile.

<!-- AUTO-GENERATED:docs-index START -->
- [Contribution guide](contribution.md)
- [Security boundaries](security-boundaries.md)
- [Refactor inventory](refactor-inventory.md)
<!-- AUTO-GENERATED:docs-index END -->

Start with the [contribution guide](contribution.md) for the change workflow. Review the
[security boundaries](security-boundaries.md) before adding a write, subprocess, package-manager,
test, build, migration, or export path. The [refactor inventory](refactor-inventory.md) records
the Phase 1 classification and is historical design evidence, not a current permission to own
product files.

The root `README.md`, source-only CI, and repository handoff documents are repository assets.
Only `tools/` and `docs/toolingdocs/` may become portable payload content.
