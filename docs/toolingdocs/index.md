# Portable tooling documentation

This documentation travels with the `tools/` directory. It explains the observed
implementation of Template Tooling: project-local context resolution, profile-selected
adapters, bounded structured changes, transactional integration, explicit live actions,
versioned folder replacement, verification, and acceptance evidence.

Start with [Check](integration/check.md) before applying any integration. Use the
[installation guide](guides/install.md) only when you intentionally want a live dependency
action, and read [security boundaries](development/security-boundaries.md) before changing
ownership or process policies.

The tooling is deliberately not an application scaffold. Missing product paths remain
informational, foreign product content stays project-owned, and copied payload metadata proves
self-consistency rather than publisher authenticity.

## Documentation map

<!-- AUTO-GENERATED:docs-index START -->
### Acceptance

- [Acceptance](acceptance/acceptance.md)
- [Copy matrix](acceptance/copy-matrix.md)
- [Definition of done](acceptance/definition-of-done.md)

### Architecture

- [Architecture](architecture/architecture.md)
- [Ownership and state](architecture/ownership-and-state.md)
- [Profiles, features and adapters](architecture/profiles-features-and-adapters.md)
- [Project context and paths](architecture/project-context-and-paths.md)

### Case study

- [Portable tooling case study / Fallstudie zum portablen Tooling](case-study/case-study.md)

### Development

- [Development](development/development.md)
- [Contribution guide](development/contribution.md)
- [Refactor inventory](development/refactor-inventory.md)
- [Security boundaries](development/security-boundaries.md)

### Guides

- [Guides](guides/guides.md)
- [Build product artifacts](guides/builds.md)
- [Replace a portable tooling payload](guides/folder-replacement.md)
- [Install dependencies](guides/install.md)
- [Prepare a release](guides/releases.md)
- [Run tests](guides/tests.md)

### Integration

- [Integration](integration/integration.md)
- [Check](integration/check.md)
- [Full-Fix and actions](integration/full-fix-and-actions.md)
- [Migration, verification and drift](integration/migration-verification-and-drift.md)

### Reference

- [Reference](reference/reference.md)
- [Adapter capabilities reference](reference/adapter-capabilities.md)
- [CLI reference](reference/cli.md)
- [Profiles and features reference](reference/profiles-and-features.md)
- [Project configuration reference](reference/project-configuration.md)
- [Reports reference](reference/reports.md)
- [Tooling state reference](reference/tooling-state.md)
<!-- AUTO-GENERATED:docs-index END -->

For a compact operational route, continue with the [guides overview](guides/guides.md).
