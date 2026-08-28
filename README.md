<!-- AUTO-GENERATED:docs-index START -->

## 📄 Files
- 📝 [Portable Tooling – Workflow-Abschluss und Übergabe](WORKFLOW-HANDOFF.md)

# DOCS
- 📚 [Docs Home](docs/index.md)

## 📁 Toolingdocs
- 🗂️ [Overview](docs/toolingdocs/toolingdocs.md)

<!-- AUTO-GENERATED:docs-index END -->
# Template Tooling

> **Repository-only documentation.** This README is not included in any portable export.
> A copied payload consists only of `tools/` and `docs/toolingdocs/`.

Template Tooling is profile-driven integration tooling for existing projects. It detects a
project, compares the observed files with a selected portable profile, and produces a bounded
plan. It is not a full-stack application template, a project generator, or an owner of product
source code.

## Portable boundary

The portable payload owns two trees:

- `tools/` contains the runtime, packaged profiles, adapters, tests, and payload metadata.
- `docs/toolingdocs/` contains the documentation that travels with the runtime.

The target project continues to own application source, business logic, data, UI, arbitrary
configuration, and unknown files. Integration may change only tooling-managed files and
explicitly allowlisted structured keys. Existing foreign keys and product content are
preserved.

Runtime state, dependency environments, reports, and build output live outside `tools/`, for
example under `.tooling-state/` and `.dist/`. They are not portable payload content.

## Start with a copied payload

From the target project root, inspect the proposed integration first:

```sh
python tools/control.py integrate --check
```

Review the detected profile, paths, conflicts, and operations. When local `.git` metadata
exists, its resolved top-level must equal the project root and the worktree must be clean; a
target without local `.git` follows the explicit non-repository safety path:

```sh
python tools/control.py integrate --full-fix
python tools/control.py tooling verify
```

`--check` is read-only. `--full-fix` stages its planned changes, verifies them, and either
publishes the complete transaction or rolls it back. The built-in adapters are conservative:
they do not scaffold missing product trees and currently do not add dependency declarations.

For a Full-Fix, planned dependency validation, quality checks, tooling tests, and declared build
commands run in the isolated staging tree before publication; a failed action leaves the live
target unchanged. Direct lifecycle commands can still execute product code and should be used
only with the relevant guide and project-owner approval.

## Documentation

- [Portable documentation](docs/toolingdocs/index.md)
- [Installation](docs/toolingdocs/guides/install.md)
- [Tests](docs/toolingdocs/guides/tests.md)
- [Builds](docs/toolingdocs/guides/builds.md)
- [Releases](docs/toolingdocs/guides/releases.md)
- [Folder replacement and migration](docs/toolingdocs/guides/folder-replacement.md)
- [Development](docs/toolingdocs/development/development.md)
- [Security boundaries](docs/toolingdocs/development/security-boundaries.md)
- [Acceptance](docs/toolingdocs/acceptance/acceptance.md)

## Current status

The integration and migration commands fail closed on unsupported plans, unsafe paths, payload
inconsistency, or an unsafe/dirty Git preflight when Git metadata exists. The payload manifest
proves internal consistency of the copied files against the included manifest; it is not an
external authenticity or release signature.

`python tools/control.py tooling export` creates a deterministic
`Template-Tooling-<version>/` directory in the current directory. Pass `--output PATH` to select
an existing output parent. The command fails closed instead of merging with or replacing an
existing package and writes only `tools/` plus `docs/toolingdocs/`, including a manifest of the
exported bytes. Build artifacts under `.dist/` are product outputs, not portable exports.

The source-only tests under `tests/source/`, this README, repository metadata, the source marker
and the workflow handoff are deliberately absent from the package. The manifest proves internal
self-consistency; obtain the export from a trusted revision because it is not a publisher
signature.

Official `tooling-v*` releases add a deterministic tar archive, `SHA256SUMS`, and GitHub
Sigstore provenance outside that payload boundary. See the
[0.4.0 release notes](RELEASE-NOTES.md) for download, verification, installation, and migration
commands.

## Hosted CI

The source repository has a portable GitHub Actions CI under `.github/workflows/`. Its support
matrix is centrally defined in `tools/resources/config/support-matrix.toml`; jobs create their
virtual environments under the runner temporary directory, never under `tools/`. The CI keeps
source-repository policy checks separate from portable payload checks, then proves copied
check/fix/verify/idempotence, export reproducibility, historical migration, system behavior, and
the bilingual documentation build on the appropriate runners.

Acceptance, nightly, and release workflows begin with Gate 0: concrete adapter, transactional
action, and rollback tests plus `python -m tools.ci_gate --require-ready`. A missing runtime
capability therefore blocks downstream acceptance instead of producing a synthetic success.
The quality workflow also audits every pytest skip site for a visible technical reason and blocks
unreviewed growth beyond the recorded baseline.

Existing workflows in a customer project remain customer-owned and are not replaced by the
tooling. Source-only tests under `tests/source/` are CI evidence for this repository, not a
customer-facing proof bundled with an export.

For repository work, follow the [contribution guide](docs/toolingdocs/development/contribution.md)
and keep changes small, tested, and ownership-aware.
