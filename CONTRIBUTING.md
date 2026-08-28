# Contributing to Template Tooling

Thank you for helping improve Template Tooling. Contributions should make one
bounded behavior easier to verify without expanding ownership of a target
project.

## Before you start

1. Read the [Code of Conduct](CODE_OF_CONDUCT.md).
2. Search existing issues and pull requests before opening a duplicate.
3. For a substantial behavior change, open an issue first so scope and safety
   boundaries can be agreed before implementation.
4. Read the [detailed contribution guide](docs/toolingdocs/development/contribution.md)
   and [security boundaries](docs/toolingdocs/development/security-boundaries.md).

Do not disclose vulnerabilities or secrets in a public issue. Follow the
[security policy](SECURITY.md) instead.

## Make a focused change

- Keep application source, business logic, data, UI, unknown files, and foreign
  configuration project-owned.
- Restrict tooling-managed portable content to `tools/` and
  `docs/toolingdocs/`.
- Preserve unrelated structured keys, paths, and file content.
- Add or update tests for success, failure, rollback, and idempotence when a
  mutating behavior changes.
- Keep generated files, runtime state, environments, logs, reports, caches,
  builds, and secrets out of commits.

## Verify your change

Prepare dependencies in an environment outside `tools/`, then run the focused
tests for the code you changed. The main tooling suite is:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tools/tests
```

When command behavior or documentation changes, also run:

```sh
PYTHONDONTWRITEBYTECODE=1 python tools/control.py --help
PYTHONDONTWRITEBYTECODE=1 python tools/control.py docs check --docs-dir docs
```

Before opening a pull request, inspect:

```sh
git status --short
git diff --check
git diff --stat
```

## Open a pull request

Use an English imperative commit subject, keep each commit focused, and complete
the pull request template. Explain the ownership boundary, link the related
issue, and list the exact verification commands and results. A pull request is
ready for review when hosted checks pass and the documentation matches the
implemented behavior.
