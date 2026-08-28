<!-- AUTO-GENERATED:backlink START -->
[← Back](README.md)
<!-- AUTO-GENERATED:backlink END -->
# Portable Tooling – Release-Abschluss und Übergabe

- Abschlussstand: 28. August 2026
- Tooling-Version: `0.4.0`
- Kanonischer Release-Tag: `tooling-v0.4.0`
- Letzter funktionaler `main`-Ausgangspunkt vor der Release-Finalisierung:
  `3ae5eba1aaebbc9a07b26dba2fec026ff4fa499b`
- Portables Payload-Manifest:
  `sha256:4939096be922c8b3a549a5a1b44fbfe10b17c924344cbd155f7f345158088d24`
- Manifestierte Nutzdateien: 399
- Reale Exportdateien einschließlich Manifest: 400
- Konsistente portable Dokumentationsseiten: 90

Der endgültige Release-Commit wird absichtlich nicht als selbstbezüglicher SHA in dieses Dokument
geschrieben. Verbindliche Identität sind der unveränderliche annotierte Tag `tooling-v0.4.0`, sein
GitHub-Release und die für das Archiv ausgestellte Provenienzattestierung.

## Abschlussentscheidung

Template Tooling `0.4.0` ist funktional abgeschlossen und releasefähig. Die früheren Blocker in
Dokumentationsindex, Payload-Manifest, historischem Checkout und der vollständigen
Windows-Acceptance-Matrix sind behoben. Es ist keine weitere Implementierungsphase offen.

Die richtige Version bleibt `0.4.0`: Sie ist das erste veröffentlichte Release der neuen portablen
Architektur, alle registrierten direkten Migrationen zielen bereits auf `0.4.0`, und es existierte
vor dieser Freigabe weder ein Git-Tag noch ein GitHub Release. Eine künstliche Erhöhung würde den
Migrationsvertrag vom tatsächlich geprüften Payload trennen.

## Ausgelieferte Architektur

Der portable Payload besitzt genau zwei verwaltete Bäume:

```text
Template-Tooling-0.4.0/
├── tools/
└── docs/
    └── toolingdocs/
```

- `tools/` enthält Runtime, fünf Profile, capability-basierte Adapter, Ressourcen, portable Tests
  und `PORTABLE-PAYLOAD.json`.
- `docs/toolingdocs/` enthält die vollständige mitkopierte Dokumentation und die deutschen sowie
  englischen Fallstudienquellen.
- Produktquellen, unbekannte Dateien, fremde strukturierte Schlüssel, `project-tooling.toml`,
  `.tooling-state/`, Reports, Umgebungen und Buildausgaben bleiben außerhalb des austauschbaren
  Payload-Eigentums.
- Das Root-README, diese Übergabe, Source-only-Tests, Git-Metadaten und Hosted-Workflows werden
  nicht exportiert.

## Funktionsumfang

- Fünf Profile: `web-only`, `web-cloud`, `desktop-local`, `desktop-cloud` und `full-platform`.
- Read-only Detektion und Planung über `integrate --check`.
- Transaktionaler Full-Fix mit Preimage-Prüfung, Staging, Backup, Verifikation, atomarer
  Veröffentlichung und Rollback.
- Konfigurierbare Projektpfade und strikte Eigentumsregeln für Tooling-, strukturierte und
  produktverwaltete Dateien.
- Echte Idempotenz: Check nach Fix, zweiter Full-Fix und zweite Migration sind No-ops.
- Direkte Migrationen von `0.1.0`, `0.2.0` und `0.3.0` nach `0.4.0`.
- Fail-closed Ablehnung manipulierter, gemischter, driftender oder nicht registrierter Payloads.
- Deterministischer Export mit normalisierten Modi und Zeitstempeln.
- Physische Trennung portabler Kundentests unter `tools/tests/` von Repository-Verträgen unter
  `tests/source/`.

## Veröffentlichungs- und Vertrauenskette

Der lokale Export bleibt bewusst eine Verzeichnisoperation. Der source-only Releasepublisher
unter `.github/scripts/create_portable_release.py` validiert dessen Grenze und erzeugt außerhalb
des Payloads:

- `Template-Tooling-0.4.0.tar.gz` als deterministisches kanonisches Archiv;
- `SHA256SUMS` als externen Archivhash;
- `Template-Tooling-0.4.0.intoto.jsonl` als herunterladbares Sigstore-Provenienzbundle.

Die Tag-Pipeline `.github/workflows/release.yml` prüft zuerst Gate 0, echte historische Upgrades,
Dokumentation, vollständige portable Acceptance und den Releasevertrag gegen den exakten Tag.
Danach verifiziert sie den Archivhash, registriert eine GitHub-Artefaktattestierung und erstellt
erst zuletzt das dauerhafte GitHub Release aus `RELEASE-NOTES.md`. Ein manueller Dispatch führt
dieselben Prüf- und Paketierungsschritte ohne Veröffentlichung aus.

Die drei Nachweise haben verschiedene Aufgaben:

1. `PORTABLE-PAYLOAD.json` prüft die interne Vollständigkeit der extrahierten Dateien.
2. `SHA256SUMS` prüft das heruntergeladene Archiv.
3. Die GitHub-Attestierung bindet diesen Archivdigest an Repository, Workflow und Tag.

## CI- und Acceptance-Nachweise

- [PR #10](https://github.com/kleiveist/Template-Tooling/pull/10) schloss die
  Dokumentationsindex-/Manifestkorrektur mit 25 grünen Checks ab.
- [PR #11](https://github.com/kleiveist/Template-Tooling/pull/11) korrigierte den vollständigen
  Git-History-Checkout und die begrenzten Windows-Subprozessbudgets.
- Der [vollständige manuelle Acceptance-Lauf](https://github.com/kleiveist/Template-Tooling/actions/runs/33171469030)
  bestand Gate 0, Linux, Windows, macOS, alle Profile, historische Migrationen, Rollback, Export
  und `final-ci-gate` auf demselben PR-Commit.
- Der zunächst flüchtig fehlgeschlagene macOS-Snapshot wurde auf demselben Commit ohne Codeänderung
  wiederholt und bestand; der parallele vollständige Lauf hatte den identischen Job ebenfalls
  bestanden.

Lokaler Nachweis der Release-Finalisierung mit einer Umgebung außerhalb von `tools/`:

```text
Release-, Workflow-, Dokumentations-, Export- und Manifestverträge
=> 86 passed

Vollständige Repository-/Payload-Suite mit Nested-Schutz
=> 1182 passed, 99 skipped in 61.36s

Dokumentationsnavigation
=> 90 Seiten konsistent

Zwei reale Exporte
=> jeweils 400 Dateien; Manifest sha256:4939096…088d24

Zwei daraus erstellte Archive
=> bytegleich; SHA256SUMS jeweils erfolgreich verifiziert
```

Die 99 Skips sind begründete, profil- oder plattformspezifische Fälle. Der source-only
`skip-audit` blockiert fehlende Gründe und einen unreviewten Anstieg. Ein Skip ersetzt keinen
erforderlichen Hosted-Matrixlauf.

## Branch- und Release-Governance

Der geschützte `main`-Zweig muss Pull Requests, einen aktuellen Branch und diese stabilen
GitHub-Actions-Kontexte verlangen:

- `quality` und `skip-audit`;
- `core-linux`, `python-support-matrix (3.11)` und `python-support-matrix (3.13)`;
- `system-linux`, `system-windows` und `system-macos`;
- `documentation-build`;
- `real-version-upgrade` und `windows-upgrade`;
- `final-ci-gate`.

Force-Push und Branch-Löschung bleiben gesperrt. Der Tag `tooling-v0.4.0` wird annotiert erstellt
und nach Veröffentlichung weder verschoben noch ersetzt. Repository-Regeln und Releaseobjekte
sind GitHub-Zustand und deshalb nicht Bestandteil des portablen Payload-Manifests.

## Definition of Done

- [x] Der portable Umfang ist auf `tools/` und `docs/toolingdocs/` begrenzt.
- [x] Alle fünf Profile und die vollständige Fixture-Matrix sind implementiert.
- [x] Check und Verify sind read-only; Full-Fix ist transaktional und rollbackfähig.
- [x] Produktdateien, unbekannte Dateien und fremde strukturierte Schlüssel bleiben geschützt.
- [x] Zweite Checks, Full-Fixes und Migrationen sind echte No-ops.
- [x] Alle drei Vorgängerversionen besitzen direkt geprüfte Migrationen nach `0.4.0`.
- [x] Linux-, Windows- und macOS-Hosted-Nachweise sind grün.
- [x] 90 Dokumentationsseiten, beide Fallstudienfassungen und Navigation sind konsistent.
- [x] Das Manifest umfasst den vollständigen aktuellen Payload und ist neu erzeugt.
- [x] Der Releasevertrag erzeugt ein reproduzierbares Archiv und eine externe Prüfsumme.
- [x] Das Tag-Workflow-Gate attestiert und veröffentlicht erst nach allen Voraussetzungen.
- [x] Release Notes enthalten Umfang, Migration, Supportmatrix, Verifikation und Grenzen.

## Installation und Upgrade

Nach Download und externer Verifikation:

```sh
sha256sum --check SHA256SUMS
gh attestation verify Template-Tooling-0.4.0.tar.gz \
  --repo kleiveist/Template-Tooling
tar -xzf Template-Tooling-0.4.0.tar.gz
```

Eine neue Integration beginnt immer read-only:

```sh
python tools/control.py integrate --check
python tools/control.py integrate --full-fix
python tools/control.py tooling verify
```

Beim Upgrade `tools/` und `docs/toolingdocs/` gemeinsam ersetzen, aber
`project-tooling.toml`, `.tooling-state/` und Produktdateien behalten:

```sh
python tools/control.py tooling migrate --check
python tools/control.py tooling migrate
python tools/control.py tooling verify
```

## Restgrenze nach Projektabschluss

Es gibt keinen offenen funktionalen Releaseblocker. Ein Pilot in einem echten Bestandsprojekt
bleibt vor einem organisationsweiten Rollout empfohlen, weil generische Fixtures nicht jede
kundenspezifische CI-, Berechtigungs- und Produktstruktur abbilden können. Ohne ein vom Betreiber
benanntes Zielprojekt ist dieser Pilot keine zulässige automatische externe Änderung und kein
Bestandteil des `0.4.0`-Releasevertrags.

## Nicht regressieren

- Keine Produktdatei durch Kopie, Migration oder Full-Fix vollständig ersetzen.
- Keine Abhängigkeit von einem Template-Repository oder Drei-Wege-Merge einführen.
- Keine Umgebung, Runtime, Logs, Caches oder Builds unter `tools/` erzeugen.
- Read-only Befehle dürfen keine Reports, State-, Bytecode- oder Cachedateien schreiben.
- Persistierten Managed-Tree-Drift niemals still neu baselinen.
- Source-only-Tests nicht in den Kundenpayload verschieben.
- Keine unpinned Actions, `pull_request_target`-Workflows oder Fork-Secrets einführen.
- Manifest, Payload, Archiv, Prüfsumme und Attestierung niemals aus verschiedenen Revisionen
  kombinieren.
- Bestehende Exportziele oder veröffentlichte Tags niemals automatisch ersetzen.
