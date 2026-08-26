<!-- AUTO-GENERATED:backlink START -->
[← Tooling documentation](../index.md)
<!-- AUTO-GENERATED:backlink END -->

# Guides

These guides cover the operator-facing workflow after a complete `tools/` and
`docs/toolingdocs/` payload has been copied into a project. Run commands from the target project
root and inspect a read-only command before its mutating counterpart whenever one is available.

<!-- AUTO-GENERATED:docs-index START -->
- [Install](install.md)
- [Tests](tests.md)
- [Builds](builds.md)
- [Releases](releases.md)
- [Folder replacement and migration](folder-replacement.md)
<!-- AUTO-GENERATED:docs-index END -->

The integration transaction, explicit live actions, and product release checks have different
safety boundaries. Each guide calls those differences out instead of treating every command as
transactional.
