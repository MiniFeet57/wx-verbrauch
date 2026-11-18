#!/usr/bin/env python3
"""Verbrauchserfassung für Strom, Wasser und Gas mit wxPython und SQLite.

Speichert Zählerstände pro Medium und Datum, berechnet Verbrauch und Kosten,
zeigt Tabelle + Liniendiagramm (letzte 36 Messungen) und kann CSV importieren
sowie exportieren. Option "Alle" zeigt alle Medien in einem Diagramm.

Start: python3 verbrauch_wx.py
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import sqlite3
import wx
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).with_name("verbrauch.db")
OLD_DB_PATH = Path(__file__).with_name("stromverbrauch.db")

DATA_MEDIUM_LABELS = ("Strom", "Wasser", "Gas")
MEDIUM_LABELS = DATA_MEDIUM_LABELS + ("Alle",)
MEDIUM_BY_LABEL = {
    "Strom": "strom", "Wasser": "wasser", "Gas": "gas", "Alle": "alle"
}
LABEL_BY_MEDIUM = {v: k for k, v in MEDIUM_BY_LABEL.items()}

DEFAULT_PRICES = {"strom": 0.30, "wasser": 2.10, "gas": 1.20}
UNITS = {"strom": "kWh", "wasser": "m³", "gas": "m³"}
SERIES_COLORS = {
    "strom": wx.Colour(63, 143, 208),    # Blau
    "wasser": wx.Colour(46, 160, 90),    # Grün
    "gas": wx.Colour(220, 140, 40),      # Orange
}

CHART_COUNT = 36
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y")

WHITE = wx.Colour(255, 255, 255)
BLACK = wx.Colour(0, 0, 0)
DARK_RED = wx.Colour(120, 0, 0)
GREY = wx.Colour(136, 136, 136)
GRID = wx.Colour(230, 230, 230)


# ----------------------------------------------------------------- Helfer
def connect() -> sqlite3.Connection:
    if not DB_PATH.exists() and OLD_DB_PATH.exists():
        shutil.copy2(OLD_DB_PATH, DB_PATH)
    con = sqlite3.connect(DB_PATH)

    con.execute(
        """CREATE TABLE IF NOT EXISTS settings (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL
           )"""
    )
    for medium, price in DEFAULT_PRICES.items():
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (f"preis_{medium}", str(price)),
        )

    columns = {row[1] for row in con.execute("PRAGMA table_info(readings)")}
    if columns and "medium" not in columns:
        # Migration der alten Strom-only-Datenbank
        con.execute("ALTER TABLE readings RENAME TO readings_old")
        con.execute(
            """CREATE TABLE readings (
                   medium TEXT NOT NULL,
                   datum TEXT NOT NULL,
                   stand REAL NOT NULL,
                   PRIMARY KEY(medium, datum)
               )"""
        )
        con.execute(
            "INSERT INTO readings(medium, datum, stand) SELECT 'strom', datum, stand FROM readings_old"
        )
        con.execute("DROP TABLE readings_old")
        old = con.execute("SELECT value FROM settings WHERE key='preis'").fetchone()
        if old:
            con.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES('preis_strom', ?)", (old[0],)
            )
            con.execute("DELETE FROM settings WHERE key='preis'")
    else:
        con.execute(
            """CREATE TABLE IF NOT EXISTS readings (
                   medium TEXT NOT NULL,
                   datum TEXT NOT NULL,
                   stand REAL NOT NULL,
                   PRIMARY KEY(medium, datum)
               )"""
        )
    con.commit()
    return con


def parse_float(text: str) -> float:
    """Akzeptiert 123.4, 123,4, 1.234,56 und 1,234.56."""
    match = re.search(r"-?\d[\d.,\s\u00a0]*", str(text))
    if not match:
        raise ValueError("Keine Zahl gefunden")
    number = match.group(0).replace(" ", "").replace("\u00a0", "")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    else:
        number = number.replace(",", ".")
    number = number.rstrip(".") or "0"
    return float(number)


def parse_date(text: str) -> date:
    value = str(text).strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unbekanntes Datumsformat: {text}")


def read_csv_rows(path: str | Path) -> list[list[str]]:
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = source.read_text(encoding="cp1252")
    try:
        delimiter = csv.Sniffer().sniff(raw[:4096], delimiters=";,\t|").delimiter
    except csv.Error:
        delimiter = ";" if raw.count(";") >= raw.count(",") else ","
    rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))
    return [row for row in rows if any(cell.strip() for cell in row)]


def format_number(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def style_list(ctrl: wx.ListCtrl) -> None:
    """Sichert weiße Schrift auf dunklem Grund (auch bei Dark Theme)."""
    ctrl.SetBackgroundColour(BLACK)
    ctrl.SetTextColour(WHITE)


# ------------------------------------------------------------- Diagramm-Panel
class ChartPanel(wx.Panel):
    """Liniendiagramm für eine oder mehrere Reihen.

    Jede Series: {"label", "unit", "color", "values": [(datum, wert), ...]}
    Da kWh und m³ unterschiedliche Einheiten haben, wird jede Reihe normalisiert
    und ihr Wertebereich in der Legende angezeigt.
    """

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, size=(-1, 190))
        self.SetBackgroundColour(WHITE)
        self.SetMinSize((380, 190))
        self.series: list[dict] = []
        self.title = ""
        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

    def SetData(self, series: list[dict], title: str = "") -> None:
        self.series = series
        self.title = title
        self.Refresh()

    def on_size(self, event: wx.SizeEvent) -> None:
        self.Refresh()
        event.Skip()

    def on_paint(self, event: wx.PaintEvent) -> None:
        dc = wx.PaintDC(self)
        dc.Clear()
        width, height = self.GetClientSize()
        if width <= 0 or height <= 0:
            return

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)

        left, right = 58, 14
        top, bottom = 40, 30
        base = height - bottom
        if base <= top + 10:
            return

        # Gemeinsame X-Achse über alle Reihen
        all_dates = sorted({d for series in self.series for d, _ in series["values"]})[-CHART_COUNT:]
        if not all_dates:
            dc.SetTextForeground(wx.Colour(102, 102, 102))
            message = "Noch keine Verbrauchswerte vorhanden."
            text_width, _ = dc.GetTextExtent(message)
            dc.DrawText(message, max(4, (width - text_width) // 2), height // 2 - 8)
            return

        slot = (width - left - right) / len(all_dates)
        x_by_date = {d: left + slot * (i + 0.5) for i, d in enumerate(all_dates)}

        # Reihen vorbereiten (eigene Y-Skala pro Reihe)
        prepared: list[dict] = []
        for series in self.series:
            points = [
                (x_by_date[d], value) for d, value in series["values"] if d in x_by_date
            ]
            if not points:
                continue
            values = [value for _, value in points]
            low = min(0.0, min(values))
            high = max(values)
            span = high - low or 1.0
            prepared.append({
                "label": series["label"],
                "unit": series["unit"],
                "color": series["color"],
                "points": points,
                "low": low,
                "high": high,
                "span": span,
            })

        if not prepared:
            dc.SetTextForeground(wx.Colour(102, 102, 102))
            message = "Keine gültigen Messwerte vorhanden."
            text_width, _ = dc.GetTextExtent(message)
            dc.DrawText(message, max(4, (width - text_width) // 2), height // 2 - 8)
            return

        # Titel
        dc.SetTextForeground(BLACK)
        dc.DrawText(self.title or "Verlauf", 6, 4)

        # Legende mit Wertebereich pro Reihe
        legend_x = left
        legend_y = 20
        for item in prepared:
            text = (
                f"{item['label']} {format_number(item['low'], 1)}"
                f"–{format_number(item['high'], 1)} {item['unit']}"
            )
            text_width, _ = dc.GetTextExtent(text)
            if legend_x + 18 + text_width > width - right:
                legend_x = left
                legend_y += 12
            dc.SetPen(wx.Pen(item["color"], 2))
            dc.DrawLine(legend_x, legend_y + 5, legend_x + 14, legend_y + 5)
            dc.SetTextForeground(item["color"])
            dc.DrawText(text, legend_x + 18, legend_y)
            legend_x += 18 + text_width + 14

        # Gitternetz
        dc.SetPen(wx.Pen(GRID))
        for fraction in (0.0, 0.5, 1.0):
            y = base - fraction * (base - top)
            dc.DrawLine(left, int(y), width - right, int(y))

        # Achsen
        dc.SetPen(wx.Pen(GREY))
        dc.DrawLine(left, base, width - right, base)
        dc.DrawLine(left, top, left, base)

        # Y-Beschriftung bei einer einzelnen Reihe
        if len(prepared) == 1:
            item = prepared[0]
            dc.SetTextForeground(wx.Colour(102, 102, 102))
            dc.DrawText(format_number(item["high"], 1), 4, top - 6)
            dc.DrawText(format_number(item["low"], 1), 4, base - 6)

        # Linien zeichnen
        for item in prepared:
            color = item["color"]
            points = [
                (x, base - (value - item["low"]) / item["span"] * (base - top))
                for x, value in item["points"]
            ]
            dc.SetPen(wx.Pen(color, 2))
            for index in range(1, len(points)):
                x1, y1 = points[index - 1]
                x2, y2 = points[index]
                dc.DrawLine(int(x1), int(y1), int(x2), int(y2))
            dc.SetBrush(wx.Brush(color))
            for x, y in points:
                dc.DrawCircle(int(x), int(y), 3)

        # X-Beschriftung
        step = max(1, len(all_dates) // 6)
        for index, datum in enumerate(all_dates):
            if index == 0 or index == len(all_dates) - 1 or index % step == 0:
                label = datum[5:]
                text_width, _ = dc.GetTextExtent(label)
                dc.SetTextForeground(wx.Colour(85, 85, 85))
                dc.DrawText(label, int(x_by_date[datum] - text_width / 2), base + 8)


# --------------------------------------------------------------- Importdialog
class ImportDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, path: str, default_medium: str) -> None:
        super().__init__(parent, title="CSV importieren", size=(780, 520))
        self.rows = read_csv_rows(path)
        self.records: list[tuple[str, str, float]] | None = None
        self.errors: list[str] = []

        header_words = ("datum", "date", "stand", "zähler", "zaehler", "wert",
                        "value", "verbrauch", "kwh", "messung", "medium")
        first = self.rows[0] if self.rows else []
        if any(word in " ".join(first).lower() for word in header_words):
            self.header = [cell.strip() or f"Spalte {i + 1}" for i, cell in enumerate(first)]
            self.data = self.rows[1:]
        else:
            width = max((len(row) for row in self.rows), default=0)
            self.header = [f"Spalte {i + 1}" for i in range(width)]
            self.data = self.rows
        self.has_data = bool(self.data)

        if not self.has_data:
            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(
                wx.StaticText(self, label="Die Datei enthält keine Datenzeilen."),
                0, wx.ALL, 16,
            )
            close_btn = wx.Button(self, wx.ID_CLOSE, "Schließen")
            close_btn.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_CANCEL))
            sizer.Add(close_btn, 0, wx.ALL | wx.ALIGN_RIGHT, 16)
            self.SetSizer(sizer)
            self.Fit()
            return

        self._build(default_medium)

    def _build(self, default_medium: str) -> None:
        main = wx.BoxSizer(wx.VERTICAL)
        form = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        form.AddGrowableCol(1, 1)

        form.Add(wx.StaticText(self, label="Medium:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.medium_choice = wx.Choice(self, choices=list(DATA_MEDIUM_LABELS))
        self.medium_choice.SetStringSelection(
            LABEL_BY_MEDIUM[default_medium] if default_medium != "alle" else "Strom"
        )
        form.Add(self.medium_choice, 0, wx.EXPAND)

        choices = [f"{i}: {name}" for i, name in enumerate(self.header)]
        form.Add(wx.StaticText(self, label="Datum-Spalte:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.date_choice = wx.Choice(self, choices=choices)
        self.date_choice.SetSelection(0)
        form.Add(self.date_choice, 0, wx.EXPAND)

        form.Add(wx.StaticText(self, label="Zählerstand-Spalte:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.value_choice = wx.Choice(self, choices=choices)
        self.value_choice.SetSelection(1 if len(choices) > 1 else 0)
        form.Add(self.value_choice, 0, wx.EXPAND)

        main.Add(form, 0, wx.EXPAND | wx.ALL, 12)

        preview_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Vorschau (erste 8 Zeilen)")
        self.preview = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        style_list(self.preview)
        for index, name in enumerate(self.header):
            self.preview.InsertColumn(index, name, width=130)
        for row in self.data[:8]:
            item_index = self.preview.InsertItem(self.preview.GetItemCount(), row[0] if row else "")
            for col in range(1, len(self.header)):
                self.preview.SetItem(item_index, col, row[col] if col < len(row) else "")
            self.preview.SetItemTextColour(item_index, WHITE)
        preview_box.Add(self.preview, 1, wx.EXPAND | wx.ALL, 6)
        main.Add(preview_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        note = wx.StaticText(
            self,
            label="Gültige Zeilen werden übernommen; gleiche Datumswerte werden aktualisiert.",
        )
        main.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        buttons = wx.StdDialogButtonSizer()
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Abbrechen")
        import_btn = wx.Button(self, wx.ID_OK, "Importieren")
        buttons.AddButton(cancel_btn)
        buttons.AddButton(import_btn)
        buttons.Realize()
        import_btn.SetDefault()
        cancel_btn.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_CANCEL))
        import_btn.Bind(wx.EVT_BUTTON, self.on_import)
        main.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizer(main)
        self.Layout()

    @staticmethod
    def _selected_index(choice: wx.Choice) -> int:
        return int(choice.GetStringSelection().split(":", 1)[0])

    def on_import(self, event: wx.CommandEvent | None) -> None:
        medium = MEDIUM_BY_LABEL[self.medium_choice.GetStringSelection()]
        date_index = self._selected_index(self.date_choice)
        value_index = self._selected_index(self.value_choice)

        records: list[tuple[str, str, float]] = []
        errors: list[str] = []
        for line_no, row in enumerate(self.data, start=2):
            try:
                datum = parse_date(row[date_index]).isoformat()
                stand = parse_float(row[value_index])
            except (IndexError, ValueError) as exc:
                errors.append(f"Zeile {line_no}: {exc}")
                continue
            records.append((medium, datum, stand))

        self.records = records
        self.errors = errors
        self.EndModal(wx.ID_OK)


# ------------------------------------------------------------------ Hauptframe
class VerbrauchFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="Verbrauchserfassung – Strom / Wasser / Gas", size=(960, 660))
        self.con = connect()
        self.CreateStatusBar()
        self.SetStatusText("Bereit.")

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Eingabe
        input_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Zählerstand erfassen")
        grid = wx.GridBagSizer(7, 9)

        grid.Add(wx.StaticText(panel, label="Medium:"), (0, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.medium_choice = wx.Choice(panel, choices=list(MEDIUM_LABELS))
        self.medium_choice.SetStringSelection("Strom")
        self.medium_choice.Bind(wx.EVT_CHOICE, self.on_medium_changed)
        grid.Add(self.medium_choice, (0, 1), flag=wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Datum (JJJJ-MM-TT):"), (0, 2),
                 flag=wx.ALIGN_CENTER_VERTICAL)
        self.date_text = wx.TextCtrl(panel, value=date.today().isoformat(), size=(120, -1))
        grid.Add(self.date_text, (0, 3), flag=wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Zählerstand:"), (0, 4),
                 flag=wx.ALIGN_CENTER_VERTICAL)
        self.stand_text = wx.TextCtrl(panel, value="", size=(100, -1), style=wx.TE_PROCESS_ENTER)
        self.stand_text.Bind(wx.EVT_TEXT_ENTER, self.save_reading)
        grid.Add(self.stand_text, (0, 5), flag=wx.EXPAND)

        save_btn = wx.Button(panel, label="Speichern")
        save_btn.Bind(wx.EVT_BUTTON, self.save_reading)
        grid.Add(save_btn, (0, 6))

        self.price_label = wx.StaticText(panel, label="Preis (EUR/kWh):")
        grid.Add(self.price_label, (1, 2), flag=wx.ALIGN_CENTER_VERTICAL)
        self.price_text = wx.TextCtrl(panel, value=self._load_price_text("strom"),
                                      size=(90, -1), style=wx.TE_PROCESS_ENTER)
        self.price_text.Bind(wx.EVT_TEXT_ENTER, self.save_price)
        grid.Add(self.price_text, (1, 3), flag=wx.EXPAND)
        self.price_btn = wx.Button(panel, label="Preis übernehmen")
        self.price_btn.Bind(wx.EVT_BUTTON, self.save_price)
        grid.Add(self.price_btn, (1, 4), span=(1, 2), flag=wx.EXPAND)

        grid.AddGrowableCol(1)
        grid.AddGrowableCol(3)
        grid.AddGrowableCol(5)
        input_box.Add(grid, 0, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(input_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # Tabelle
        table_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Messungen")
        self.list = wx.ListCtrl(
            panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN
        )
        style_list(self.list)
        for index, (heading, width) in enumerate((
            ("Medium", 90), ("Datum", 115), ("Zählerstand", 135),
            ("Verbrauch", 125), ("Kosten (EUR)", 115),
        )):
            self.list.InsertColumn(index, heading, width=width)
        table_box.Add(self.list, 1, wx.EXPAND | wx.ALL, 6)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (
            ("Auswahl löschen", self.delete_selected),
            ("Aktualisieren", self.refresh),
            ("CSV importieren", self.import_csv),
            ("CSV exportieren", self.export_csv),
        ):
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            button_sizer.Add(button, 0, wx.RIGHT, 6)
        table_box.Add(button_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        main_sizer.Add(table_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # Diagramm + Zusammenfassung
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        self.chart = ChartPanel(panel)
        bottom.Add(self.chart, 1, wx.EXPAND | wx.ALL, 4)

        summary_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Zusammenfassung")
        summary_grid = wx.FlexGridSizer(cols=2, vgap=7, hgap=12)
        self.total_text = wx.StaticText(panel, label="–")
        self.cost_text = wx.StaticText(panel, label="–")
        self.avg_text = wx.StaticText(panel, label="–")
        for label, value in (
            ("Gesamtverbrauch:", self.total_text),
            ("Gesamtkosten:", self.cost_text),
            ("Ø pro Tag:", self.avg_text),
        ):
            summary_grid.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            summary_grid.Add(value, 0, wx.ALIGN_RIGHT)
        summary_box.Add(summary_grid, 0, wx.ALL, 10)
        bottom.Add(summary_box, 0, wx.EXPAND)
        main_sizer.Add(bottom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        panel.SetSizer(main_sizer)
        self._build_menu()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.refresh()
        self.Centre()

    def _build_menu(self) -> None:
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        import_item = file_menu.Append(wx.ID_OPEN, "CSV importieren ...")
        export_item = file_menu.Append(wx.ID_SAVEAS, "CSV exportieren ...")
        file_menu.AppendSeparator()
        quit_item = file_menu.Append(wx.ID_EXIT, "Beenden")
        menubar.Append(file_menu, "Datei")

        help_menu = wx.Menu()
        about_item = help_menu.Append(wx.ID_ABOUT, "Über")
        menubar.Append(help_menu, "Hilfe")
        self.SetMenuBar(menubar)

        self.Bind(wx.EVT_MENU, self.import_csv, import_item)
        self.Bind(wx.EVT_MENU, self.export_csv, export_item)
        self.Bind(wx.EVT_MENU, self.on_close, quit_item)
        self.Bind(wx.EVT_MENU, self.show_about, about_item)

    # ------------------------------------------------------------- Daten
    def current_medium(self) -> str:
        return MEDIUM_BY_LABEL[self.medium_choice.GetStringSelection()]

    def on_medium_changed(self, event: wx.CommandEvent | None = None) -> None:
        medium = self.current_medium()
        if medium == "alle":
            self.price_label.SetLabel("Preis: je Medium")
            self.price_text.SetValue("")
            self.price_text.Disable()
            self.price_btn.Disable()
            self.SetStatusText("Alle Medien in einem Diagramm (Reihen normiert).")
        else:
            self.price_label.SetLabel(f"Preis (EUR/{UNITS[medium]}):")
            self.price_text.Enable()
            self.price_btn.Enable()
            self.price_text.SetValue(self._load_price_text(medium))
            self.SetStatusText(f"Medium gewechselt: {LABEL_BY_MEDIUM[medium]}")
        self.refresh()
        if event:
            event.Skip()

    def _load_price_text(self, medium: str) -> str:
        row = self.con.execute("SELECT value FROM settings WHERE key=?", (f"preis_{medium}",)).fetchone()
        try:
            value = parse_float(row[0]) if row else DEFAULT_PRICES[medium]
        except (TypeError, ValueError):
            value = DEFAULT_PRICES[medium]
        return format_number(value)

    def save_price(self, event: wx.CommandEvent | None = None) -> None:
        medium = self.current_medium()
        if medium == "alle":
            return
        try:
            price = parse_float(self.price_text.GetValue())
            if price < 0:
                raise ValueError("Preis darf nicht negativ sein")
        except ValueError:
            wx.MessageDialog(self, "Bitte einen gültigen Preis eingeben.", "Eingabefehler",
                             wx.OK | wx.ICON_ERROR).ShowModal()
            return
        self.con.execute("UPDATE settings SET value=? WHERE key=?", (str(price), f"preis_{medium}"))
        self.con.commit()
        self.price_text.SetValue(format_number(price))
        self.SetStatusText(
            f"{LABEL_BY_MEDIUM[medium]}: {format_number(price)} EUR/{UNITS[medium]} gespeichert."
        )
        self.refresh()
        if event:
            event.Skip()

    def _get_price(self, medium: str) -> float:
        row = self.con.execute("SELECT value FROM settings WHERE key=?", (f"preis_{medium}",)).fetchone()
        try:
            return parse_float(row[0]) if row else DEFAULT_PRICES[medium]
        except (TypeError, ValueError):
            return DEFAULT_PRICES[medium]

    def load_rows(self, medium: str) -> list[dict]:
        price = self._get_price(medium)
        raw = self.con.execute(
            "SELECT datum, stand FROM readings WHERE medium=? ORDER BY datum", (medium,)
        ).fetchall()
        rows: list[dict] = []
        previous: float | None = None
        for datum, stand in raw:
            diff = None if previous is None else round(stand - previous, 3)
            rows.append({
                "datum": datum,
                "stand": stand,
                "verbrauch": diff,
                "kosten": None if diff is None else diff * price,
            })
            previous = stand
        return rows

    @staticmethod
    def _last_values(rows: list[dict]) -> list[tuple[str, float]]:
        return [
            (row["datum"], row["verbrauch"])
            for row in rows if row["verbrauch"] is not None
        ][-CHART_COUNT:]

    def save_reading(self, event: wx.CommandEvent | None = None) -> None:
        medium = self.current_medium()
        if medium == "alle":
            wx.MessageDialog(
                self, "Bitte zuerst ein konkretes Medium (Strom, Wasser oder Gas) wählen.",
                "Hinweis", wx.OK | wx.ICON_INFORMATION,
            ).ShowModal()
            return
        try:
            datum = parse_date(self.date_text.GetValue()).isoformat()
        except ValueError:
            wx.MessageDialog(
                self, "Datum bitte als JJJJ-MM-TT eingeben, z. B. 2026-11-17.",
                "Eingabefehler", wx.OK | wx.ICON_ERROR,
            ).ShowModal()
            return
        try:
            stand = parse_float(self.stand_text.GetValue())
        except ValueError:
            wx.MessageDialog(self, "Zählerstand bitte als Zahl eingeben.",
                             "Eingabefehler", wx.OK | wx.ICON_ERROR).ShowModal()
            return

        self.con.execute(
            """INSERT INTO readings(medium, datum, stand) VALUES(?, ?, ?)
               ON CONFLICT(medium, datum) DO UPDATE SET stand=excluded.stand""",
            (medium, datum, stand),
        )
        self.con.commit()
        self.date_text.SetValue(datum)
        self.stand_text.SetValue("")
        self.SetStatusText(f"{LABEL_BY_MEDIUM[medium]}: Zählerstand für {datum} gespeichert.")
        self.refresh()
        if event:
            event.Skip()

    def delete_selected(self, event: wx.CommandEvent | None = None) -> None:
        index = self.list.GetFirstSelected()
        if index == -1:
            wx.MessageDialog(self, "Bitte zuerst eine Zeile auswählen.", "Hinweis",
                             wx.OK | wx.ICON_INFORMATION).ShowModal()
            return
        medium_label = self.list.GetItemText(index, 0)
        datum = self.list.GetItemText(index, 1)
        medium = MEDIUM_BY_LABEL[medium_label]
        answer = wx.MessageDialog(
            self, f"{medium_label}-Messung vom {datum} wirklich löschen?", "Löschen",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        ).ShowModal()
        if answer != wx.ID_YES:
            return
        self.con.execute("DELETE FROM readings WHERE medium=? AND datum=?", (medium, datum))
        self.con.commit()
        self.SetStatusText(f"{medium_label}-Messung vom {datum} gelöscht.")
        self.refresh()
        if event:
            event.Skip()

    # ----------------------------------------------------------- Anzeige
    def _set_headers(self, unit: str | None) -> None:
        suffix = f" ({unit})" if unit else ""
        headings = (
            "Medium", "Datum", f"Zählerstand{suffix}", f"Verbrauch{suffix}", "Kosten (EUR)"
        )
        for column, text in enumerate(headings):
            col = self.list.GetColumn(column)
            col.SetText(text)
            self.list.SetColumn(column, col)

    def _fill_table(self, table_rows: list[tuple]) -> None:
        self.list.DeleteAllItems()
        for index, (medium_label, datum, stand, verbrauch, kosten, negative) in enumerate(table_rows):
            self.list.InsertItem(index, medium_label)
            self.list.SetItem(index, 1, datum)
            self.list.SetItem(index, 2, stand)
            self.list.SetItem(index, 3, verbrauch)
            self.list.SetItem(index, 4, kosten)
            self.list.SetItemTextColour(index, WHITE)
            self.list.SetItemBackgroundColour(index, DARK_RED if negative else BLACK)

    def refresh(self, event: wx.CommandEvent | None = None) -> None:
        medium = self.current_medium()
        if medium == "alle":
            self._refresh_all()
        else:
            self._refresh_single(medium)
        if event:
            event.Skip()

    def _refresh_single(self, medium: str) -> None:
        unit = UNITS[medium]
        label = LABEL_BY_MEDIUM[medium]
        self._set_headers(unit)
        rows = self.load_rows(medium)

        self._fill_table([
            (
                label,
                row["datum"],
                format_number(row["stand"]),
                "–" if row["verbrauch"] is None else format_number(row["verbrauch"]),
                "–" if row["kosten"] is None else format_number(row["kosten"]),
                row["verbrauch"] is not None and row["verbrauch"] < 0,
            )
            for row in rows
        ])

        diffs = [r["verbrauch"] for r in rows if r["verbrauch"] is not None]
        total = sum(diffs)
        cost = sum(r["kosten"] for r in rows if r["kosten"] is not None)
        days = (
            (date.fromisoformat(rows[-1]["datum"]) - date.fromisoformat(rows[0]["datum"])).days
            if len(rows) >= 2 else 0
        )
        avg = total / days if days > 0 else None

        self.total_text.SetLabel(f"{format_number(total)} {unit}")
        self.cost_text.SetLabel(f"{format_number(cost)} EUR")
        self.avg_text.SetLabel("–" if avg is None else f"{format_number(avg)} {unit}/Tag")

        self.chart.SetData(
            [{
                "label": label,
                "unit": unit,
                "color": SERIES_COLORS[medium],
                "values": self._last_values(rows),
            }],
            f"{label} – letzte {CHART_COUNT} Messungen",
        )

    def _refresh_all(self) -> None:
        self._set_headers(None)
        table_rows: list[tuple] = []
        series: list[dict] = []
        totals: dict[str, float] = {}
        total_cost = 0.0

        for medium in ("strom", "wasser", "gas"):
            rows = self.load_rows(medium)
            label = LABEL_BY_MEDIUM[medium]
            unit = UNITS[medium]
            diffs = [r["verbrauch"] for r in rows if r["verbrauch"] is not None]
            totals[medium] = sum(diffs)
            total_cost += sum(r["kosten"] for r in rows if r["kosten"] is not None)
            table_rows.extend([
                (
                    label,
                    row["datum"],
                    format_number(row["stand"]),
                    "–" if row["verbrauch"] is None else format_number(row["verbrauch"]),
                    "–" if row["kosten"] is None else format_number(row["kosten"]),
                    row["verbrauch"] is not None and row["verbrauch"] < 0,
                )
                for row in rows
            ])
            series.append({
                "label": label,
                "unit": unit,
                "color": SERIES_COLORS[medium],
                "values": self._last_values(rows),
            })

        table_rows.sort(key=lambda item: (item[1], DATA_MEDIUM_LABELS.index(item[0])))
        self._fill_table(table_rows)

        self.total_text.SetLabel("–")
        self.cost_text.SetLabel(f"{format_number(total_cost)} EUR")
        self.avg_text.SetLabel("–")
        self.SetStatusText(
            "Alle Medien: "
            + " | ".join(
                f"{LABEL_BY_MEDIUM[key]} {format_number(totals[key])} {UNITS[key]}"
                for key in ("strom", "wasser", "gas")
            )
        )
        self.chart.SetData(series, f"Alle Medien – normiert, letzte {CHART_COUNT} Messungen")

    # ------------------------------------------------------- Import/Export
    def import_csv(self, event: wx.CommandEvent | None = None) -> None:
        file_dialog = wx.FileDialog(
            self, "CSV-Datei auswählen",
            wildcard="CSV-Datei (*.csv;*.txt)|*.csv;*.txt|Alle Dateien (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if file_dialog.ShowModal() != wx.ID_OK:
            file_dialog.Destroy()
            return
        path = file_dialog.GetPath()
        file_dialog.Destroy()

        try:
            dialog = ImportDialog(self, path, self.current_medium())
        except OSError as exc:
            wx.MessageDialog(self, str(exc), "Import fehlgeschlagen",
                             wx.OK | wx.ICON_ERROR).ShowModal()
            return

        if not dialog.has_data:
            wx.MessageDialog(self, "Die Datei enthält keine Datenzeilen.", "CSV",
                             wx.OK | wx.ICON_WARNING).ShowModal()
            dialog.Destroy()
            return

        if dialog.ShowModal() == wx.ID_OK:
            self._apply_import(dialog)
        dialog.Destroy()
        if event:
            event.Skip()

    def _apply_import(self, dialog: ImportDialog) -> None:
        if not dialog.records:
            detail = "\n".join(dialog.errors[:8])
            wx.MessageDialog(
                self,
                "Keine gültigen Zeilen gefunden." + (f"\n\n{detail}" if detail else ""),
                "Import", wx.OK | wx.ICON_WARNING,
            ).ShowModal()
            return
        self.con.executemany(
            """INSERT INTO readings(medium, datum, stand) VALUES(?, ?, ?)
               ON CONFLICT(medium, datum) DO UPDATE SET stand=excluded.stand""",
            dialog.records,
        )
        self.con.commit()
        medium = dialog.records[0][0]
        self.medium_choice.SetStringSelection(LABEL_BY_MEDIUM[medium])
        self.on_medium_changed()
        skipped = len(dialog.errors)
        note = f" ({skipped} Zeilen übersprungen)" if skipped else ""
        self.SetStatusText(f"{len(dialog.records)} Messungen importiert{note}.")
        if skipped:
            wx.MessageDialog(
                self,
                f"{len(dialog.records)} Zeilen importiert, {skipped} übersprungen.\n\n"
                + "\n".join(dialog.errors[:8]),
                "Import abgeschlossen", wx.OK | wx.ICON_INFORMATION,
            ).ShowModal()

    def export_csv(self, event: wx.CommandEvent | None = None) -> None:
        medium = self.current_medium()
        if medium == "alle":
            wx.MessageDialog(
                self, "Bitte zuerst ein konkretes Medium zum Export wählen.",
                "Hinweis", wx.OK | wx.ICON_INFORMATION,
            ).ShowModal()
            return
        unit = UNITS[medium]
        file_dialog = wx.FileDialog(
            self, "CSV speichern",
            defaultFile=f"verbrauch_{medium}_{date.today().isoformat()}.csv",
            wildcard="CSV-Datei (*.csv)|*.csv|Alle Dateien (*.*)|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if file_dialog.ShowModal() != wx.ID_OK:
            file_dialog.Destroy()
            return
        path = file_dialog.GetPath()
        file_dialog.Destroy()
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh, delimiter=";")
                writer.writerow([
                    "Medium", "Datum", f"Zählerstand ({unit})",
                    f"Verbrauch ({unit})", "Kosten (EUR)",
                ])
                for row in self.load_rows(medium):
                    writer.writerow([
                        LABEL_BY_MEDIUM[medium],
                        row["datum"],
                        format_number(row["stand"]),
                        "" if row["verbrauch"] is None else format_number(row["verbrauch"]),
                        "" if row["kosten"] is None else format_number(row["kosten"]),
                    ])
        except OSError as exc:
            wx.MessageDialog(self, str(exc), "Export fehlgeschlagen",
                             wx.OK | wx.ICON_ERROR).ShowModal()
            return
        self.SetStatusText(f"CSV exportiert: {path}")
        if event:
            event.Skip()

    def show_about(self, event: wx.CommandEvent | None = None) -> None:
        wx.MessageDialog(
            self,
            "Verbrauchserfassung für Strom, Wasser und Gas.\n\n"
            f"Speicherung: SQLite ({DB_PATH.name})\n"
            f"Liniendiagramm: letzte {CHART_COUNT} Messungen\n"
            "Option 'Alle': alle Medien in einem Diagramm (normiert).\n"
            "CSV-Import mit Spaltenzuordnung, CSV-Export mit Semikolon.\n"
            "Benötigt wxPython.",
            "Über", wx.OK | wx.ICON_INFORMATION,
        ).ShowModal()
        if event:
            event.Skip()

    def on_close(self, event: wx.CloseEvent | None = None) -> None:
        self.con.close()
        self.Destroy()


if __name__ == "__main__":
    app = wx.App(False)
    frame = VerbrauchFrame()
    frame.Show()
    app.MainLoop()
