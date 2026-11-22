# README.md – Verbrauchserfassung (Strom / Wasser / Gas)

## Projektzweck

Einzeldatei-Desktop-App zur Erfassung von Zählerständen. Sie speichert Messwerte in
SQLite, berechnet Verbrauch und Kosten und zeigt Tabelle plus Liniendiagramm.
Die UI ist auf Deutsch und basiert auf wxPython.

Hauptquelldatei:  `wx-verbrauch.py`

## Start und Voraussetzungen

```sh
python3 wx-verbrauch.py
```

- Python 3 mit `from __future__ import annotations` (Typangaben als Strings)
- `wxPython` muss installiert sein; SQLite ist Teil der Standardbibliothek
- Es gibt keine `requirements.txt`, keine Tests und keine Build-Konfiguration
- Zur Laufzeit wird neben dem Skript `verbrauch.db` angelegt; diese Datei ist
  Nutzerdatenbestand und sollte nicht gelöscht oder überschrieben werden

## Dateien im Projekt

| Datei | Rolle |
| --- | --- |
| `verbrauch_wx.py` | gesamte Anwendung: Konstanten, Helfer, Diagramm, Import-Dialog, Hauptframe |
| `verbrauch.db` | SQLite-Datenbank (`readings`, `settings`), zur Laufzeit erzeugt |
| `verbrauch_export.csv` | Beispiel-Export (Semikolon-getrennt, UTF-8 mit BOM) |

## Architektur

Alles liegt in einer Moduldatei. Reihenfolge der Abschnitte:

1. Konstanten und Mappings
2. Helfer (`connect`, `parse_float`, `parse_date`, `read_csv_rows`, `format_number`, `style_list`)
3. `ChartPanel` – eigenes `wx.Panel` mit manueller `wx.PaintDC`-Zeichnung
4. `ImportDialog` – `wx.Dialog` mit Spaltenzuordnung und Vorschau
5. `VerbrauchFrame` – Hauptfenster mit Eingabe, Tabelle, Diagramm, Zusammenfassung
6. `if __name__ == "__main__"` – `wx.App`, Frame, `MainLoop`

### Wichtige Konstanten

- Medien: `strom`, `wasser`, `gas`, zusätzlich Pseudo-Medium `alle`
- Labels: `MEDIUM_BY_LABEL` (UI → DB), `LABEL_BY_MEDIUM` (DB → UI)
- `DATA_MEDIUM_LABELS = ("Strom", "Wasser", "Gas")` – `alle` ist hier bewusst nicht enthalten
- Einheiten: Strom `kWh`, Wasser/Gas `m³`
- Standardpreise: `0.30`, `2.10`, `1.20` EUR pro Einheit
- `CHART_COUNT = 36` – Anzahl der letzten Messungen im Diagramm
- Farben: Strom blau, Wasser grün, Gas orange; `DARK_RED` markiert negative Verbräuche

## Datenmodell (`connect()`)

```sql
readings(medium TEXT NOT NULL, datum TEXT NOT NULL, stand REAL NOT NULL,
         PRIMARY KEY(medium, datum))
settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)
```

- Datum wird immer als ISO-String `YYYY-MM-DD` gespeichert
- Preise liegen als `preis_<medium>` (Text!) in `settings`; beim Start werden fehlende
  Einträge mit `INSERT OR IGNORE` aus `DEFAULT_PRICES` angelegt
- Wenn `verbrauch.db` fehlt, aber `stromverbrauch.db` existiert, wird letztere kopiert
- Migration: besitzt `readings` keine Spalte `medium`, wird die alte Tabelle in
  `readings_old` umbenannt, eine neue Tabelle erstellt und alle alten Zeilen als
  `strom` übernommen; alter Key `preis` wird zu `preis_strom`
- Alle Schreibzugriffe nutzen `ON CONFLICT(medium, datum) DO UPDATE SET stand=excluded.stand`
  (Upsert), d. h. gleiche Kombination wird aktualisiert, nicht dupliziert
- `on_close()` schließt die Verbindung und zerstört das Frame

## Parsing und Formatierung

- `parse_float`: akzeptiert `123.4`, `123,4`, `1.234,56` und `1,234.56`; Leerzeichen und
  NBSP werden entfernt; bei gemischten Trennzeichen entscheidet das zuletzt stehende
- `parse_date`: zuerst `date.fromisoformat`, danach `%d.%m.%Y`, `%d/%m/%Y`, `%Y/%m/%d`, `%d-%m-%Y`
- `read_csv_rows`: UTF-8 mit BOM, Fallback CP1252; Trennzeichen per `csv.Sniffer`
  aus `; , \t |`, sonst Mehrheitsentscheid; leere Zeilen werden verworfen
- `format_number`: immer zwei Nachkommastellen, Dezimalkomma (deutsche Anzeige)
- `style_list`: schwarzer Hintergrund, weiße Schrift – auch bei Dark Theme beibehalten

## Geschäftslogik

- `load_rows(medium)`: Zeilen nach Datum sortiert; `verbrauch = stand - vorheriger stand`
  (auf 3 Stellen gerundet), erste Zeile hat `verbrauch = None`; `kosten = verbrauch * preis`
- `VerbrauchFrame.refresh()` delegiert an `_refresh_single(medium)` oder `_refresh_all()`
- Einzelmedium: Tabelle mit Einheit im Spaltenkopf, Zusammenfassung mit Gesamtverbrauch,
  Gesamtkosten und Ø pro Tag (`total / Tage zwischen erster und letzter Messung`);
  Diagramm zeigt die letzten 36 Verbräuche
- `alle`: alle drei Medien in einer Tabelle (sortiert nach Datum, dann Medium),
  Gesamtkosten über alle Medien, Gesamtverbrauch/Tagesmittel bewusst `–`;
  Statuszeile zeigt die Einzelverbräuche; Diagramm zeigt drei normalisierte Reihen
  (je Reihe eigene Y-Skala, Wertebereich in der Legende), weil kWh und m³
  nicht direkt vergleichbar sind
- Negative Verbräuche (Zählerstand zurückgesetzt) werden in der Tabelle dunkelrot markiert,
  aber nicht gefiltert

## UI-Ablauf (`VerbrauchFrame`)

- Eingabezeile: Medium (inkl. `Alle`), Datum (Default heute), Zählerstand, `Speichern`
- Preiszeile: Label mit passender Einheit, Preis, `Preis übernehmen`
- Bei `Alle` werden Preisfelder deaktiviert und `Speichern` zeigt einen Hinweis
- Tabellenbuttons: `Auswahl löschen` (mit Ja/Nein-Bestätigung), `Aktualisieren`,
  `CSV importieren`, `CSV exportieren`
- Menü `Datei`: Import, Export, Beenden; Menü `Hilfe`: Über
- Untere Zeile: `ChartPanel` links, `Zusammenfassung` rechts
- Statuszeile (`wx.StatusBar`) für Rückmeldungen zu Speichern/Löschen/Import/Export

## CSV-Import

- Dateiauswahl: `*.csv;*.txt` oder alle Dateien
- `ImportDialog` erkennt eine Kopfzeile an Schlüsselwörtern wie `datum`, `date`,
  `stand`, `zähler`, `wert`, `medium`; sonst werden generische Spaltennamen vergeben
- Vorschau zeigt die ersten 8 Zeilen; Medium sowie Datum- und Zählerstand-Spalte
  werden per `wx.Choice` gewählt (Spaltenindex aus dem String vor `:`)
- Ungültige Zeilen werden gesammelt, nicht abgebrochen; gültige Zeilen als
  `(medium, ISO-Datum, stand)` importiert und per Upsert übernommen
- Nach dem Import wird das Medium auf das der ersten Zeile gesetzt und neu geladen

## CSV-Export

- Nur für ein konkretes Medium; bei `alle` erscheint ein Hinweis
- Dateiname default: `verbrauch_<medium>_<YYYY-MM-DD>.csv`
- UTF-8 mit BOM, Trennzeichen `;`
- Kopfzeile: `Medium;Datum;Zählerstand (<Einheit>);Verbrauch (<Einheit>);Kosten (EUR)`
- Erster Verbrauch und erste Kosten bleiben leer (kein Vorgängerwert)

## Hinweise für Änderungen

- Die App ist bewusst monolithisch: neue Medien erfordern Anpassung von
  `MEDIUM_BY_LABEL`, `DATA_MEDIUM_LABELS`, `DEFAULT_PRICES`, `UNITS` und `SERIES_COLORS`
- Preise werden als Text in der DB gespeichert und beim Lesen mit `parse_float`
  robuster geparst – beim Schreiben nicht einfach `float` ohne `format_number` nehmen
- Das Diagramm wird komplett manuell gezeichnet; für Layout-/Farbänderungen
  `ChartPanel.on_paint()` anpassen und `Refresh()` nach Datenänderungen aufrufen
- Änderungen an der Tabelle müssen `style_list()` beibehalten, sonst verschwindet
  weiße Schrift im Dark Theme
- Nach Datenänderungen immer `commit()` und `refresh()` aufrufen
- Es gibt keine automatisierten Tests; Smoke-Test: App starten, Messwert speichern,
  Medium wechseln, Import/Export prüfen, DB-Inhalt kontrollieren
