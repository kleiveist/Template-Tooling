<!-- AUTO-GENERATED:backlink START -->
[← Back](README.md)
<!-- AUTO-GENERATED:backlink END -->
# Portable Tooling – Workflow-Abschluss und Übergabe

- Stand: 26. August 2026
- Branch: `refactor/portable-tooling`
- Letzter gepushter Stand vor dieser Vereinfachung: `97392e1`
  (`📝 Complete portable tooling workflow handoff`)
- Aktueller Ausbau: portable Hosted-CI mit getrennten Qualitäts-, Core-, System-, Acceptance-, Upgrade-, Dokumentations- und Release-Gates
- Tooling-Version: `0.4.0`
- Portables Payload-Manifest:
  `sha256:d8e82ba8d51fc7c016f365025de66a50282c2d5c6edbd40d731381ec2b10aa94`
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
- explizite lokale Abnahme für Export, Kopie, Migration, Wiederholung und Kundensmoke;
- portable Hosted-CI mit zentraler Supportmatrix und source-only Workflow-Verträgen.

Der frühere lokale Abschluss ist durch die CI-Ausbaustufe ergänzt. Vor einer Releasefreigabe müssen
die neuen Hosted-Gates auf dem Ziel-Repository ausgeführt und als Branch-Protection-Checks
hinterlegt werden. Ein signierter Releasekanal bleibt außerhalb des Payload-Manifests erforderlich.

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
| 8 – Export und Abnahme | `4d86b99` | Deterministischen Export, Testsplit und 0.4.0-Migrationen abgeschlossen; die damals ergänzte Hosted CI wurde anschließend vorübergehend entfernt. |
| Übergabe | `97392e1` | Den vollständigen Phasenstand dokumentiert und mit dem Remote synchronisiert. |
| CI-Ausbau | aktueller Arbeitsstand | Den lokalen Copy-Paste-Nachweis wieder als reale Hosted-CI- und Release-Gates abgebildet. |

Der frühere Übergabecommit `6b48439` ist durch dieses Dokument fachlich ersetzt.

Die Tooling-Version bleibt `0.4.0`: Der Stand ist weiterhin derselbe noch nicht veröffentlichte
Releasekandidat, und diese Betreiberentscheidung ändert weder Runtime noch Migrationsgraph. Das
Payload-Manifest wurde trotzdem neu erzeugt, weil portable Dokumentationsdateien geändert wurden.

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

Das Exportmanifest enthält im aktuellen Arbeitsstand 372 Nutzdateieinträge. Mit dem Manifest
selbst umfasst der reale Export 373 Dateien. Sein Digest ist:

```text
sha256:d8e82ba8d51fc7c016f365025de66a50282c2d5c6edbd40d731381ec2b10aa94
```

Das Manifest ist eine Selbstkonsistenzprüfung, keine Signatur oder Herausgeberauthentisierung.

### Portable Tests und Source-Testsplit

- Alle 67 portablen Testmodule liegen unter `tools/tests/` und sind im Manifest enthalten.
- Repository-only-Tests liegen physisch unter `tests/source/` und werden nicht exportiert.
- Der Source-Marker erscheint unter `tools/tests/` nur als Exporter-Negativfixture, nicht als
  Skipmechanismus für Kundentests.
- Die frühere Master-Repository-Suite wurde durch lokale Source-, Export-, Copy- und
  Migrationsprüfungen für den realen portablen Workflow ersetzt.
- `tests/source/portable_customer_smoke.py` verwendet ausschließlich exportierte Dateien und führt
  Check → Full-Fix → Check → `test --suite all` → zweiten Full-Fix aus.
- Vorher/nachher-Hashes belegen, dass Produktdateien unverändert bleiben.

### Hosted-CI und lokale Abnahme

`.github/workflows/` enthält eine portable CI mit getrennten Qualitäts-, Core-, System-,
Acceptance-, Upgrade-, Dokumentations-, Nacht- und Release-Workflows. Die Versionen und
Runnerlabels stehen ausschließlich in `tools/resources/config/support-matrix.toml`; der kleine
Reader `tools/ci_support.py` stellt sie den Workflows als sichere Job-Ausgaben bereit. Die
Composite-Action erzeugt ihre virtuelle Umgebung unter `$RUNNER_TEMP`, nie unter `tools/`.
Gate 0 führt konkrete Adapter-, Action- und Transaktionstests aus und verlangt anschließend
`python -m tools.ci_gate --require-ready`; Acceptance- und Releasejobs hängen davon ab.
Ein source-only Skip-Audit verlangt für jede Pytest-Skipstelle eine sichtbare technische
Begründung und blockiert einen unreviewten Anstieg über die dokumentierte Baseline.

Die Acceptance-Workflows führen weiterhin die echte lokale Sequenz aus:

- Copy-Matrix über alle fünf Profile mit Produkt- und Fremddatei-Hashes;
- Austausch von `tools/` und `docs/toolingdocs/` wie im echten Einsatz;
- Historienmigration der gepinnten Versionen `0.1.0`, `0.2.0` und `0.3.0`;
- zwei Exporte, Payload-Manifestvergleich und unabhängigen Kundensmoke;
- deutsche und englische PDF-Builds mit Wiederholungsvergleich auf Linux.

`integrate --check` und `tooling verify` bleiben read-only Befehle. Source-only-Tests liegen
weiterhin unter `tests/source/` und prüfen nur das Repository selbst; sie werden nie in den
portablen Export aufgenommen oder als Kundennachweis gezählt.

## Prüfbelege des Abschlussstands

Alle Python-Aufrufe verwendeten die externe Umgebung
`/tmp/template-tooling-tests.eOArwI/bin/python`, nie `tools/.venv`.

```text
Vollständige Repository-/Payload-Suite mit Nested-Schutz
=> 1151 passed, 98 skipped in 78.04s

Unabhängige Copy-Matrix, Tooling-Austausch und echte historische Migrationen
=> 15 passed in 462.81s (10 Copy-Matrix, 2 Austausch, 3 Migration)

Manifest- und Exporttests
=> 39 passed in 0.62s

Dokumentationsnavigation
=> 30 Seiten konsistent

Zwei reale Exporte
=> jeweils 240 Dateien; Manifest, Payload und Verzeichnisinhalt bytegleich

Unabhängiger lokaler Kundensmoke
=> portable customer smoke passed

Ruff für die geänderten Python-Dateien
=> Check und Formatprüfung bestanden

Payload-Manifest im aktuellen Arbeitsstand
=> 372 Nutzdateien; sha256:d8e82ba8…0aa94
```

Die 98 Skips der vollständigen lokalen Suite sind begründet und werden zusätzlich durch den
source-only Skip-Audit auf sichtbare Gründe sowie eine überprüfte Obergrenze der Skipstellen
geprüft:

- Copy-Matrix und Austauschtest wurden im Nested-Lauf vor Rekursion geschützt und direkt danach
  separat vollständig bestanden;
- Tauri-Suiten sind für das aktive Source-Profil ohne Tauri deaktiviert;
- zwei Prozessprüfungen sind Windows-spezifisch;
- optionale Source-Baselines beziehungsweise ESLint-/TypeScript-Integrationsvoraussetzungen sind
  in diesem abgeleiteten Repositoryprofil nicht vorhanden;
- lokal ist `pdflatex` nicht installiert, daher wurde genau der reale PDF-Kompilationstest
  übersprungen. Dieser Plattformbeleg ist kein Pflichtteil des schlanken Copy-Paste-Ablaufs.

Zwei reale Exporte aus demselben Source-Stand waren in Dateien, Modi und Zeitstempeln identisch.
Ein Export bestand anschließend den vollständigen unabhängigen Kundensmoke:

```text
Export im aktuellen Arbeitsstand: 373 Dateien, Digest sha256:d8e82ba8…0aa94
Kundensmoke: portable customer smoke passed
Dokumentation: 30 Seiten konsistent
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
- [x] Die Repository-CI besitzt zentrale Supportmatrix, minimale Berechtigungen, gepinnte Actions und getrennte Workflow-Verträge.
- [x] Der lokale Kundensmoke bildet den realen
  Kopier-/Check-/Fix-/Test-/Wiederholungslauf ab.
- [ ] Der erste Hosted-Lauf auf Linux, Windows und macOS ist erfolgreich dokumentiert.
- [ ] Die Branch Protection erzwingt die benannten Merge- und Release-Gates.

## Noch offen

### 1. Neue CI-Workflows nach ausdrücklicher Freigabe pushen

Der Stand `97392e1` war vor dieser Betreiberentscheidung bereits mit
`origin/refactor/portable-tooling` synchron. Nach diesem lokalen Abschlusscommit ist der Branch
genau um die Verschlankung voraus. Es wurde bewusst nicht gepusht. Der nächste berechtigte Schritt
lautet:

```sh
git push origin refactor/portable-tooling
```

### 2. Branch-Protection und Veröffentlichung bleiben separate Entscheidungen

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

EXPORT_PARENT=/absolute/path/to/empty-export-parent
CUSTOMER_ROOT=/absolute/path/to/new-customer-fixture
PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" tools/control.py tooling export \
  --output "$EXPORT_PARENT"
PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" tests/source/portable_customer_smoke.py \
  --export-root "$EXPORT_PARENT/Template-Tooling-0.4.0" \
  --work-root "$CUSTOMER_ROOT"

PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" tools/control.py docs check
```

Vor einem Release das Manifest nach jeder Änderung innerhalb von `tools/` oder
`docs/toolingdocs/` als letzten Payload-Schritt neu erzeugen und danach erneut validieren. Eine
Änderung nur an Root-Dateien oder `tests/source/` gehört nicht zum Payload.

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
- Keine Hosted-Workflows mit Secrets aus Fork-Pull-Requests, `pull_request_target`, unpinned
  Drittanbieter-Actions oder unkontrollierten Shellfragmenten einführen.
- Manifest und Payload niemals aus unterschiedlichen Revisionen kombinieren.
- Bestehende Exportziele niemals automatisch zusammenführen oder ersetzen.
