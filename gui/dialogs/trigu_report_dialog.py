from collections import defaultdict
from datetime import datetime
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)


LEGACY_PERIOD_ID = "__legacy__"
ALL_PERIOD_ID = "__all__"
REPORT_TYPES = ("Основные", "Сорт", "Доп")


def _to_datetime(value):
    try:
        return datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def build_report_periods(search_runs: list[str], work_finishes: list[str]) -> list[dict]:
    """Build one automatic maximum-gap split per manually bounded day chunk."""
    runs_by_day = defaultdict(list)
    for value in search_runs:
        timestamp = _to_datetime(value)
        if timestamp:
            runs_by_day[timestamp.date()].append(timestamp)
    finishes_by_day = defaultdict(list)
    for value in work_finishes:
        timestamp = _to_datetime(value)
        if timestamp:
            finishes_by_day[timestamp.date()].append(timestamp)

    periods = []
    for day, runs in runs_by_day.items():
        runs = sorted(set(runs))
        finishes = sorted(set(finishes_by_day[day]))
        chunks, remaining = [], runs
        for finish in finishes:
            before = [run for run in remaining if run <= finish]
            if before:
                chunks.append((before, finish))
                remaining = [run for run in remaining if run > finish]
        if remaining:
            chunks.append((remaining, None))

        for runs_chunk, explicit_end in chunks:
            split_index = None
            if len(runs_chunk) > 1:
                gaps = [
                    (runs_chunk[index + 1] - runs_chunk[index], index)
                    for index in range(len(runs_chunk) - 1)
                ]
                split_index = max(gaps, key=lambda pair: pair[0])[1]
            groups = (
                [runs_chunk[:split_index + 1], runs_chunk[split_index + 1:]]
                if split_index is not None else [runs_chunk]
            )
            for group_index, group in enumerate(groups):
                start = group[0]
                end = explicit_end if (
                    group_index == len(groups) - 1 and explicit_end
                ) else group[-1]
                periods.append({
                    "id": f"{start.isoformat()}|{end.isoformat()}",
                    "start": start,
                    "end": end,
                    "label": f"{start:%d.%m} · {start:%H:%M}–{end:%H:%M}",
                })
    return sorted(periods, key=lambda period: period["start"], reverse=True)


def period_for_report(report: dict, periods: list[dict]) -> str | None:
    timestamp = _to_datetime(report.get("search_started_at"))
    if not timestamp:
        return LEGACY_PERIOD_ID
    for period in periods:
        if period["start"] <= timestamp <= period["end"]:
            return period["id"]
    return LEGACY_PERIOD_ID


def supply_report_type(report: dict) -> str:
    """Classify special supplies from API status, then from their name."""
    status = str(report.get("supply_status", "") or "").strip().casefold()
    if status == "sorting":
        return "Сорт"
    if status == "dogruzka":
        return "Доп"
    name = str(report.get("supply_name", "") or "").casefold()
    if re.search(r"(?<!\w)сорт(?:ировка)?(?!\w)", name):
        return "Сорт"
    if re.search(r"(?<!\w)(?:доп|допы|догрузка)(?!\w)", name):
        return "Доп"
    return "Основные"


def filter_supply_reports(
    reports: dict[str, dict], periods: list[dict], *, period_id: str,
    category: str = "", shop: str = "", marketplace: str = "",
) -> dict[str, dict]:
    result = {}
    for number, report in reports.items():
        if period_id != ALL_PERIOD_ID and period_for_report(report, periods) != period_id:
            continue
        if shop and report.get("shop") != shop:
            continue
        if marketplace and report.get("marketplace") != marketplace:
            continue
        categories = report.get("categories", {})
        if category:
            if category not in categories:
                continue
            categories = {category: categories[category]}
        result[number] = {**report, "categories": categories}
    return result


def aggregate_supply_reports(reports: dict[str, dict]) -> tuple[dict, dict]:
    by_store = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    totals = defaultdict(lambda: [0, 0])
    for report in reports.values():
        shop = str(report.get("shop", "") or "").strip() or "UNKNOWN"
        marketplace = str(report.get("marketplace", "") or "").strip().upper() or "UNKNOWN"
        categories = report.get("categories", {})
        if not isinstance(categories, dict):
            continue
        for category, quantity in categories.items():
            name = str(category or "").strip() or "Без категории"
            try:
                amount = max(0, int(quantity))
            except (TypeError, ValueError):
                continue
            by_store[(shop, marketplace)][name][0] += 1
            by_store[(shop, marketplace)][name][1] += amount
            totals[name][0] += 1
            totals[name][1] += amount
    return dict(by_store), dict(totals)


def _format_categories(categories: dict) -> list[str]:
    return [
        f"  {category} — {values[0]} поставок, {values[1]} штук"
        for category, values in sorted(categories.items(), key=lambda item: item[0].casefold())
    ]


def format_supply_report(reports: dict[str, dict], unavailable_count: int = 0) -> str:
    reports_by_type = {
        report_type: {
            number: report for number, report in reports.items()
            if supply_report_type(report) == report_type
        }
        for report_type in REPORT_TYPES
    }
    if not any(reports_by_type.values()):
        lines = ["Нет поставок для выбранных фильтров."]
    else:
        lines = []
        stores = sorted({
            store for typed_reports in reports_by_type.values()
            for store in aggregate_supply_reports(typed_reports)[0]
        }, key=lambda item: (item[0].casefold(), item[1]))
        aggregates = {
            report_type: aggregate_supply_reports(typed_reports)
            for report_type, typed_reports in reports_by_type.items()
        }
        for shop, marketplace in stores:
            lines.append(f"Магазин: {shop} — {marketplace}")
            for report_type in REPORT_TYPES:
                categories = aggregates[report_type][0].get((shop, marketplace))
                if not categories:
                    continue
                lines.append(report_type)
                lines.extend(_format_categories(categories))
            lines.append("")
        lines.append("Всего по всем магазинам")
        for report_type in REPORT_TYPES:
            totals = aggregates[report_type][1]
            if not totals:
                continue
            lines.append(report_type)
            lines.extend(_format_categories(totals))
    if unavailable_count:
        lines.extend([
            "",
            f"Не удалось восстановить данные для {unavailable_count} старых поставок. "
            "Повторите открытие отчёта позже.",
        ])
    return "\n".join(lines)


class TriguReportDialog(QDialog):
    work_finished = Signal()
    history_cleared = Signal()

    def __init__(
        self, reports: dict[str, dict], search_runs: list[str],
        work_finishes: list[str], unavailable_count: int = 0, parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Отчёт по поставкам")
        self.resize(820, 600)
        self.reports = reports
        self.search_runs = search_runs
        self.work_finishes = work_finishes
        self.unavailable_count = unavailable_count

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.period_filter = QComboBox(self)
        self.category_filter = QComboBox(self)
        self.shop_filter = QComboBox(self)
        self.marketplace_filter = QComboBox(self)
        for label, widget in (
            ("Период", self.period_filter), ("Категория", self.category_filter),
            ("Магазин", self.shop_filter), ("Площадка", self.marketplace_filter),
        ):
            widget.setMinimumWidth(145)
            widget.setToolTip(label)
            filters.addWidget(widget)
        layout.addLayout(filters)

        self.report_text = QTextEdit(self)
        self.report_text.setReadOnly(True)
        layout.addWidget(self.report_text)

        actions = QHBoxLayout()
        self.finish_button = QPushButton("Завершил работу", self)
        self.save_button = QPushButton("Сохранить TXT", self)
        self.clear_history_button = QPushButton("Очистить историю", self)
        actions.addWidget(self.finish_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.clear_history_button)
        actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        for control in (
            self.period_filter, self.category_filter, self.shop_filter,
            self.marketplace_filter,
        ):
            control.currentIndexChanged.connect(self._refresh_report)
        self.finish_button.clicked.connect(self.work_finished.emit)
        self.save_button.clicked.connect(self._save_text)
        self.clear_history_button.clicked.connect(self._clear_history)
        self._populate_filters()

    def _populate_filters(self):
        self.periods = build_report_periods(self.search_runs, self.work_finishes)
        period_ids_with_reports = {
            period_for_report(report, self.periods) for report in self.reports.values()
        }
        self.period_filter.blockSignals(True)
        self.period_filter.clear()
        self.period_filter.addItem("Все", ALL_PERIOD_ID)
        for period in self.periods:
            if period["id"] in period_ids_with_reports:
                self.period_filter.addItem(period["label"], period["id"])
        if LEGACY_PERIOD_ID in period_ids_with_reports:
            self.period_filter.addItem("Без даты", LEGACY_PERIOD_ID)
        if self.period_filter.count() > 1:
            self.period_filter.setCurrentIndex(1)
        self.period_filter.blockSignals(False)
        self._populate_filter(self.category_filter, "Все категории", sorted({
            category for report in self.reports.values()
            for category in report.get("categories", {})
        }, key=str.casefold))
        self._populate_filter(self.shop_filter, "Все магазины", sorted({
            str(report.get("shop", "") or "").strip() or "UNKNOWN"
            for report in self.reports.values()
        }, key=str.casefold))
        self._populate_filter(self.marketplace_filter, "Все площадки", sorted({
            str(report.get("marketplace", "") or "").strip().upper() or "UNKNOWN"
            for report in self.reports.values()
        }))
        self.finish_button.setEnabled(bool(self.search_runs))
        self._refresh_report()

    @staticmethod
    def _populate_filter(widget, all_label, values):
        widget.blockSignals(True)
        widget.clear()
        widget.addItem(all_label, "")
        for value in values:
            widget.addItem(value, value)
        widget.blockSignals(False)

    def _filtered_reports(self):
        if not self.period_filter.count():
            return {}
        return filter_supply_reports(
            self.reports, self.periods,
            period_id=self.period_filter.currentData(),
            category=self.category_filter.currentData(),
            shop=self.shop_filter.currentData(),
            marketplace=self.marketplace_filter.currentData(),
        )

    def _refresh_report(self):
        self.report_text.setPlainText(format_supply_report(
            self._filtered_reports(), self.unavailable_count,
        ))

    def set_work_finished(self, work_finishes: list[str]):
        self.work_finishes = work_finishes
        self._populate_filters()

    def _save_text(self):
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "Сохранить отчёт", "Отчёт поставок.txt", "Текстовый файл (*.txt)",
        )
        if not path:
            return
        if not path.casefold().endswith(".txt"):
            path += ".txt"
        try:
            with open(path, "w", encoding="utf-8") as file:
                file.write(self.report_text.toPlainText())
        except OSError as error:
            QMessageBox.critical(self, "Сохранение отчёта", f"Не удалось сохранить файл:\n{error}")

    def _clear_history(self):
        answer = QMessageBox.question(
            self, "Очистить историю",
            "Удалить всю историю отчётов и рабочих интервалов? Это действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.history_cleared.emit()

    def set_history_cleared(self):
        self.reports = {}
        self.search_runs = []
        self.work_finishes = []
        self.unavailable_count = 0
        self._populate_filters()
