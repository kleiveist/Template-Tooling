<!-- AUTO-GENERATED:backlink START -->
[← Guides](guides.md)
<!-- AUTO-GENERATED:backlink END -->

# Prepare a release

The normal command surface validates product releases but does not publish them.

## Version checks

Show the effective version and validate enabled component metadata:

```sh
python tools/control.py version
python tools/control.py version check
```

The root `VERSION` file is the product source of truth when present; otherwise the tooling falls
back to `tools/VERSION`. The check requires valid SemVer and compares the metadata of enabled
frontend and Tauri components.

To copy the product version into enabled component metadata:

```sh
python tools/control.py version sync
```

`version sync` directly changes product-owned files such as `package.json`, lockfiles,
`tauri.conf.json`, `Cargo.toml`, or `Cargo.lock` when applicable. It is not an integration
transaction. Review and commit those changes as an explicit product-version update.

## Non-publishing release gate

```sh
python tools/control.py release check
```

The gate checks applicable version metadata, project identity, desktop security/capabilities,
tag context, Git cleanliness, and signing-environment signals. A warning is evidence to review;
only a reported failure makes the command fail. The command neither uploads nor publishes an
artifact.

Run the applicable tests and builds before this gate, inspect every warning, and verify artifact
checksums and signatures using the target platform's release process.

## Portable tooling release limit

```sh
python tools/control.py tooling export
```

This command is registered but intentionally returns `NOT_READY` in the current phase. There is
no supported CLI path yet for publishing a portable tooling package. `.dist/` product artifacts
are not tooling exports.

Until export and its CI acceptance gate are implemented, a tooling update must be transferred as
one reviewed `tools/` plus `docs/toolingdocs/` pair from a trusted, pinned repository revision.
The included payload manifest verifies self-consistency only; it does not authenticate the
source. Follow the [folder replacement guide](folder-replacement.md) and never combine files from
different revisions.
