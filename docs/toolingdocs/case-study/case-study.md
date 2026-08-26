<!-- AUTO-GENERATED:backlink START -->
[← Tooling documentation](../index.md)
<!-- AUTO-GENERATED:backlink END -->

# Portable tooling case study / Fallstudie zum portablen Tooling

This directory contains a newly written, bilingual case study of the portable tooling
architecture. The English and German editions use the same evidence model and diagrams, but
each edition is a complete document rather than a short translation note.

Dieser Ordner enthält eine vollständig neu verfasste, zweisprachige Fallstudie zur portablen
Toolingarchitektur. Die englische und die deutsche Fassung verwenden dasselbe Evidenzmodell
und dieselben Diagramme; beide Fassungen sind eigenständige vollständige Dokumente.

## Contents / Inhalt

- [`source/en/main.tex`](source/en/main.tex): English case study.
- [`source/de/main.tex`](source/de/main.tex): deutsche Fallstudie.
- [`assets/architecture-pipeline.tex`](assets/architecture-pipeline.tex): portable LaTeX
  diagram of the transactional integration pipeline.
- [`assets/ownership-boundary.tex`](assets/ownership-boundary.tex): portable LaTeX diagram
  of the project/tooling ownership boundary.
- [`build.py`](build.py): standard-library build driver for deterministic `pdflatex` runs.
- [`tests/test_reproducible_build.py`](tests/test_reproducible_build.py): structural and
  byte-reproducibility tests.

## Reproducible build / Reproduzierbarer Build

The builder requires `pdflatex`, fixes time, timezone, locale and TeX cache locations, redirects
TeX output and runtime state into disposable directories, runs two passes from each source
directory, and publishes only the final PDFs to an explicitly external output directory. It
refuses to write into this case-study tree and restores prior editions if grouped publication
fails.

Der Builder benötigt `pdflatex`, fixiert Zeit, Zeitzone, Locale und TeX-Cachepfade, leitet
TeX-Ausgaben und Laufzeitzustand in automatisch entfernte Verzeichnisse um, führt zwei Läufe
aus dem jeweiligen Quellverzeichnis aus und veröffentlicht ausschließlich die fertigen PDFs
in ein explizit externes Ziel. Ausgaben innerhalb dieses Quellbaums werden abgelehnt; schlägt
die gemeinsame Veröffentlichung fehl, werden vorherige Fassungen wiederhergestellt.

```sh
python docs/toolingdocs/case-study/build.py --language all --output-dir ../portable-tooling-case-study-output
```

The output files are `portable-tooling-case-study-de.pdf` and
`portable-tooling-case-study-en.pdf`. No PDF, `.aux`, `.log`, `.toc`, SyncTeX or latexmk
state belongs in the source tree.

Die Ausgabedateien heißen `portable-tooling-case-study-de.pdf` und
`portable-tooling-case-study-en.pdf`. PDFs, `.aux`-, `.log`-, `.toc`-, SyncTeX- oder
latexmk-Zustände gehören nicht in den Quellbestand.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  docs/toolingdocs/case-study/tests
```

The PDF test skips with an explicit reason when `pdflatex` is unavailable; all structural
checks continue to run. / Fehlt `pdflatex`, wird nur der PDF-Test mit expliziter Begründung
übersprungen; die Strukturprüfungen laufen weiterhin.

<!-- AUTO-GENERATED:docs-index START -->
- (no pages)
<!-- AUTO-GENERATED:docs-index END -->
