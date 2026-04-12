# FSV Unterjesingen Website

Die Website des Flugsportvereins Unterjesingen e.V. wird mit [Hugo](https://gohugo.io/) gebaut. Das Repository enthält die Inhalte, Templates, Assets und Hilfsskripte für die Seite.

## Voraussetzungen

- `hugo`
- `node` und `npm`

## Installation von `node`, `npm` und Hugo

### `node` und `npm`

`npm` ist der Standard-Paketmanager für Node.js und wird zusammen mit Node.js installiert. Offizielle Downloadseite: <https://nodejs.org/en/download>. Nach der Installation prüfen:
```console
node --version
npm --version
```

### Hugo

Hugo sollte aus den offiziellen Installationsanleitungen der Hugo-Dokumentation installiert werden: <https://gohugo.io/installation/>. Danach die Installation prüfen:
```console
hugo version
```
Die JavaScript-Abhängigkeiten werden einmalig mit `npm install` installiert.

## Arbeitsweise im Repository

Änderungen werden immer über Pull Requests in den Branch `master` eingebracht. Es darf niemals direkt auf `master` committed werden.

Empfohlener Ablauf:

1. Einen eigenen Arbeits-Branch erstellen, der von `master` abzweigt.
2. Änderungen im Arbeits-Branch umsetzen.
3. Einen Pull Request mit Ziel `master` öffnen.
4. Erst nach Review und Freigabe in `master` mergen.

## Lokale Vorschau

```console
npm install
hugo server
```

Danach läuft die Website standardmäßig unter <http://localhost:1313/>. Beim ersten Mal wird `hugo server` wahrscheinlich ein paar Minuten brauchen, bevor die Website lokal verfügbar ist. Das liegt daran, dass Hugo Versionen der Bilder auf der Website in verschiedenen Größen generiert (Medienoptimierung). Diese werden unter `resources/_gen/images` gespeichert, müssen aber nur einmal generiert werden.

Für die Medienverwaltung gibt es einen separaten lokalen Editor:

```console
npm run media-editor
```

Der Editor ist dann unter <http://127.0.0.1:4173/> erreichbar.

## Wichtige Verzeichnisse

- `content/`: Seiteninhalte
- `content/blog/`: Blog-Beiträge als Hugo Leaf Bundles
- `content/media/`: zentrale Medienbibliothek
- `layouts/`: Templates und Shortcodes
- `assets/`: CSS, JavaScript und globale Assets
- `static/`: statische Dateien
- `scripts/`: Hilfsskripte, insbesondere der Medieneditor unter `scripts/media_editor.js`

## Neue Blog-Beiträge anlegen

Blog-Beiträge liegen jeweils in einem eigenen Ordner unter `content/blog/` und bestehen mindestens aus einer `index.md`.

Ein neuer Beitrag kann zum Beispiel so angelegt werden:

```bash
hugo new content/blog/2026-04-12-mein-artikel/index.md
```

Der Hugo-Standardarchetyp erzeugt hier zunächst TOML-Front-Matter. Im Repository wird für Inhalte überwiegend YAML verwendet; den erzeugten Block daher am besten direkt in dieses Schema umstellen:

```yaml
---
title: "Mein Artikel"
date: 2026-04-12T18:30:00+02:00
author: "Vorname Nachname"
description: "Kurze Zusammenfassung für die Blog-Übersicht."
thumbnail: "2026-04/mein-bild"
---
```

Hinweise zu den wichtigsten Feldern:

- `title`: Überschrift des Beitrags
- `date`: Veröffentlichungsdatum mit Uhrzeit
- `author`: Name des Autors oder der Autorin
- `description`: Teasertext für Listenansichten und Vorschauen
- `thumbnail`: Bild für die Blog-Karte; muss auf einen Eintrag aus der Medienbibliothek zeigen

Der eigentliche Inhalt folgt unter dem Front Matter (der mit `---`-abgerenzte Block) in normalem Markdown. Ein kurzen Überblick der Markdown Features findet sich [hier](https://blog.stueber.de/posts/markdown/). Bitte beachte, dass für die Einbettung von Bildern statt dem normalen Markdownsyntax (`![alt text](path.jpg)`) Hugo Shortcodes (`{{< img src=...`, siehe folgender Abschnitt) verwendet werden.

## Bilder im Inhalt einfügen

Neue Bilder sollen **nicht direkt in in den Seitenordner kopiert** und auch nicht von Hand unter `content/media/` angelegt werden. Für neue Inhaltsbilder ist ausschließlich der Medieneditor vorgesehen.

Empfohlener Ablauf:

1. `node scripts/media_editor.js` starten.
2. Im Browser `http://127.0.0.1:4173/` öffnen.
3. Bilddatei hochladen.
4. Titel und insbesondere `Alt`-Text pflegen.
5. Den Medienpfad aus dem Editor verwenden, zum Beispiel `2026-04/mein-bild`.

Im Markdown dann mit den vorhandenen Shortcodes einbinden:

```go-html-template
{{< img src="2026-04/mein-bild" alt="Startaufstellung am Flugplatz Poltringen" >}}
```

Wenn eine Bildunterschrift gewünscht ist:

```go-html-template
{{< figure
  src="2026-04/mein-bild"
  caption="Startaufstellung am Samstagmorgen."
  alt="Startaufstellung am Flugplatz Poltringen"
>}}
```

Wichtig:

- `src` verweist auf den Medienpfad aus `content/media`, ohne `media.jpg`
- `thumbnail` in Blog-Beiträgen funktioniert ebenfalls nur mit Medien aus der Bibliothek
- neue Inhaltsbilder sollen nicht als lokale Page Resources im Beitragsordner landen

## Hero-Bilder als Page Resources

Die einzige vorgesehene Ausnahme für lokale Bilder im Seitenordner sind Hero-Bilder.

Beispiel für einen Seitenordner:

```text
content/blog/2026-04-12-mein-artikel/
├── hero-image.jpg
└── index.md
```

Dazu in `index.md`:

```yaml
---
title: "Mein Artikel"
date: 2026-04-12T18:30:00+02:00
author: "Vorname Nachname"
description: "Kurze Zusammenfassung für die Blog-Übersicht."
thumbnail: "2026-04/mein-bild"
hero_image:
  url: "hero-image.jpg"
  brightness: 0.65
---
```

Hinweise:

- `hero_image.url` verweist auf eine Datei im selben Ordner wie die `index.md`.
- `brightness` ist ein optionales Feld und steuert die Abdunklung des Hero-Bilds; Werte zwischen `0` und `1` werden hier erwartet.
- Für Hero-Bilder ein breites Querformat verwenden, idealerweise mindestens ca. `1600px` Breite.
- Ein Hero-Bild ersetzt **nicht** das `thumbnail`; für die Blog-Übersicht wird weiterhin ein Medienbibliotheksbild für die Vorschau des Blog-Beitrags benötigt.

## Historische Altbestände

Im Repository gibt es noch ein paar migrierte ältere Beiträge mit lokalen Bildern im jeweiligen Bundle. Das ist ein Altbestand aus der WordPress-Migration. Diese sollten idealerweise auch zur Medienbibliothek hinzugefügt werden und sind wie folgt:

- `content/blog/2015-06-23-alleinflug-nathanael/image-02.jpg`
- `content/blog/2016-03-02-messestand-fdf/image-01.jpg`
- `content/blog/2021-05-01-weglide-streckenflug/image-02.jpg`
- `content/blog/2021-05-01-weglide-streckenflug/image-03.png`
- `content/blog/2021-05-01-weglide-streckenflug/image-04.png`
- `content/blog/2021-05-01-weglide-streckenflug/image-05.png`
- `content/blog/2021-05-01-weglide-streckenflug/image-06.png`
- `content/blog/2021-05-01-weglide-streckenflug/image-07.png`
- `content/blog/2021-05-01-weglide-streckenflug/image-08.png`
- `content/blog/2021-05-01-weglide-streckenflug/image-09.png`

Für neue Inhalte gilt immer: Normale Bilder immer über den Medieneditor, lokale Page Resources nur für Hero-Bilder.
