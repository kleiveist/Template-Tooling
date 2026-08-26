<!-- AUTO-GENERATED:backlink START -->
[← Acceptance](acceptance.md)
<!-- AUTO-GENERATED:backlink END -->

# Definition of done

This definition is a release gate, not a statement that every future phase is complete. Each
item needs reproducible evidence from the exact candidate revision.

## Portable integration gate

| Area | Pass condition |
| --- | --- |
| Payload scope | The candidate contains only the intended `tools/` and `docs/toolingdocs/` content. The repository root README, source marker, CI, handoff files, local state, dependency environments, caches, reports, and builds are excluded. |
| Runtime closure | `tools/` contains every packaged profile, configuration resource, runtime module, fixed adapter capability, and verified runtime asset required by a copied execution; it does not depend on the source checkout. |
| Legacy isolation | Runtime code contains no old Template-Projekte repository identity/URL, external template Git source, full-product baseline, adoption/scaffold reconstruction, `tools/template_lifecycle/`, or `.template/` state model. |
| Payload consistency | The reviewed manifest covers the complete portable file set and every copied file matches it. Documentation calls this self-consistency, not publisher authentication. |
| Context and profiles | All five built-in profiles load from `tools/resources/profiles/` through `ProjectContext`; packaged configuration loads from `tools/resources/config/`; configured custom paths remain inside the project; optional missing paths do not cause scaffolding. |
| External state | `.tooling-state/` replaces legacy state and holds tooling environments, runtime records, and evidence outside `tools/`. No `.venv`, `.runtime`, `target`, cache, or local log is versioned in the payload. |
| Read-only planning | `integrate --check` and migration check change no target file or state and fail closed on ambiguity, unsafe paths, malformed structures, conflicts, or invalid payload. |
| Ownership | Unknown/product files remain byte-for-byte unchanged. Structured writes are limited to exact allowlisted keys and preserve all foreign content. Current built-ins do not add dependency declarations. |
| Transaction | Full-fix/migration require applicable preconditions, stage writes, check preimages, back up, verify, publish state last, roll back injected failures, and leave actionable evidence. |
| Action planning | Staged dependency validation is planned only for an actual dependency-key operation. Quality/test actions are planned only when required and avoid importing arbitrary target plugins as part of staging. |
| Live actions | Install/test/build adapter dispatch accepts only a profile-selected adapter and its fixed capability. Documentation states that these commands execute live product behavior outside integration rollback. |
| Idempotence | Check after fix is integrated; a second full-fix and a second migration produce no operations, action, report, state change, or target-tree change. |
| Copy matrix | Every fixture in [the copy matrix](copy-matrix.md) passes detection, fix, verify, copied tools test, protected-hash, and no-op assertions. |
| Historical upgrade | The pinned real `0.1.0` payload upgrades through the registered `0.1.0` to `0.3.0` reconciliation; tampering is rejected; config/state migrate; current payload and product hashes remain unchanged. |
| Documentation | All portable pages use relative links and the exact generated index/backlink markers; `python tools/control.py docs check` passes; command examples match the parser. |
| Case study | New German and English portable-tooling sources and diagrams build reproducibly; no inherited PDF, renamed legacy chapter, old architecture claim, or generated LaTeX artifact is stored as source. |
| Repository quality | Focused tests, the complete `tools/tests` suite, static CLI/documentation contracts, `git diff --check`, and the portable-artifact scan pass on the candidate. |

Warnings, skips, and environment-dependent cases must be explained in the acceptance record. A
skipped required matrix or historical-migration test is not a pass. Product-specific suites may
be inapplicable only when the fixture/profile explicitly does not configure that feature.

## Manual review before handoff

- Review the final diff by ownership class and confirm that no unrelated user changes were
  absorbed.
- Confirm the candidate version and registered migration graph match the release notes.
- Verify that integration reports sanitize their bounded fields. Review optional raw test reports
  for commands, absolute paths, stdout/stderr and secrets before sharing; no private target
  content may appear in fixtures or documentation.
- Exercise a forced transaction failure and recovery path for any changed write/publish logic.
- Copy the candidate into at least one clean temporary target and run its CLI through the copied
  interpreter path, not imports from the source checkout.
- Record commands, exit codes, environment constraints, and the exact commit under test.

## Open release work

The current command `python tools/control.py tooling export` intentionally reports `NOT_READY`.
A portable exporter, deterministic export artifact verification, and its publishing/CI workflow
remain open work. Product artifacts under `.dist/` do not satisfy this gate.

Until that work lands, “integration complete” means the checked-in copy/migration contract is
accepted; it does not mean a supported portable package has been exported or published. Any
manual transfer must use one complete, reviewed `tools/` plus `docs/toolingdocs/` pair from a
trusted pinned revision and then follow the [folder replacement guide](../guides/folder-replacement.md).
