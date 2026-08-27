<!-- AUTO-GENERATED:backlink START -->
[← Tooling documentation](../index.md)
<!-- AUTO-GENERATED:backlink END -->

# Guides

These guides cover the operator-facing workflow after a complete `tools/` and
`docs/toolingdocs/` payload has been copied into a project. Run commands from the target project
root and inspect a read-only command before its mutating counterpart whenever one is available.

<!-- AUTO-GENERATED:docs-index START -->
- [Building and testing](building-and-testing.md)
- [Builds](builds.md)
- [Custom project paths](custom-project-paths.md)
- [First integration](first-integration.md)
- [Folder replacement and migration](folder-replacement.md)
- [Install](install.md)
- [Releases](releases.md)
- [Selecting a profile](selecting-a-profile.md)
- [Tests](tests.md)
- [Troubleshooting](troubleshooting.md)
- [Upgrading tooling](upgrading-tooling.md)
<!-- AUTO-GENERATED:docs-index END -->

The integration transaction, explicit live actions, and product release checks have different
safety boundaries. Each guide calls those differences out instead of treating every command as
transactional.
