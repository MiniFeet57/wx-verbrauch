#!/usr/bin/env python3
"""Verbrauchserfassung für Strom, Wasser und Gas mit PySide6 und SQLite.

Speichert Zählerstände pro Medium und Datum, berechnet Verbrauch und Kosten,
zeigt Tabelle + Liniendiagramm (letzte 36 Messungen) und kann CSV importieren
sowie exportieren. Option "Alle" zeigt alle Medien in einem Diagramm.

Start: python3 verbrauch_pyside6.py
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

DB_PATH = Path(__file__).with_name("verbrauch.db")

DATA_MEDIUM_LABELS = ("Strom", "Wasser", "Gas")
MEDIUM_LABELS = DATA_MEDIUM_LABELS + ("Alle",)
MEDIUM_BY_LABEL = {"Strom": "strom", "Wasser": "wasser", "Gas": "gas", "Alle": "alle"}
LABEL_BY_MEDIUM = {v: k for k, v in MEDIUM_BY_LABEL.items()}

DEFAULT_PRICES = {"strom": 0.30, "wasser": 2.10, "gas": 1.20}
UNITS = {"strom": "kWh", "wasser": "m³", "gas": "m³"}
SERIES_COLORS = {
    "strom": QColor(63, 143, 208),    # Blau
    "wasser": QColor(46, 160, 90),    # Grün
    "gas": QColor(220, 140, 40),      # Orange
}

CHART_COUNT = 36
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y")

# Farben für das dunkle Qt-Design
TEXT = QColor("#e2e8f0")
MUTED = QColor("#94a3b8")
GRID = QColor("#2b3c58")
AXIS = QColor("#64748b")
DARK_RED = QColor("#6b1f22")
CHART_BG = QColor("#0e192c")

STYLE = """
QWidget { color: #e2e8f0; font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #0b1220; }

QMenuBar { background: #0f172a; border-bottom: 1px solid #1e293b; padding: 4px 8px; }
QMenuBar::item { padding: 6px 12px; border-radius: 6px; }
QMenuBar::item:selected { background: #1e293b; color: #38bdf8; }
QMenu { background: #16233a; border: 1px solid #2b3c58; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 7px 24px; border-radius: 5px; }
QMenu::item:selected { background: #2563eb; }

QGroupBox {
    background: #131f35;
    border: 1px solid #23324c;
    border-radius: 12px;
    margin-top: 10px;
    padding: 12px 14px 14px 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 4px;
    color: #93a4bd;
    font-size: 11px;
    font-weight: 700;
}

QLabel#statValue { color: #f1f5f9; font-size: 16px; font-weight: 700; }
QLabel#hint { color: #7f93b0; font-size: 11px; }

QLineEdit, QComboBox {
    background: #0d1628;
    border: 1px solid #31425f;
    border-radius: 8px;
    padding: 7px 10px;
    min-height: 20px;
    selection-background-color: #2563eb;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border-left: 1px solid #31425f;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
    background: #16233a;
}
QComboBox::down-arrow {
    image: none;
    width: 0px;
    height: 0px;
    border-left: 6px solid transparent;
    border-right: 6px solid transparent;
    border-top: 7px solid #a9bbd4;
    margin-right: 7px;
}
QComboBox QAbstractItemView {
    background: #16233a; border: 1px solid #31425f; border-radius: 8px;
    selection-background-color: #2563eb; padding: 4px;
}
QLineEdit:disabled, QComboBox:disabled { color: #52627b; }

QPushButton {
    background: #1c2b45; color: #dbe6f5;
    border: 1px solid #31425f; border-radius: 8px;
    padding: 8px 14px; font-weight: 600;
}
QPushButton:hover { background: #263a5d; border-color: #4a628c; }
QPushButton:pressed { background: #1a2b4c; }
QPushButton:disabled { color: #52627b; background: #16223a; border-color: #24324b; }
QPushButton#primary { background: #2563eb; border-color: #2563eb; color: white; }
QPushButton#primary:hover { background: #3b82f6; border-color: #3b82f6; }
QPushButton#primary:disabled { background: #16223a; border-color: #24324b; color: #52627b; }
QPushButton#danger:hover { background: #7f1d1d; border-color: #b91c1c; }

QTableWidget {
    background: #0e192c;
    alternate-background-color: #13223a;
    gridline-color: #1e2d47;
    border: 1px solid #23324c;
    border-radius: 10px;
    selection-background-color: #1d4ed8;
    selection-color: #ffffff;
    padding: 2px;
}
QHeaderView::section {
    background: #1a2942; color: #a9bbd4;
    border: 0; border-right: 1px solid #263752;
    padding: 8px 10px; font-weight: 700; text-align: left;
}
QTableWidget::item { padding: 5px 10px; border: 0; }

QStatusBar { background: #0f172a; color: #90a1b9; border-top: 1px solid #1e293b; }
QStatusBar QLabel { padding: 6px 10px; }
QMessageBox { background: #0b1220; }
QScrollBar:vertical { background: #0e192c; width: 10px; margin: 2px; border-radius: 5px; }
QScrollBar::handle:vertical { background: #334760; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

# ----------------------------------------------------------------- Helfer


def connect() -> sqlite3.Connection:

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


# ------------------------------------------------------------- Diagramm-Panel
class ChartPanel(QWidget):
    """Liniendiagramm für eine oder mehrere Reihen.

    Jede Series: {"label", "unit", "color", "values": [(datum, wert), ...]}
    Da kWh und m³ unterschiedliche Einheiten haben, wird jede Reihe normalisiert
    und ihr Wertebereich in der Legende angezeigt.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chart")
        self.setStyleSheet("background: #0e192c; border: 1px solid #23324c; border-radius: 10px;")
        self.setMinimumSize(380, 190)
        self.series: list[dict] = []
        self.title = ""

    def SetData(self, series: list[dict], title: str = "") -> None:
        self.series = series
        self.title = title
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), CHART_BG)
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return

        painter.setFont(QFont("Segoe UI", 8))
        metrics = painter.fontMetrics()

        left, right = 58, 14
        top, bottom = 40, 30
        base = height - bottom
        if base <= top + 10:
            return

        all_dates = sorted({d for s in self.series for d, _ in s["values"]})[-CHART_COUNT:]
        if not all_dates:
            painter.setPen(MUTED)
            message = "Noch keine Verbrauchswerte vorhanden."
            painter.drawText(
                int(max(4, (width - metrics.horizontalAdvance(message)) / 2)),
                height // 2 - 8,
                message,
            )
            return

        slot = (width - left - right) / len(all_dates)
        x_by_date = {d: left + slot * (i + 0.5) for i, d in enumerate(all_dates)}

        prepared: list[dict] = []
        for series in self.series:
            points = [(x_by_date[d], value) for d, value in series["values"] if d in x_by_date]
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
            painter.setPen(MUTED)
            message = "Keine gültigen Messwerte vorhanden."
            painter.drawText(
                int(max(4, (width - metrics.horizontalAdvance(message)) / 2)),
                height // 2 - 8,
                message,
            )
            return

        # Titel
        painter.setPen(TEXT)
        painter.drawText(6, 15, self.title or "Verlauf")

        # Legende mit Wertebereich pro Reihe
        legend_x = left
        legend_y = 26
        for item in prepared:
            text = (
                f"{item['label']} {format_number(item['low'], 1)}"
                f"–{format_number(item['high'], 1)} {item['unit']}"
            )
            text_width = metrics.horizontalAdvance(text)
            if legend_x + 18 + text_width > width - right:
                legend_x = left
                legend_y += 12
            painter.setPen(QPen(item["color"], 2))
            painter.drawLine(QLineF(legend_x, legend_y, legend_x + 14, legend_y))
            painter.setPen(item["color"])
            painter.drawText(legend_x + 18, legend_y + 4, text)
            legend_x += 18 + text_width + 14

        # Gitternetz
        painter.setPen(QPen(GRID))
        for fraction in (0.0, 0.5, 1.0):
            y = base - fraction * (base - top)
            painter.drawLine(QLineF(left, y, width - right, y))

        # Achsen
        painter.setPen(QPen(AXIS))
        painter.drawLine(QLineF(left, base, width - right, base))
        painter.drawLine(QLineF(left, top, left, base))

        # Y-Beschriftung bei einer einzelnen Reihe
        if len(prepared) == 1:
            item = prepared[0]
            painter.setPen(MUTED)
            painter.drawText(4, top - 5, format_number(item["high"], 1))
            painter.drawText(4, base - 4, format_number(item["low"], 1))

        # Linien zeichnen
        for item in prepared:
            color = item["color"]
            points = [
                (x, base - (value - item["low"]) / item["span"] * (base - top))
                for x, value in item["points"]
            ]
            painter.setPen(QPen(color, 2))
            for index in range(1, len(points)):
                x1, y1 = points[index - 1]
                x2, y2 = points[index]
                painter.drawLine(QLineF(x1, y1, x2, y2))
            painter.setBrush(QBrush(color))
            for x, y in points:
                painter.drawEllipse(QPointF(x, y), 3, 3)

        # X-Beschriftung
        step = max(1, len(all_dates) // 6)
        for index, datum in enumerate(all_dates):
            if index == 0 or index == len(all_dates) - 1 or index % step == 0:
                label = datum[5:]
                painter.setPen(MUTED)
                painter.drawText(
                    int(x_by_date[datum] - metrics.horizontalAdvance(label) / 2),
                    int(base + 16),
                    label,
                )


# --------------------------------------------------------------- Importdialog
class ImportDialog(QDialog):
    def __init__(self, parent: QWidget, path: str, default_medium: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("CSV importieren")
        self.resize(780, 520)
        self.rows = read_csv_rows(path)
        self.records: list[tuple[str, str, float]] | None = None
        self.errors: list[str] = []

        header_words = (
            "datum", "date", "stand", "zähler", "zaehler", "wert",
            "value", "verbrauch", "kwh", "messung", "medium",
        )
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
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("Die Datei enthält keine Datenzeilen."))
            close_button = QPushButton("Schließen")
            close_button.clicked.connect(self.reject)
            layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
            return

        self._build(default_medium)

    def _build(self, default_medium: str) -> None:
        root = QVBoxLayout(self)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 1)

        self.medium_choice = QComboBox()
        self.medium_choice.addItems(list(DATA_MEDIUM_LABELS))
        self.medium_choice.setCurrentText(
            LABEL_BY_MEDIUM[default_medium] if default_medium != "alle" else "Strom"
        )
        form.addWidget(QLabel("Medium:"), 0, 0)
        form.addWidget(self.medium_choice, 0, 1)

        choices = [f"{i}: {name}" for i, name in enumerate(self.header)]
        self.date_choice = QComboBox()
        self.date_choice.addItems(choices)
        self.date_choice.setCurrentIndex(0)
        form.addWidget(QLabel("Datum-Spalte:"), 1, 0)
        form.addWidget(self.date_choice, 1, 1)

        self.value_choice = QComboBox()
        self.value_choice.addItems(choices)
        self.value_choice.setCurrentIndex(1 if len(choices) > 1 else 0)
        form.addWidget(QLabel("Zählerstand-Spalte:"), 2, 0)
        form.addWidget(self.value_choice, 2, 1)
        root.addLayout(form)

        root.addWidget(QLabel("Vorschau (erste 8 Zeilen):"))
        self.preview = QTableWidget()
        self.preview.setColumnCount(len(self.header))
        self.preview.setHorizontalHeaderLabels(self.header)
        self.preview.setRowCount(len(self.data[:8]))
        for row_index, row in enumerate(self.data[:8]):
            for column in range(len(self.header)):
                value = row[column] if column < len(row) else ""
                item = QTableWidgetItem(value)
                item.setForeground(TEXT)
                self.preview.setItem(row_index, column, item)
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.preview.verticalHeader().setVisible(False)
        self.preview.setStyleSheet(
            "QTableWidget { background: #0b1220; alternate-background-color: #111c30; "
            "gridline-color: #23324c; border: 1px solid #23324c; border-radius: 8px; }"
        )
        root.addWidget(self.preview, 1)

        hint = QLabel("Gültige Zeilen werden übernommen; gleiche Datumswerte werden aktualisiert.")
        hint.setObjectName("hint")
        root.addWidget(hint)

        buttons = QDialogButtonBox()
        cancel_button = buttons.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        import_button = buttons.addButton("Importieren", QDialogButtonBox.ButtonRole.AcceptRole)
        import_button.setObjectName("primary")
        cancel_button.clicked.connect(self.reject)
        import_button.clicked.connect(self.on_import)
        root.addWidget(buttons)

    @staticmethod
    def _selected_index(choice: QComboBox) -> int:
        return int(choice.currentText().split(":", 1)[0])

    def on_import(self) -> None:
        medium = MEDIUM_BY_LABEL[self.medium_choice.currentText()]
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
        self.accept()


# ------------------------------------------------------------------ Hauptframe
class VerbrauchFrame(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Verbrauchserfassung – Strom / Wasser / Gas")
        self.resize(1020, 700)
        self.con = connect()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Bereit.")

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(14, 12, 14, 8)
        main_layout.setSpacing(10)

        # Eingabe
        input_box = QGroupBox("Zählerstand erfassen")
        grid = QGridLayout(input_box)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(5, 1)

        grid.addWidget(QLabel("Medium:"), 0, 0)
        self.medium_combo = QComboBox()
        self.medium_combo.addItems(list(MEDIUM_LABELS))
        self.medium_combo.setCurrentText("Strom")
        grid.addWidget(self.medium_combo, 0, 1)

        grid.addWidget(QLabel("Datum (JJJJ-MM-TT):"), 0, 2)
        self.date_text = QLineEdit(date.today().isoformat())
        self.date_text.setFixedWidth(130)
        grid.addWidget(self.date_text, 0, 3)

        grid.addWidget(QLabel("Zählerstand:"), 0, 4)
        self.stand_text = QLineEdit()
        self.stand_text.setFixedWidth(120)
        self.stand_text.returnPressed.connect(self.save_reading)
        grid.addWidget(self.stand_text, 0, 5)

        self.save_button = QPushButton("Speichern")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save_reading)
        grid.addWidget(self.save_button, 0, 6)

        self.price_label = QLabel("Preis (EUR/kWh):")
        grid.addWidget(self.price_label, 1, 2)
        self.price_text = QLineEdit(self._load_price_text("strom"))
        self.price_text.setFixedWidth(110)
        self.price_text.returnPressed.connect(self.save_price)
        grid.addWidget(self.price_text, 1, 3)
        self.price_button = QPushButton("Preis übernehmen")
        self.price_button.clicked.connect(self.save_price)
        grid.addWidget(self.price_button, 1, 4, 1, 2)
        main_layout.addWidget(input_box)

        # Tabelle
        table_box = QGroupBox("Messungen")
        table_layout = QVBoxLayout(table_box)
        self.list = QTableWidget()
        self.list.setColumnCount(5)
        self.list.setHorizontalHeaderLabels([
            "Medium", "Datum", "Zählerstand", "Verbrauch", "Kosten (EUR)"
        ])
        self.list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.list.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.list.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.list.setAlternatingRowColors(True)
        self.list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.list.verticalHeader().setVisible(False)
        table_layout.addWidget(self.list)

        button_layout = QHBoxLayout()
        for label, handler, object_name in (
            ("Auswahl löschen", self.delete_selected, "danger"),
            ("Aktualisieren", self.refresh, ""),
            ("CSV importieren", self.import_csv, ""),
            ("CSV exportieren", self.export_csv, ""),
        ):
            button = QPushButton(label)
            if object_name:
                button.setObjectName(object_name)
            button.clicked.connect(handler)
            button_layout.addWidget(button)
        button_layout.addStretch()
        table_layout.addLayout(button_layout)
        main_layout.addWidget(table_box, 1)

        # Diagramm + Zusammenfassung
        bottom = QHBoxLayout()
        self.chart = ChartPanel()
        bottom.addWidget(self.chart, 3)

        summary_box = QGroupBox("Zusammenfassung")
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setSpacing(10)
        self.stats: dict[str, QLabel] = {}
        for key in ("Gesamtverbrauch:", "Gesamtkosten:", "Ø pro Tag:"):
            caption = QLabel(key)
            caption.setObjectName("hint")
            value = QLabel("–")
            value.setObjectName("statValue")
            summary_layout.addWidget(caption)
            summary_layout.addWidget(value)
            self.stats[key] = value
        summary_layout.addStretch()
        bottom.addWidget(summary_box, 1)
        main_layout.addLayout(bottom)

        self._build_menu()
        self.medium_combo.currentTextChanged.connect(self.on_medium_changed)
        self.on_medium_changed()

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("Datei")
        file_menu.addAction("CSV importieren ...", self.import_csv)
        file_menu.addAction("CSV exportieren ...", self.export_csv)
        file_menu.addSeparator()
        file_menu.addAction("Beenden", self.close)
        help_menu = menubar.addMenu("Hilfe")
        help_menu.addAction("Über", self.show_about)

    # ------------------------------------------------------------- Daten
    def current_medium(self) -> str:
        return MEDIUM_BY_LABEL[self.medium_combo.currentText()]

    def on_medium_changed(self, text: str = "") -> None:
        medium = self.current_medium()
        if medium == "alle":
            self.price_label.setText("Preis: je Medium")
            self.price_text.setText("")
            self.price_text.setDisabled(True)
            self.price_button.setDisabled(True)
            self.save_button.setDisabled(True)
            self.statusBar().showMessage("Alle Medien in einem Diagramm (Reihen normiert).")
        else:
            self.price_label.setText(f"Preis (EUR/{UNITS[medium]}):")
            self.price_text.setDisabled(False)
            self.price_button.setDisabled(False)
            self.save_button.setDisabled(False)
            self.price_text.setText(self._load_price_text(medium))
            self.statusBar().showMessage(f"Medium gewechselt: {LABEL_BY_MEDIUM[medium]}")
        self.refresh()

    def _load_price_text(self, medium: str) -> str:
        row = self.con.execute(
            "SELECT value FROM settings WHERE key=?", (f"preis_{medium}",)
        ).fetchone()
        try:
            value = parse_float(row[0]) if row else DEFAULT_PRICES[medium]
        except (TypeError, ValueError):
            value = DEFAULT_PRICES[medium]
        return format_number(value)

    def save_price(self) -> None:
        medium = self.current_medium()
        if medium == "alle":
            return
        try:
            price = parse_float(self.price_text.text())
            if price < 0:
                raise ValueError("Preis darf nicht negativ sein")
        except ValueError:
            QMessageBox.critical(self, "Eingabefehler", "Bitte einen gültigen Preis eingeben.")
            return
        self.con.execute(
            "UPDATE settings SET value=? WHERE key=?", (str(price), f"preis_{medium}")
        )
        self.con.commit()
        self.price_text.setText(format_number(price))
        self.statusBar().showMessage(
            f"{LABEL_BY_MEDIUM[medium]}: {format_number(price)} EUR/{UNITS[medium]} gespeichert."
        )
        self.refresh()

    def _get_price(self, medium: str) -> float:
        row = self.con.execute(
            "SELECT value FROM settings WHERE key=?", (f"preis_{medium}",)
        ).fetchone()
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

    def save_reading(self) -> None:
        medium = self.current_medium()
        if medium == "alle":
            QMessageBox.information(
                self, "Hinweis",
                "Bitte zuerst ein konkretes Medium (Strom, Wasser oder Gas) wählen.",
            )
            return
        try:
            datum = parse_date(self.date_text.text()).isoformat()
        except ValueError:
            QMessageBox.critical(
                self, "Eingabefehler",
                "Datum bitte als JJJJ-MM-TT eingeben, z. B. 2026-11-17.",
            )
            return
        try:
            stand = parse_float(self.stand_text.text())
        except ValueError:
            QMessageBox.critical(self, "Eingabefehler", "Zählerstand bitte als Zahl eingeben.")
            return

        self.con.execute(
            """INSERT INTO readings(medium, datum, stand) VALUES(?, ?, ?)
               ON CONFLICT(medium, datum) DO UPDATE SET stand=excluded.stand""",
            (medium, datum, stand),
        )
        self.con.commit()
        self.date_text.setText(datum)
        self.stand_text.setText("")
        self.statusBar().showMessage(
            f"{LABEL_BY_MEDIUM[medium]}: Zählerstand für {datum} gespeichert."
        )
        self.refresh()

    def delete_selected(self) -> None:
        selected = self.list.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "Hinweis", "Bitte zuerst eine Zeile auswählen.")
            return
        index = selected[0].row()
        medium_label = self.list.item(index, 0).text()
        datum = self.list.item(index, 1).text()
        medium = MEDIUM_BY_LABEL[medium_label]
        answer = QMessageBox.question(
            self, "Löschen",
            f"{medium_label}-Messung vom {datum} wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.con.execute("DELETE FROM readings WHERE medium=? AND datum=?", (medium, datum))
        self.con.commit()
        self.statusBar().showMessage(f"{medium_label}-Messung vom {datum} gelöscht.")
        self.refresh()

    # ----------------------------------------------------------- Anzeige
    def _set_headers(self, unit: str | None) -> None:
        suffix = f" ({unit})" if unit else ""
        self.list.setHorizontalHeaderLabels([
            "Medium", "Datum", f"Zählerstand{suffix}", f"Verbrauch{suffix}", "Kosten (EUR)"
        ])

    def _fill_table(self, table_rows: list[tuple]) -> None:
        self.list.setRowCount(0)
        for index, (medium_label, datum, stand, verbrauch, kosten, negative) in enumerate(table_rows):
            self.list.insertRow(index)
            for column, value in enumerate((medium_label, datum, stand, verbrauch, kosten)):
                item = QTableWidgetItem(value)
                item.setForeground(TEXT)
                if negative:
                    item.setBackground(DARK_RED)
                self.list.setItem(index, column, item)
        self.list.resizeRowsToContents()

    def refresh(self) -> None:
        medium = self.current_medium()
        if medium == "alle":
            self._refresh_all()
        else:
            self._refresh_single(medium)

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

        self.stats["Gesamtverbrauch:"].setText(f"{format_number(total)} {unit}")
        self.stats["Gesamtkosten:"].setText(f"{format_number(cost)} EUR")
        self.stats["Ø pro Tag:"].setText(
            "–" if avg is None else f"{format_number(avg)} {unit}/Tag"
        )

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

        self.stats["Gesamtverbrauch:"].setText("–")
        self.stats["Gesamtkosten:"].setText(f"{format_number(total_cost)} EUR")
        self.stats["Ø pro Tag:"].setText("–")
        self.statusBar().showMessage(
            "Alle Medien: "
            + " | ".join(
                f"{LABEL_BY_MEDIUM[key]} {format_number(totals[key])} {UNITS[key]}"
                for key in ("strom", "wasser", "gas")
            )
        )
        self.chart.SetData(series, f"Alle Medien – normiert, letzte {CHART_COUNT} Messungen")

    # ------------------------------------------------------- Import/Export
    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "CSV-Datei auswählen",
            "",
            "CSV-Datei (*.csv *.txt);;Alle Dateien (*)",
        )
        if not path:
            return

        try:
            dialog = ImportDialog(self, path, self.current_medium())
        except OSError as exc:
            QMessageBox.critical(self, "Import fehlgeschlagen", str(exc))
            return

        if not dialog.has_data:
            QMessageBox.warning(self, "CSV", "Die Datei enthält keine Datenzeilen.")
            return

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_import(dialog)

    def _apply_import(self, dialog: ImportDialog) -> None:
        if not dialog.records:
            detail = "\n".join(dialog.errors[:8])
            QMessageBox.warning(
                self,
                "Import",
                "Keine gültigen Zeilen gefunden." + (f"\n\n{detail}" if detail else ""),
            )
            return
        self.con.executemany(
            """INSERT INTO readings(medium, datum, stand) VALUES(?, ?, ?)
               ON CONFLICT(medium, datum) DO UPDATE SET stand=excluded.stand""",
            dialog.records,
        )
        self.con.commit()
        medium = dialog.records[0][0]
        self.medium_combo.setCurrentText(LABEL_BY_MEDIUM[medium])
        self.on_medium_changed()
        skipped = len(dialog.errors)
        note = f" ({skipped} Zeilen übersprungen)" if skipped else ""
        self.statusBar().showMessage(f"{len(dialog.records)} Messungen importiert{note}.")
        if skipped:
            QMessageBox.information(
                self,
                "Import abgeschlossen",
                f"{len(dialog.records)} Zeilen importiert, {skipped} übersprungen.\n\n"
                + "\n".join(dialog.errors[:8]),
            )

    def export_csv(self) -> None:
        medium = self.current_medium()
        if medium == "alle":
            QMessageBox.information(
                self, "Hinweis", "Bitte zuerst ein konkretes Medium zum Export wählen."
            )
            return
        unit = UNITS[medium]
        path, _ = QFileDialog.getSaveFileName(
            self,
            "CSV speichern",
            f"verbrauch_{medium}_{date.today().isoformat()}.csv",
            "CSV-Datei (*.csv);;Alle Dateien (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
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
            QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))
            return
        self.statusBar().showMessage(f"CSV exportiert: {path}")

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "Über",
            "Verbrauchserfassung für Strom, Wasser und Gas.\n\n"
            f"Speicherung: SQLite ({DB_PATH.name})\n"
            f"Liniendiagramm: letzte {CHART_COUNT} Messungen\n"
            "Option 'Alle': alle Medien in einem Diagramm (normiert).\n"
            "CSV-Import mit Spaltenzuordnung, CSV-Export mit Semikolon.\n"
            "Benötigt PySide6.",
        )

    def closeEvent(self, event) -> None:
        self.con.close()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = VerbrauchFrame()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
