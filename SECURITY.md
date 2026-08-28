# Security Policy

## Supported versions

Security fixes are applied to the current development branch and the latest
published minor release.

| Version | Supported |
| --- | --- |
| `main` | Yes |
| `0.4.x` | Yes |
| `< 0.4.0` | No |

Users of an unsupported version should reproduce the issue with the latest
release before reporting it whenever that can be done safely.

## Report a vulnerability

Use GitHub's [private vulnerability reporting form][report]. Do not open a
public issue for a suspected vulnerability and do not include credentials,
private project content, or unredacted runtime reports.

Include the following information when available:

- the affected version or commit;
- the affected command, adapter, profile, and platform;
- the expected and observed ownership or security boundary;
- minimal, redacted reproduction steps;
- impact and any known workaround or mitigation.

The maintainer will assess the report, coordinate a fix and disclosure when
appropriate, and keep reporter information private to the extent the process
allows. Public disclosure should wait until a fix or agreed mitigation is
available.

For the project's threat model, transaction boundary, and secret-handling
rules, see the [security boundaries documentation][boundaries].

[report]: https://github.com/kleiveist/Template-Tooling/security/advisories/new
[boundaries]: docs/toolingdocs/development/security-boundaries.md
