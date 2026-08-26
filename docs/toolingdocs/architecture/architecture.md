# Architecture

<!-- AUTO-GENERATED:backlink START -->
[Portable tooling documentation](../index.md)
<!-- AUTO-GENERATED:backlink END -->

Template-Tooling is copied into a target project and then resolves that project from
local evidence and persisted decisions. It does not depend on a source-repository
identity or on the removed template lifecycle. The replaceable payload is `tools/`
plus the configured `docs/toolingdocs/` tree; project configuration and integration
state remain outside those roots.

The architecture deliberately separates four concerns:

1. `ProjectContext` resolves every project-local path without creating files.
2. Profiles select features; features select deterministic adapters.
3. Adapters observe, plan and verify, but submit writes through one transaction
   boundary.
4. Configuration, state and manifests record decisions and detect unreviewed drift.

Automatic integration is intentionally conservative. Missing product roots and
optional product markers are reported, not scaffolded. The only current product-file
mutation is an allowlisted `package.json` script patch when the file already exists,
has a safe shape and already declares the matching tool. Portable integration never
invokes live product commands implicitly. Profile-selected
[adapter actions](../reference/adapter-capabilities.md) are its fixed dispatch surface;
the direct commands described in the [guides](../guides/guides.md) are also explicit
live operations outside Full-Fix.

## Architecture pages

<!-- AUTO-GENERATED:docs-index START -->
- [Ownership and state](ownership-and-state.md)
- [Profiles, features and adapters](profiles-features-and-adapters.md)
- [Project context and paths](project-context-and-paths.md)
<!-- AUTO-GENERATED:docs-index END -->

For operational behavior, continue with the [integration overview](../integration/integration.md).
