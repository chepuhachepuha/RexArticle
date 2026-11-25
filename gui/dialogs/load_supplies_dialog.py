"""Choose deliveries from Trigu or load PDF files locally."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget, QComboBox, QHeaderView, QListWidget, QListWidgetItem, QSplitter,
    QLineEdit,
)

from core.trigu_api import (
    TriguApiError, TriguClient, load_marketplace_order,
)


FILTER_COMBO_WIDTH = 150
SUPPLY_SEARCH_WIDTH = 220


class LoadSuppliesDialog(QDialog):
    def __init__(self, parent=None, *, auto_load=True):
        super().__init__(parent)
        self.setWindowTitle("Загрузить поставки")
        self.resize(1200, 680)
        self.setMinimumSize(1000, 600)
        self.selected_supplies = []
        self.local_files = []
        self._supplies = []
        self._selected_numbers = set()
        self._updating_table = False
        self.marketplace_order = load_marketplace_order()
        self._sort_column = 1
        self._sort_order = Qt.SortOrder.AscendingOrder

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        trigu_tab = QWidget()
        trigu_layout = QVBoxLayout(trigu_tab)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Магазин:"))
        self.shop_filter = QComboBox()
        self.shop_filter.setFixedWidth(FILTER_COMBO_WIDTH)
        self.shop_filter.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(self.shop_filter)
        filters.addWidget(QLabel("Статус:"))
        self.status_filter = QComboBox()
        self.status_filter.setFixedWidth(FILTER_COMBO_WIDTH)
        self.status_filter.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(self.status_filter)
        filters.addWidget(QLabel("Площадка:"))
        self.marketplace_filter = QComboBox()
        self.marketplace_filter.setFixedWidth(FILTER_COMBO_WIDTH)
        self.marketplace_filter.addItem("Все", "")
        self.marketplace_filter.addItem("WB", "WB")
        self.marketplace_filter.addItem("Ozon", "OZON")
        self.marketplace_filter.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.marketplace_filter)
        self.name_search = QLineEdit()
        self.name_search.setPlaceholderText("Поиск по названию поставки")
        self.name_search.setClearButtonEnabled(True)
        self.name_search.setFixedWidth(SUPPLY_SEARCH_WIDTH)
        self.name_search.textChanged.connect(self._apply_filters)
        filters.addWidget(self.name_search)
        filters.addStretch()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._load_trigu)
        filters.addWidget(refresh)
        trigu_layout.addLayout(filters)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "", "Номер", "Название", "Магазин", "Площадка", "Статус",
            "Позиций",
        ])
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setSortingEnabled(False)
        table_header = self.table.horizontalHeader()
        table_header.setStretchLastSection(False)
        table_header.setSortIndicatorShown(True)
        table_header.setSortIndicator(1, self._sort_order)
        table_header.sortIndicatorChanged.connect(self._on_sort_changed)
        self.table.itemChanged.connect(self._on_checkbox_changed)
        splitter.addWidget(self.table)

        selected_panel = QWidget()
        selected_panel.setMinimumWidth(280)
        selected_layout = QVBoxLayout(selected_panel)
        selected_layout.setContentsMargins(12, 0, 0, 0)
        selected_title = QLabel("Выбрано")
        selected_title.setStyleSheet("font-weight: 600;")
        selected_layout.addWidget(selected_title)
        self.selected_list = QListWidget()
        self.selected_list.setWordWrap(True)
        self.selected_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.selected_list.setStyleSheet("""
            QListWidget::item {
                border-bottom: 1px solid #4a4a4a;
                padding: 6px;
            }
            QListWidget::item:selected {
                background: #3b536d;
            }
        """)
        self.selected_list.setToolTip(
            "Двойной клик снимает выбор поставки"
        )
        self.selected_list.itemDoubleClicked.connect(
            self._remove_from_selected
        )
        selected_layout.addWidget(self.selected_list)
        self.selected_count = QLabel("Выбрано поставок: 0")
        selected_layout.addWidget(self.selected_count)
        splitter.addWidget(selected_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([860, 300])
        trigu_layout.addWidget(splitter, 1)
        self.trigu_status = QLabel("Нажмите «Обновить», чтобы получить активные поставки.")
        self.trigu_status.setWordWrap(True)
        trigu_layout.addWidget(self.trigu_status, 0)
        tabs.addTab(trigu_tab, "Trigu")

        local_tab = QWidget()
        local_layout = QVBoxLayout(local_tab)
        local_layout.addWidget(QLabel("Выберите PDF-файлы поставок для прежнего локального сценария."))
        local_button = QPushButton("Выбрать PDF-файлы")
        local_button.clicked.connect(self._choose_local_files)
        local_layout.addWidget(local_button)
        self.local_status = QLabel("Файлы не выбраны")
        self.local_status.setWordWrap(True)
        local_layout.addWidget(self.local_status)
        local_layout.addStretch()
        tabs.addTab(local_tab, "Локально")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Готово")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if auto_load:
            QTimer.singleShot(0, self._load_trigu)

    def _load_trigu(self):
        try:
            self._supplies = TriguClient().list_active_supplies()
        except TriguApiError as error:
            self.trigu_status.setText(str(error))
            answer = QMessageBox.question(
                self, "Trigu API", f"{error}\n\nОткрыть настройки API?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes and hasattr(self.parent(), "open_settings"):
                self.parent().open_settings()
            return
        available_numbers = {item.number for item in self._supplies}
        self._selected_numbers.intersection_update(available_numbers)
        self._populate_filters()
        self._resize_columns_to_all_contents()
        self._apply_filters()

    def _populate_filters(self):
        self.shop_filter.blockSignals(True)
        self.status_filter.blockSignals(True)
        self.marketplace_filter.blockSignals(True)
        self.shop_filter.clear(); self.shop_filter.addItem("Все")
        self.status_filter.clear()
        self.status_filter.addItem("Все", "")
        self.status_filter.addItem("Формируется", "forming")
        self.status_filter.addItem("В работе", "production")
        self.shop_filter.addItems(sorted({item.shop for item in self._supplies}, key=str.casefold))
        self.shop_filter.blockSignals(False)
        self.status_filter.blockSignals(False)
        self.marketplace_filter.blockSignals(False)

    @staticmethod
    def _status_text(status):
        return {
            "forming": "Формируется",
            "production": "В работе",
        }.get(status.status, status.status_label or status.status)

    @classmethod
    def _supply_values(cls, item):
        return [
            item.number, item.name, item.shop, item.marketplace,
            cls._status_text(item), str(item.items_count),
        ]

    def _resize_columns_to_all_contents(self):
        metrics = self.table.fontMetrics()
        headers = [
            "Номер", "Название", "Магазин", "Площадка", "Статус", "Позиций",
        ]
        values_by_column = [[header] for header in headers]
        for item in self._supplies:
            for column, value in enumerate(self._supply_values(item)):
                values_by_column[column].append(str(value))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 42)
        for column, values in enumerate(values_by_column, start=1):
            width = max(metrics.horizontalAdvance(value) for value in values) + 34
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, max(72, width))

    def _apply_filters(self):
        shop = self.shop_filter.currentText()
        status = self.status_filter.currentData() or ""
        marketplace = self.marketplace_filter.currentData() or ""
        name_query = self.name_search.text().casefold().strip()
        items = self._sort_supplies([
            item for item in self._supplies
            if (shop == "Все" or item.shop == shop)
            and (not status or item.status == status)
            and (not marketplace or item.marketplace.upper() == marketplace)
            and (not name_query or name_query in item.name.casefold())
        ])
        self._updating_table = True
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            checkbox = QTableWidgetItem()
            checkbox.setData(Qt.ItemDataRole.UserRole, item.number)
            checkbox.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            checkbox.setCheckState(
                Qt.CheckState.Checked
                if item.number in self._selected_numbers
                else Qt.CheckState.Unchecked
            )
            checkbox.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, checkbox)
            values = self._supply_values(item)
            for column, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.number)
                self.table.setItem(row, column, cell)
        self._updating_table = False
        self._refresh_selected_list()
        self.trigu_status.setText(f"Показано активных поставок: {len(items)}")

    def _on_sort_changed(self, column, order):
        self._sort_column = column
        self._sort_order = order
        self._apply_filters()

    def _sort_supplies(self, supplies):
        rank = {
            marketplace: index
            for index, marketplace in enumerate(self.marketplace_order)
        }
        groups = {}
        for supply in supplies:
            marketplace = str(supply.marketplace or "").upper()
            groups.setdefault(marketplace, []).append(supply)

        def value(item):
            value_column = max(0, self._sort_column - 1)
            if value_column == 5:
                return item.items_count
            return str(self._supply_values(item)[value_column]).casefold()

        result = []
        marketplaces = sorted(
            groups,
            key=lambda item: (rank.get(item, len(rank)), item.casefold()),
        )
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder
        for marketplace in marketplaces:
            result.extend(sorted(groups[marketplace], key=value, reverse=reverse))
        return result

    def _visible_number(self, row):
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _on_checkbox_changed(self, item):
        if self._updating_table or item.column() != 0:
            return
        number = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_numbers.add(number)
        else:
            self._selected_numbers.discard(number)
        self._refresh_selected_list()

    def _restore_checkbox_states(self):
        for row in range(self.table.rowCount()):
            checkbox = self.table.item(row, 0)
            if checkbox is not None:
                checkbox.setCheckState(
                    Qt.CheckState.Checked
                    if self._visible_number(row) in self._selected_numbers
                    else Qt.CheckState.Unchecked
                )

    def _refresh_selected_list(self):
        self.selected_list.clear()
        selected = self._sort_supplies([
            supply for supply in self._supplies
            if supply.number in self._selected_numbers
        ])
        for supply in selected:
            item = QListWidgetItem(supply.name)
            item.setData(Qt.ItemDataRole.UserRole, supply.number)
            self.selected_list.addItem(item)
        self.selected_count.setText(
            f"Выбрано поставок: {len(self._selected_numbers)}"
        )

    def _remove_from_selected(self, item):
        self._selected_numbers.discard(
            item.data(Qt.ItemDataRole.UserRole)
        )
        self._updating_table = True
        self._restore_checkbox_states()
        self._updating_table = False
        self._refresh_selected_list()

    def _choose_local_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите PDF-файлы поставок", "", "PDF files (*.pdf)")
        if files:
            self.local_files = files
            self.local_status.setText("Выбрано файлов: " + str(len(files)))

    def _accept(self):
        self.selected_supplies = self._sort_supplies([
            item for item in self._supplies
            if item.number in self._selected_numbers
        ])
        if not self.selected_supplies and not self.local_files:
            QMessageBox.information(self, "Нет выбора", "Выберите поставки Trigu или PDF-файлы.")
            return
        self.accept()
