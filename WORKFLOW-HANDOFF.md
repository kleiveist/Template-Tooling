# Portable Tooling – Workflow-Abschluss und Übergabe

- Stand: 26. August 2026
- Branch: `refactor/portable-tooling`
- Implementierungsstand: `4d86b99`
  (`📦 Add deterministic portable export and CI`)
- Tooling-Version: `0.4.0`
- Portables Payload-Manifest:
  `sha256:6bd0f398d058633c1cd62367e3109964c6961be60e9c8686ccc542df23f634ea`
- Ausgangspunkt: `main` bei `9fefcdd`

## Kurzfassung

Der verbindliche Umbauworkflow von Phase 1 bis Phase 8 ist implementiert, geprüft und in
atomaren Phasencommits festgehalten. Das Repository stellt jetzt ein eigenständiges,
profilgesteuertes Tooling bereit, das zusammen mit seiner Dokumentation direkt in bestehende
Projekte kopiert oder deterministisch exportiert werden kann. Produktdateien, unbekannte Dateien,
Projektkonfiguration und persistierter Tooling-State bleiben außerhalb des austauschbaren
Payload-Eigentums.

Der Abschlussstand umfasst insbesondere:

- portablen Projektkontext und konfigurierbare Pfade;
- fünf Profile und capability-basierte Adapter;
- read-only Check, transaktionalen Full-Fix, reale Aktionen, Backup und Rollback;
- direkt registrierte Migrationen von `0.1.0`, `0.2.0` und `0.3.0` nach `0.4.0`;
- unabhängige Copy-, Austausch-, Produkt-Hash- und Idempotenztests;
- vollständig neue portable Dokumentation sowie deutsche und englische Fallstudienquellen;
- deterministischen, fail-closed Export mit selbstvalidierendem Payload-Manifest;
- physische Trennung portabler Kundentests von Source-Repository-Tests;
- reale Linux- und Windows-CI für Export, Kopie, Migration, Wiederholung und Fallstudienbuild.

Innerhalb des beschriebenen Entwicklungsworkflows ist keine Implementierungsphase mehr offen.
Offen sind nur noch externe Betriebsaktionen: den lokalen Branch ausdrücklich zu pushen, die
dadurch gestartete gehostete CI zu beobachten und gegebenenfalls einen signierten Releasekanal
außerhalb des Payload-Manifests bereitzustellen.

## Phasen- und Commitübersicht

| Phase | Commit | Abschluss |
| --- | --- | --- |
| 1 – Bestandsaufnahme | `1c6cc21` | Übernommene Module und Tests als `KEEP`, `REFACTOR`, `EXTRACT`, `REPLACE` oder `REMOVE` klassifiziert. |
| 2 – Portabler Kontext | `a3ba1ac` | Zentrale Projekt-, Tooling-, Ressourcen-, Dokumentations- und State-Pfade sowie `project-tooling.toml` eingeführt. |
| 3 – Lifecycle-Extraktion | `70c4bf3` | Sicherheits- und Transaktionsmechanismen extrahiert; Git-Template-, Drei-Wege-Merge- und `.template/`-Architektur entfernt. |
| 4 – Profile und Adapter | `1269695` | Fünf Profile, Feature-Auflösung, Adapterverträge, Registry, Detektion, Planung und Verifikation aufgebaut. |
| 5 – Integration | `b578eb8` | Read-only Check, Full-Fix, State, Driftprüfung, Migration, Transaktion, Rollback und Reports implementiert. |
| 6 – Acceptance-Härtung | `30b318c` | Unabhängige Copy-Matrix, Austauschbarkeit und Fail-Closed-Prüfungen aufgebaut. |
| 6 – Funktionsabschluss | `74400bb` | Reale profilgesteuerte Aktionen, Prozessgrenzen, Managed-Payload-Schutz und produktive Migration ergänzt. |
| 7 – Dokumentation | `ee4d4fe` | Root-README, 30 portable Seiten und vollständig neue bilinguale LaTeX-Fallstudie erstellt. |
| 8 – Export und CI | `4d86b99` | Deterministischen Export, Testsplit, 0.4.0-Migrationen und portable Linux-/Windows-CI abgeschlossen. |

Der frühere Übergabecommit `6b48439` ist durch dieses Dokument fachlich ersetzt.

## Tatsächlich abgeschlossener Funktionsstand

### Portabler Kontext und Eigentumsmodell

- `tools/core/context.py` löst Projekt-, Tooling-, Ressourcen-, Dokumentations- und State-Wurzeln
  aus dem Installationsort und der Projektkonfiguration auf.
- Die Tooling-Virtualenv liegt unter `.tooling-state/venv`, niemals unter `tools/`.
- Laufzeitberichte und Logs liegen außerhalb des austauschbaren Tooling-Payloads.
- Tooling-verwaltete, strukturiert verwaltete und projektverwaltete Dateien besitzen getrennte
  Schreibregeln.
- Produktquellen, Fremdschlüssel und unbekannte Dateien werden nicht als Tooling-Eigentum
  behandelt und nicht automatisch ersetzt.
- `tools/template_lifecycle/`, die zugehörigen Alt-Tests, `.template/`, alte Repository-IDs und
  Template-URLs sind nicht mehr vorhanden.

### Profile, Adapter und Integrationsablauf

- Alle fünf Profile werden aus `tools/resources/profiles/` geladen:
  `web-only`, `web-cloud`, `desktop-local`, `desktop-cloud` und `full-platform`.
- Adapter kapseln Detektion, Planung, Apply, Verifikation sowie freigegebene Live-Capabilities.
- `integrate --check` ist bytegenau read-only und erzeugt weder State noch Reports, Bytecode oder
  Cachedateien.
- `integrate --full-fix` prüft Preimages, arbeitet in Staging, sichert betroffene Dateien,
  verifiziert den Kandidaten, veröffentlicht State zuletzt und rollt Fehler zurück.
- Ein zweiter Check und ein zweiter Full-Fix sind nach erfolgreicher Integration echte No-ops.
- Live-Aktionen sind feste profilabhängige Capabilities; frei konfigurierte Shellfragmente werden
  nicht ausgeführt.

### Austausch und Migration

- Die direkte manuelle Kopie von `tools/` und `docs/toolingdocs/` bleibt unterstützt.
- `project-tooling.toml`, `.tooling-state/` und Produktdateien überleben den vollständigen Austausch
  beider verwalteter Verzeichnisse.
- Die Registry behält die historischen Pfade `0.1.0 → 0.2.0`, `0.1.0 → 0.3.0` und
  `0.2.0 → 0.3.0`.
- Für das aktuelle Release existieren direkte, nicht implizit verkettete Reconciliations:
  `0.1.0 → 0.4.0`, `0.2.0 → 0.4.0` und `0.3.0 → 0.4.0`.
- Echte, per Commit sowie `tools`- und `docs/toolingdocs`-Tree-ID gepinnte historische Payloads
  aller drei Vorgängerversionen wurden erfolgreich migriert.
- Manipulierte oder gemischte Payloads, nicht registrierte Versionssprünge und persistierter
  Managed-Tree-Drift schlagen fehl, statt still neu baselined zu werden.

### Dokumentation und Fallstudie

- `docs/toolingdocs/` enthält 30 verlinkte Seiten zu Architektur, Integration, Guides, Referenz,
  Entwicklung, Acceptance und Fallstudie.
- Das Root-`README.md` ist ausdrücklich repository-only und nicht Bestandteil des Exports.
- Deutsche Quellen liegen unter `docs/toolingdocs/case-study/source/de/`, englische unter
  `docs/toolingdocs/case-study/source/en/`.
- Gliederung, Text, Diagramme und Buildlogik wurden neu für das portable Tooling erstellt.
- Der Build verwendet externe temporäre Verzeichnisse, zwei `pdflatex`-Durchläufe,
  `SOURCE_DATE_EPOCH`, deaktivierte variable PDF-Metadaten und atomare Ausgabe mit Rollback.
- Keine PDFs, Aux-, Log- oder sonstigen LaTeX-Buildartefakte liegen im Quellbestand.

### Deterministischer Export

Der produktive Befehl lautet:

```sh
python tools/control.py tooling export
python tools/control.py tooling export --output /existierender/ausgabeordner
```

Er erzeugt `Template-Tooling-0.4.0/` mit ausschließlich:

```text
Template-Tooling-0.4.0/
├── tools/
└── docs/
    └── toolingdocs/
```

Der Exporter:

- inventarisiert und hasht den vollständigen portablen Payload;
- kopiert nur manifestierte reguläre Dateien in ein isoliertes Staging-Verzeichnis;
- normalisiert logische Modi und Zeitstempel;
- validiert Manifest und Quelle erneut vor der Veröffentlichung;
- veröffentlicht atomar ohne ein vorhandenes Ziel zu ersetzen;
- prüft Staging-Identität und den veröffentlichten Payload erneut;
- lehnt Symlinks, Sonderdateien, Case-Folding- und Unicode-Normalisierungskollisionen,
  Windows-ungültige Namen, Secrets, Runtime-, Cache-, Coverage-, Archiv- und Buildreste ab;
- erlaubt nur die exakte Rust-Analyzer-WASM und das kanonische Tauri-`build/`-Quellverzeichnis als
  enge Ausnahmen;
- exportiert weder Root-README noch diese Übergabe, `.git/`, `.github/`, State oder Source-Tests.

Das Exportmanifest enthält 239 Nutzdateieinträge. Mit dem Manifest selbst umfasst der reale
Export 240 Dateien. Sein Digest ist:

```text
sha256:6bd0f398d058633c1cd62367e3109964c6961be60e9c8686ccc542df23f634ea
```

Das Manifest ist eine Selbstkonsistenzprüfung, keine Signatur oder Herausgeberauthentisierung.

### Portable Tests und Source-Testsplit

- Alle 67 portablen Testmodule liegen unter `tools/tests/` und sind im Manifest enthalten.
- Repository-only-Tests liegen physisch unter `tests/source/` und werden nicht exportiert.
- Der Source-Marker erscheint unter `tools/tests/` nur als Exporter-Negativfixture, nicht als
  Skipmechanismus für Kundentests.
- Die frühere Master-Repository-CI-Suite wurde entfernt und durch vier Phase-8-Vertragstests für
  den realen portablen Workflow ersetzt.
- Der unabhängige Kundensmoke verwendet ausschließlich exportierte Dateien und führt
  Check → Full-Fix → Check → `test --suite all` → zweiten Full-Fix aus.
- Vorher/nachher-Hashes belegen, dass Produktdateien unverändert bleiben.

### Continuous Integration

`.github/workflows/portable-tooling.yml` besitzt zwei nicht publizierende Jobs:

- Linux auf `ubuntu-24.04` mit vollständiger Git-Historie, Source-Tests, echter Historienmigration,
  Copy-Matrix, Austauschtest, Export, unabhängigem Kundensmoke und zweimaligem Build beider
  Fallstudien-PDFs mit Bytevergleich;
- Windows auf `windows-2025` mit portabler Suite, Export, exportierter CLI, Dokumentationscheck
  sowie exportierten Core-/Integrationstests.

Beide Jobs installieren die Tooling-Umgebung außerhalb von `tools/`, stellen deren Executables
über `GITHUB_PATH` bereit, verhindern Bytecode/User-Site-Einflüsse und verlangen am Ende einen
sauberen Checkout. Die verwendeten Actions sind auf vollständige Commit-SHAs gepinnt:

- `actions/checkout` `v6.0.2` → `de0fac2e4500dabe0009e67214ff5f5447ce83dd`;
- `actions/setup-python` `v6.3.0` → `ece7cb06caefa5fff74198d8649806c4678c61a1`.

Der Workflow besitzt nur `contents: read`, enthält keine Secrets, keinen Deploy- oder
Publish-Schritt und überschreibt keine bestehenden Exporte.

## Prüfbelege des Abschlussstands

Alle Python-Aufrufe verwendeten die externe Umgebung
`/tmp/template-tooling-tests.eOArwI/bin/python`, nie `tools/.venv`.

```text
Vollständige Repository-/Payload-Suite mit Nested-Schutz
=> 1075 passed, 90 skipped in 62.98s

Unabhängige Copy-Matrix plus Tooling-Austausch, ohne Nested-Schutz
=> 12 passed in 405.52s

Echte historische Payloads 0.1.0, 0.2.0 und 0.3.0 → 0.4.0
=> 3 passed in 30.49s

Betroffene Migrations-, Workflow-, Doku- und CI-Suite
=> 35 passed in 10.36s

Fokussierte Export-, Manifest-, Migration- und CI-Vertragssuite
=> 96 passed in 3.23s

Dokumentationsnavigation
=> 30 Seiten konsistent

CI-Vertrag nach finaler GITHUB_PATH-Härtung
=> 4 passed

Ruff für alle geänderten/neuen Python-Dateien
=> All checks passed; 24 files already formatted

GitHub-Workflow-Syntax
=> actionlint 1.7.12 sauber

Staged-Diff, JSON/TOML, Artefakt- und Legacy-Scan
=> sauber
```

Die 90 Skips der vollständigen lokalen Suite sind begründet:

- Copy-Matrix und Austauschtest wurden im Nested-Lauf vor Rekursion geschützt und direkt danach
  separat vollständig bestanden;
- Tauri-Suiten sind für das aktive Source-Profil ohne Tauri deaktiviert;
- zwei Prozessprüfungen sind Windows-spezifisch;
- optionale Source-Baselines beziehungsweise ESLint-/TypeScript-Integrationsvoraussetzungen sind
  in diesem abgeleiteten Repositoryprofil nicht vorhanden;
- der echte Ruff-PATH-Test wurde separat bestanden und die CI-PATH-Auflösung anschließend
  explizit gehärtet;
- lokal ist `pdflatex` nicht installiert, daher wurde genau der reale PDF-Kompilationstest
  übersprungen. Die gehostete Linux-CI installiert `texlive-latex-base` und baut/vergleicht beide
  Fassungen reproduzierbar.

Zwei reale Exporte aus demselben Source-Stand waren in Dateien, Modi und Zeitstempeln identisch.
Ein Export bestand anschließend den vollständigen unabhängigen Kundensmoke. Zusätzlich wurde der
exakte Commit `4d86b99` in einem frischen detached Worktree geprüft:

```text
Export: 240 Dateien, Digest sha256:6bd0…634ea
Kundensmoke: passed
Dokumentation: 30 Seiten konsistent
git status des frischen Worktrees: clean
```

## Definition of Done

- [x] `tools/` enthält alle Laufzeitressourcen.
- [x] `docs/toolingdocs/` enthält die vollständige portable Dokumentation.
- [x] Kein Laufzeitcode benötigt ein Template-Repository oder dessen Git-Historie.
- [x] Alte Repository-ID, URL, Lifecycle-Pakete und `.template/`-State sind entfernt.
- [x] Profile und Konfiguration werden aus `tools/resources/` geladen.
- [x] Projektpfade sind konfigurierbar.
- [x] Tooling-Virtualenv, Runtime, Logs, Caches und Builds liegen nicht unter `tools/`.
- [x] Check ist read-only; Full-Fix besitzt Backup, Verifikation und Rollback.
- [x] Produktcode und unbekannte Dateien bleiben unverändert.
- [x] Alle fünf Profile und alle neun geforderten unabhängigen Fixture-Typen sind geprüft.
- [x] Wiederholung ist idempotent.
- [x] Austausch von `tools/` und `docs/toolingdocs/` ist geprüft.
- [x] Echte Vorgängerpayloads besitzen registrierte direkte Migrationen nach `0.4.0`.
- [x] Dokumentation und beide Fallstudienfassungen wurden neu erstellt.
- [x] Root-README und Übergabe sind vom Export ausgeschlossen.
- [x] Export ist deterministisch, selbstvalidierend und fail-closed.
- [x] Source-only-Tests sind physisch vom Kundenpayload getrennt.
- [x] Linux- und Windows-CI bilden den realen Kopier-/Check-/Fix-/Test-/Wiederholungslauf ab.
- [x] Phase-8-Diff wurde vollständig geprüft und atomar committed.

## Noch offen

### 1. Lokalen Branch nur nach ausdrücklicher Freigabe pushen

Der Remote-Tracking-Stand ist weiterhin `6b48439`. Nach dem Commit dieses Dokuments ist der lokale
Branch vier Commits voraus:

```text
74400bb 🧩 Add profile actions and portable migration safeguards
ee4d4fe 📚 Document portable tooling and bilingual case study
4d86b99 📦 Add deterministic portable export and CI
📝 Complete portable tooling workflow handoff (dieser Commit)
```

Es wurde in diesem Arbeitsgang bewusst nicht gepusht. Der nächste berechtigte Schritt lautet:

```sh
git push origin refactor/portable-tooling
```

### 2. Gehostete CI nach dem Push beobachten

Der lokale Rechner besitzt weder Windows noch `pdflatex`. Deshalb muss der erste GitHub-Actions-
Lauf nach dem Push als externe Plattformbestätigung beobachtet werden. Erwartet werden beide
grünen Jobs und insbesondere der bytegleiche deutsche/englische PDF-Build. Ein externer Fehler ist
nicht durch Deaktivieren des Gates zu umgehen, sondern in einem neuen atomaren Fixcommit zu
beheben.

### 3. Veröffentlichung bleibt eine separate Entscheidung

Der Exportbefehl erstellt ein geprüftes Verzeichnis, publiziert oder signiert es aber nicht. Falls
ein Release verteilt werden soll, müssen Archivformat, externe Checksummen, Signatur,
Aufbewahrungsort und Releasefreigabe separat festgelegt werden. Das Payload-Manifest ersetzt diese
Vertrauenskette nicht.

## Sichere Fortsetzung

```sh
git status --short --branch
git rev-list --left-right --count origin/refactor/portable-tooling...HEAD
git log --oneline --decorate -10
```

Für lokale Prüfungen weiterhin eine Python-Umgebung außerhalb von `tools/` verwenden:

```sh
TOOLING_PYTHON=/absolute/path/to/external-venv/bin/python

TEMPLATE_TOOLING_NESTED_TEST=1 PYTHONDONTWRITEBYTECODE=1 \
  "$TOOLING_PYTHON" -m pytest -q -p no:cacheprovider \
  tools/tests tests/source docs/toolingdocs/case-study/tests

PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" -m pytest -q -p no:cacheprovider \
  tools/tests/acceptance/test_copy_matrix.py \
  tools/tests/acceptance/test_tooling_replacement.py \
  tests/source/test_historical_tooling_migration.py

PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" tools/control.py docs check
```

Vor einem Release das Manifest nach jeder Änderung innerhalb von `tools/` oder
`docs/toolingdocs/` als letzten Payload-Schritt neu erzeugen und danach erneut validieren. Eine
Änderung nur an Root-Dateien, `.github/` oder `tests/source/` gehört nicht zum Payload.

## Nicht regressieren

- Keine Produktdatei durch Kopie, Migration oder Full-Fix vollständig ersetzen.
- Keine Abhängigkeit von einem Template-Repository, Template-Commit oder Drei-Wege-Merge
  einführen.
- Keine Virtualenv, Runtime, Logs, Caches oder Buildausgaben unter `tools/` erzeugen.
- Geschützte Verzeichnisse nicht global freigeben; WASM und Tauri-`build/` bleiben exakte
  Ausnahmen.
- Persistierten Managed-Tree-Drift niemals still akzeptieren oder neu baselinen.
- Read-only Befehle dürfen keine Reports, State-, Bytecode- oder Cachedateien erzeugen.
- Source-only-Tests niemals wieder unter `tools/tests/` verstecken oder nur per Marker skippen.
- Manifest und Payload niemals aus unterschiedlichen Revisionen kombinieren.
- Bestehende Exportziele niemals automatisch zusammenführen oder ersetzen.
