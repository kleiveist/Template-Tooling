# Portable Tooling – Workflow-Übergabe

- Stand: 26. August 2026
- Branch: `refactor/portable-tooling`
- Letzter Implementierungscommit: `30b318c`
  (`🛡️ Harden portable tooling acceptance and verification`)
- Ausgangspunkt: `main` bei `9fefcdd`

Dieses Dokument hält den tatsächlich erreichten Stand fest. Es ersetzt keine Tests und
erklärt Phase 7 oder Phase 8 nicht vorzeitig für abgeschlossen.

## Kurzfassung

Die Architektur-, Integrations- und Copy-Test-Grundlage aus Phase 1 bis 6 ist committed.
Das alte Git-Template-/Lifecycle-Modell wurde entfernt und durch einen portablen,
profilgesteuerten Projektkontext mit sicherer Planung, Transaktion, Migration,
Verifikation und persistentem Tooling-State ersetzt.

Noch nicht fertig sind:

1. konkrete Built-in-Adapter, die aus Profilen reale, allowlist-basierte
   Produktintegrationen und Capabilities ableiten;
2. der echte transaktionale Executor für notwendige Dependency-, Quality- und
   Testaktionen innerhalb von `integrate --full-fix`;
3. eine reale versionierte Produktionsmigration und ein kompletter
   Copy-Matrix-Wiederholungslauf nach dem letzten WASM-Digest-Fix;
4. Phase 7: portable Dokumentation, neue deutsche/englische Fallstudie und Root-README;
5. Phase 8: Exporter, reale CI und abschließende Definition-of-Done-Prüfung.

Der Branch ist deshalb ein sicherer Fortsetzungsstand, aber noch kein finaler Release-Stand.

## Commit- und Phasenübersicht

| Phase | Commit | Erreichter Stand |
| --- | --- | --- |
| 1 – Bestandsaufnahme | `1c6cc21` | Alle übernommenen Module und Tests als `KEEP`, `REFACTOR`, `EXTRACT`, `REPLACE` oder `REMOVE` inventarisiert. |
| 2 – Portabler Kontext | `a3ba1ac` | Zentraler `ProjectContext`, portable Ressourcen-, Dokumentations-, State- und Projektpfade sowie `project-tooling.toml`. |
| 3 – Lifecycle-Extraktion | `70c4bf3` | Sicherheits-, Manifest-, State-, Planungs-, Migrations-, Transaktions-, Verifikations- und Reporting-Logik extrahiert; altes Template-Git-Modell entfernt. |
| 4 – Profile und Adapter | `1269695` | Fünf Profile, Feature-Auflösung sowie konservative Adapterverträge, Registry, Detektions-, Planungs- und Verifikationsgrundlage; konkrete Profilintegrationen bleiben offen. |
| 5 – Integration | `b578eb8` | Lesender Check, Full-Fix-Transaktion für unterstützte Config-/State-Änderungen, Migrationsrahmen, Rollback, Berichte, Eigentumsgrenzen und Idempotenz-Grundlage. |
| 6 – Copy-Acceptance | `30b318c` | Neun unabhängige Fixture-Typen, alle fünf Profile, gleichversioniger Austauschtest, Runtime-Isolation und Fail-Closed-Regressionsprüfungen; reale Profilmutation und Versionsmigration bleiben offen. |
| 7 – Dokumentation | offen | Nur Verzeichnisstruktur, Platzhalter und Refactor-Inventar vorhanden. |
| 8 – Export und CI | offen | CLI-Route vorhanden, Export selbst ist noch ein `NOT_READY`-Stub; `.github/` fehlt. |

## Was konkret abgeschlossen ist

### Phase 1 – belastbares Inventar

- Das Inventar liegt unter
  `docs/toolingdocs/development/refactor-inventory.md`.
- Wiederverwendbare Mechanismen wurden von alter Produkt-/Template-Architektur getrennt.
- Alte README-, Community-, Master-Repository- und Full-Stack-Fixture-Annahmen wurden nicht
  ungeprüft als neuer Qualitätsmaßstab übernommen.

### Phase 2 – zentrale Pfad- und Kontextarchitektur

- `tools/core/context.py` ist die zentrale Quelle für Projekt-, Tooling-, Ressourcen-,
  Dokumentations- und State-Pfade.
- Projektpfade sind über `project-tooling.toml` konfigurierbar.
- Die Konfiguration bleibt beim späteren Austausch von `tools/` und
  `docs/toolingdocs/` im Zielprojekt erhalten.
- Tooling-Virtualenv und Runtime liegen unter `.tooling-state/`, nicht unter `tools/`.

### Phase 3 – neues Integrationsmodell

- `tools/template_lifecycle/` existiert nicht mehr.
- `tools/tests/template_lifecycle/` existiert nicht mehr.
- `.template/` und das alte Template-Commit-/Baseline-Modell sind entfernt.
- Es gibt keine Referenz mehr auf die alte Repository-ID oder URL.
- Die erhaltenen Sicherheitsmechanismen liegen in `tools/core/` und
  `tools/integration/`.
- Die Transaktion besitzt Staging, Preimage-Prüfung, Backup, Nachverifikation,
  Rollback und Journal/Report.
- Der Planner vergleicht erkannten Projektzustand mit gewünschtem Profilzustand und
  führt keinen Drei-Wege-Merge über Produktdateien mehr aus.

### Phase 4 – Profile und Adapter

- Alle fünf Profile werden aus `tools/resources/profiles/` geladen:
  `web-only`, `web-cloud`, `desktop-local`, `desktop-cloud`, `full-platform`.
- Features werden zentral validiert und aufgelöst.
- Die Adapter-Registry bündelt Detektion, Planung, Transaktionsübergabe und
  Verifikation.
- Frontend, Backend, Tauri, Datenbank, Container, Quality, Testing,
  Dokumentation, CI und Release sind als Adapterbereiche vorhanden.

Wichtige Einschränkung: Die Built-in-Adapter sind derzeit überwiegend konservative
Detektionsadapter. Ihre `capabilities` sind leer, ihre Produktanforderungen sind optional,
und sie erzeugen noch keine konkreten strukturierten Integrationsoperationen für
`package.json`, `Cargo.toml`, `pyproject.toml`, `tauri.conf.json` oder Workflows. Die
Adapterarchitektur ist vorhanden; die gewünschte echte Profilintegration ist noch zu
implementieren und mit kundeneigenen Fremdschlüsseln zu testen.

### Phase 5 – sicherer Check-/Full-Fix-Kern

- `python tools/control.py integrate --check` ist als lesender Ablauf implementiert.
- `python tools/control.py integrate --full-fix` plant erneut und wendet unterstützte
  Änderungen innerhalb einer Rollback-Grenze an.
- Ein schmutziger Git-Worktree wird vor Mutation abgelehnt; geerbte `GIT_*`-Variablen
  werden dabei entfernt.
- Tooling-, projekt- und strukturiert verwaltete Pfade besitzen getrennte Regeln.
- Produktquellen und unbekannte Dateien werden nicht automatisch überschrieben.
- Persistierter Managed-Tree-Drift wird nicht still neu baselined.
- Tooling-Upgrades benötigen eine registrierte Migration.
- Tooling-Pythonquellen werden vor Mutation read-only kompiliert.
- Synthetische Erfolgsmeldungen für nicht ausgeführte Aktionen wurden entfernt.

Wichtige Einschränkung: Benötigt ein Plan echte Dependency-, Quality- oder
Testaktionen, bricht der Workflow aktuell absichtlich vor jeder Mutation mit
`Transactional dependency/quality/test action execution is unsupported` ab. Das ist
sicheres Fail-Closed-Verhalten, erfüllt aber den endgültigen Phase-5-/DoD-Anspruch noch
nicht. Diese Lücke muss vor der finalen Dokumentation geschlossen werden.

### Phase 6 – unabhängige Copy- und Austauschbarkeitstests

`tools/tests/acceptance/test_copy_matrix.py` enthält Fixtures für:

- leeres Projekt;
- Vite;
- FastAPI;
- Tauri;
- bestehendes `desktop-local`;
- bestehendes `web-cloud`;
- vollständiges `full-platform`;
- abweichende Verzeichnisnamen;
- zusätzliche unbekannte Dateien.

Die Matrix prüft unter anderem:

- Kopie von `tools/` und `docs/` in ein unabhängiges Projekt;
- `integrate --check`, `--full-fix`, erneuten Check und zweiten No-op-Full-Fix;
- alle fünf Profile;
- `tooling verify` und `test --suite all`;
- unveränderte Produktdatei-Hashes;
- bytegenaue/read-only Checks und Baum-Snapshots;
- keine versehentlich kopierten Caches, Logs, Virtualenvs oder Buildartefakte;
- die einzige erlaubte `dist`-Datei:
  `tools/quality/rust_analyzer/dist/rust_quality_analyzer.wasm`.

Diese Matrix ist damit bereits ein starkes Sicherheits-, Portabilitäts- und
Idempotenznetz. Weil die Built-in-Adapter noch keine konkreten strukturierten
Profiloperationen erzeugen, beweist sie derzeit jedoch überwiegend Config-/State-Verhalten,
Detektion und Nichtbeschädigung – noch nicht, dass ein unvollständiges Kundenprojekt durch
das gewählte Profil tatsächlich vollständig integriert wird.

`tools/tests/acceptance/test_tooling_replacement.py` prüft zusätzlich:

- integriertes Zielprojekt;
- Löschen und erneutes Kopieren von `tools/` und `docs/toolingdocs/`;
- Erhalt von `project-tooling.toml`, `.tooling-state/` und Produktdateien;
- Aufruf der Migrations-CLI als gleichversionigen No-op, Verifikation und anschließende
  Idempotenz;
- Austausch auch in einem leeren Projekt ohne Produktcode.

Der Test beweist noch kein Upgrade zwischen zwei echten Tooling-Versionen: Die produktive
Migrations-Registry ist leer, und der aktuelle Replacement-Test erwartet keine pending oder
applied Migration. Eine registrierte Produktionsmigration mit alter und neuer Copy-Version
bleibt ein eigener Abnahmepunkt.

Weitere abgeschlossene Härtungen:

- Die versionierte Rust-Analyzer-WASM gehört trotz geschütztem `dist/` zum
  Managed-Tree-Digest.
- Mutation oder Löschung dieser WASM erzeugt einen schreibfreien Konflikt.
- Die WASM wird als exakt erlaubter Snapshot in die isolierte
  Transaktions-Staging-Kopie übernommen; andere `dist/`-Inhalte bleiben geschützt.
- Source-repository-spezifische Tests werden über `.template-tooling-source` markiert
  und gelangen nicht in Zielprojektprüfungen.
- Geerbte Git-Umgebungsvariablen werden auch in verschachtelten Copy-Fixtures entfernt.

## Letzte bekannte Prüfbelege

Nach dem finalen WASM-Digest-/Staging-Fix:

```text
tools/tests/integration
=> 165 passed

WASM mutate/delete regression
=> 2 passed

tooling replacement acceptance
=> 2 passed

Ruff lint für alle in Phase 6 geänderten Python-Dateien,
Ruff format check für sieben gezielt ausgewählte Kern-/Neudateien,
git diff --check
=> sauber
```

Der Formatnachweis gilt nicht für jede historische, in Phase 6 nur punktuell angepasste
Testdatei: Ein vollständiger `ruff format --check` über alle geänderten Python-Dateien
meldet noch sechs legacy-formatierte Dateien. Diese Formatabweichungen wurden nicht mit
fachlichen Änderungen in denselben Commit gezogen.

Vor diesem letzten, eng begrenzten WASM-Fix waren zusätzlich grün:

```text
vollständige unabhängige Copy-Matrix
=> 10 passed in 306.66s

vollständige nicht-rekursive Repository-Suite
=> 918 passed, 116 skipped
```

Die Copy-Matrix muss nach dem WASM-Fix noch einmal vollständig ausgeführt werden. Die
gezielten Regressionstests sind grün, aber der vollständige Fünf-Minuten-Lauf wurde wegen
des angeordneten Abbruchs nicht wiederholt.

## Was als Nächstes offen ist

### 0. Phase-4-/Phase-5-Lücken schließen und Phase 6 erneut bestätigen

- [ ] Pro Profil und Feature den tatsächlich gewünschten Integrationszustand definieren.
- [ ] Built-in-Adapter konkrete, allowlist-basierte strukturierte Operationen planen
      lassen, ohne fremde Scripts, Dependencies oder Schlüssel zu entfernen.
- [ ] Notwendige Adapter-Capabilities wie `install`, `test` und `build` implementieren;
      `run` und `stop` nur dort, wo das Profil sie wirklich benötigt.
- [ ] Einen echten transaktionalen Action-Executor für Dependency-Installation,
      Quality-Prüfungen und passende Tests entwerfen.
- [ ] Aktionen müssen innerhalb derselben Staging-/Rollback-Grenze laufen oder ihre
      externen Effekte explizit reversibel und verifiziert machen.
- [ ] Keine Shell-Fragmente aus Profilen oder Migrationen ungeprüft ausführen.
- [ ] Reale Findings statt synthetischer `PASS`-Ergebnisse erzeugen.
- [ ] Fehlerfälle müssen Projektdateien, Konfiguration und State vollständig zurückrollen.
- [ ] Acceptance-Fixtures müssen danach echte fehlende Integrationen erzeugen und
      beweisen, dass Full-Fix sie strukturiert ergänzt.
- [ ] Mindestens eine echte Produktionsmigration registrieren und einen Copy-Austausch
      von einer älteren auf die aktuelle Tooling-Version prüfen.
- [ ] Erst danach die komplette Copy-Matrix erneut laufen lassen.
- [ ] Diff prüfen und einen eigenen atomaren Commit erstellen.

### 1. Phase 7 – portable Dokumentation

Die folgenden Bereiche müssen mit neuer, ausschließlich portabler Dokumentation gefüllt
werden:

- [ ] `docs/toolingdocs/architecture/`
- [ ] `docs/toolingdocs/integration/`
- [ ] `docs/toolingdocs/guides/`
- [ ] `docs/toolingdocs/reference/`
- [ ] `docs/toolingdocs/development/`
- [ ] `docs/toolingdocs/acceptance/`
- [ ] `docs/toolingdocs/case-study/`

Erforderliche Inhalte:

- [ ] Projektkontext und Pfadauflösung;
- [ ] Profile, Features und Adaptermodell;
- [ ] Eigentumsmodell und strukturierte Änderungen;
- [ ] Check, Full-Fix, Migration und Verifikation;
- [ ] State, Drift-Erkennung, Transaktion, Backup und Rollback;
- [ ] Installation, Tests, Builds, Releases und Tooling-Ordneraustausch;
- [ ] Copy-Matrix und nachvollziehbare Akzeptanzkriterien;
- [ ] keine Darstellung als altes Full-Stack-Master-Template.

Zusätzlich:

- [ ] Root-`README.md` neu schreiben; er gehört nur zum Repository und niemals in den
      portablen Export.
- [ ] Alte Dokumentationsartefakte nicht kopieren oder nur umbenennen.
- [ ] Dokumentationslinks, Beispiele und CLI-Ausgaben gegen die echte Implementierung
      testen.

### 2. Phase 7 – Fallstudie vollständig neu erstellen

- [ ] Neue deutsche LaTeX-Quellen unter
      `docs/toolingdocs/case-study/source/de/` erstellen.
- [ ] Neue englische LaTeX-Quellen unter
      `docs/toolingdocs/case-study/source/en/` erstellen.
- [ ] Neue Gliederung, Diagramme, Texte und Bewertung erstellen.
- [ ] Das portable Tooling-Konzept zum Hauptgegenstand machen.
- [ ] Reproduzierbaren PDF-Build für beide Sprachen implementieren und testen.
- [ ] Keine PDF-, Aux-, Log- oder sonstigen Buildartefakte im Quellbestand versionieren.
- [ ] Phase-7-Diff prüfen und atomar committen.

### 3. Phase 8 – Export implementieren

`python tools/control.py tooling export` ist derzeit nur verdrahtet. In
`tools/integration/service.py` liefert `run_export()` absichtlich `NOT_READY` und Exitcode 2.

Offen:

- [ ] Deterministischen Export unter einem Namen wie
      `Template-Tooling-<version>/` erzeugen.
- [ ] Ausschließlich `tools/` und `docs/` exportieren.
- [ ] `README.md`, `WORKFLOW-HANDOFF.md`, `.git/`, `.github/`, `.tooling-state/`,
      `.venv/`, `.runtime/`, `target/`, Caches, Logs und Zwischenstände ausschließen.
- [ ] Innerhalb von `tools/tests/` portable Kundentests von Source-repository-only-Tests
      trennen. Source-only-Tests entweder verlagern oder explizit vom Export ausschließen;
      portable Runtime- und Akzeptanztests müssen vollständig erhalten bleiben.
- [ ] `.template-tooling-source` niemals exportieren. Das aktuelle Fehlen des Markers im
      Ziel lässt Source-only-Tests nur skippen und ist noch keine abschließende
      Export-Policy für deren Dateien.
- [ ] Die versionierte Rust-Analyzer-WASM exakt erlauben, ohne andere `dist/`-Artefakte
      freizugeben.
- [ ] Symlinks, Groß-/Kleinschreibungsvarianten, sensible Dateien und versteckte
      Laufzeitartefakte fail-closed behandeln.
- [ ] Exportinhalt mit einem Manifest und reproduzierbaren Hashes prüfen.
- [ ] Direkte manuelle Kopie von `tools/` und `docs/` weiterhin unterstützen.

### 4. Phase 8 – reale CI hinzufügen

`.github/` existiert aktuell nicht.

- [ ] `tools/tests/test_ci_workflows.py` und weitere alte Master-Repository-CI-Erwartungen
      zuerst durch portable CI-Akzeptanztests ersetzen. Durch den Source-Marker würden
      diese Tests beim bloßen Anlegen von `.github/workflows/` sonst wieder aktiv und
      weiterhin alte Dateien wie `ci.yml`, `profiles.yml`, `postgres.yml`, `desktop.yml`
      und `release.yml` verlangen.
- [ ] Workflow auf sauberem Checkout ausführen.
- [ ] Tooling-Umgebung außerhalb von `tools/` aufbauen.
- [ ] Export erzeugen und dessen Ausschlussregeln prüfen.
- [ ] Export in ein unabhängiges Fixture kopieren.
- [ ] Check → Full-Fix → Check → alle passenden Tests → zweiter Full-Fix ausführen.
- [ ] Produktdatei-Hashes und vollständige Idempotenz prüfen.
- [ ] Deutsche und englische Fallstudie reproduzierbar bauen.
- [ ] Keine Source-only-Tests als Kundennachweis zählen.

### 5. Abschließende Definition of Done

- [ ] Vollständige Repository-Suite grün oder jeder Skip ausdrücklich begründet.
- [ ] Vollständige Copy-Matrix nach allen Änderungen grün.
- [ ] Alle fünf Profile in unabhängigen Zielprojekten grün.
- [ ] Tooling-Austausch und registrierte Migration grün.
- [ ] `integrate --check` nachweislich bytegenau read-only.
- [ ] Zweiter `--full-fix` nachweislich No-op.
- [ ] Produktcode und unbekannte Dateien unverändert.
- [ ] Keine alte Template-ID, URL, `.template/`-State oder Lifecycle-Module vorhanden.
- [ ] Keine `.venv`, `.runtime`, `target`, Caches oder Logs versioniert oder exportiert.
- [ ] Root-README und diese Übergabe nicht im Export.
- [ ] Dokumentation und beide Fallstudienfassungen vollständig und aktuell.
- [ ] Phase-8-Diff prüfen und atomar committen.

## Empfohlene Fortsetzungsreihenfolge

1. Arbeitsbaum und Branch prüfen:

   ```sh
   git status --short
   git log --oneline --decorate -8
   ```

2. Konkrete Profilintegration in den Built-in-Adaptern implementieren.
3. Phase-5-Action-Executor mit Fehler-/Rollbacktests fertigstellen.
4. Eine reale Produktionsmigration und den versionierten Copy-Austausch ergänzen.
5. Integrationssuite und komplette Copy-Matrix ausführen.
6. Diff prüfen und den Lückenschluss atomar committen.
7. Phase 7 vollständig implementieren, testen, prüfen und committen.
8. Phase 8 vollständig implementieren, in realer CI prüfen und committen.
9. Abschließenden Export aus einem frischen Checkout testen.

Nach jedem Arbeitsblock gilt weiterhin verbindlich:

```text
Tests ausführen
→ Diff prüfen
→ atomaren Commit erzeugen
→ erst dann zum nächsten Block wechseln
```

## Relevante Testbefehle

Eine lokale Python-Umgebung außerhalb von `tools/` verwenden. Keine
`tools/.venv` anlegen. `TOOLING_PYTHON` auf einen Interpreter setzen, in dessen Umgebung
`pytest` und `ruff` installiert sind; nicht auf ein zufälliges System-Python verlassen.

```sh
TOOLING_PYTHON=/absolute/path/to/external-venv/bin/python

PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" -m pytest -q -p no:cacheprovider \
  tools/tests/integration

PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" -m pytest -q -p no:cacheprovider \
  tools/tests/acceptance/test_tooling_replacement.py

PYTHONDONTWRITEBYTECODE=1 "$TOOLING_PYTHON" -m pytest -q -p no:cacheprovider \
  tools/tests/acceptance/test_copy_matrix.py

TEMPLATE_TOOLING_NESTED_TEST=1 PYTHONDONTWRITEBYTECODE=1 \
  "$TOOLING_PYTHON" -m pytest -q -p no:cacheprovider \
  tools/tests docs/toolingdocs/case-study/tests

# Geänderte Python-Dateien der aktuellen Phase explizit an Ruff übergeben.
PHASE_BASE_COMMIT=30b318c  # Beim nächsten Block auf dessen Ausgangscommit setzen.
git diff --name-only --diff-filter=ACMR "$PHASE_BASE_COMMIT" -- '*.py' \
  | xargs -r "$TOOLING_PYTHON" -m ruff check
git diff --check
```

Hinweis: `ruff check tools` besitzt auf dem historischen Gesamtbestand bekannte, nicht zu
Phase 6 gehörende Altbefunde. Geänderte Dateien müssen trotzdem vollständig sauber sein;
unabhängige Altbefunde nicht ungeprüft in denselben Commit ziehen.

## Git- und Synchronisationsstand

Bei Erstellung dieser Übergabe zeigte `origin/refactor/portable-tooling` noch auf
`b578eb8`. Der Implementierungscommit `30b318c` und der Commit dieser Übergabedatei sind
lokal und wurden in diesem Arbeitsgang nicht gepusht. Vor einer Übergabe an einen anderen
Rechner oder Agenten daher ausdrücklich prüfen:

```sh
git status --short
git rev-list --left-right --count origin/refactor/portable-tooling...HEAD
git log --oneline --decorate -8
```

## Nicht regressieren

- Nie Produktdateien durch eine Tooling-Kopie oder Full-Fix-Operation ersetzen.
- Keine neue Abhängigkeit von einem Template-Repository, dessen Git-Historie oder
  Template-Commits einführen.
- Keine Virtualenv, Runtime, Logs oder Cargo-/Frontend-Buildausgaben unter `tools/`
  erzeugen.
- Geschützte Verzeichnisse nicht global freigeben, nur weil die eine kanonische WASM
  unter `dist/` versioniert ist.
- Persistierten Managed-Tree-Drift niemals still akzeptieren oder neu baselinen.
- Check-/Verify-Befehle dürfen keine Reports, Bytecode-Dateien, Caches oder State-Updates
  erzeugen.
- Den Source-only-Marker nie exportieren; Repository-only-Tests vor Phase 8 klar von
  portablen Kundentests trennen, statt sie nur über den fehlenden Marker zu verstecken.
- Phase 7 erst dokumentieren, wenn das tatsächliche Full-Fix-Verhalten stabil ist.
