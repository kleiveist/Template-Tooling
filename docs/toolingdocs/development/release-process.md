# Release process

<!-- AUTO-GENERATED:backlink START -->
[← Back](development.md)
<!-- AUTO-GENERATED:backlink END -->

Prepare a release from a reviewed revision with a clean, understood worktree. Run the
portable documentation check, focused integration/acceptance suites and the relevant
release validation. Record the tooling version, exact revision, environment and any
skipped/inapplicable matrix rows as evidence; a skip is not a pass for a required case.

Export only the `tools/` and `docs/toolingdocs/` payload pair. Regenerate and review its
manifest after payload changes, inspect the resulting destination, and retain any
distribution checksums/signatures outside the payload. Do not include project source,
state, dependency environments, reports, PDFs or build artifacts.

Release/publish actions for a product remain explicit live operations. See
[export and release boundary](../architecture/export-and-release.md),
[release gates](../acceptance/release-gates.md), and the existing
[release guide](../guides/releases.md).
