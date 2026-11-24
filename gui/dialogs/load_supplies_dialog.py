"""Choose deliveries from Trigu or load PDF files locally."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
    QComboBox,
)

from core.trigu_api import TriguApiError, TriguClient


class LoadSuppliesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Загрузить поставки")
        self.resize(900, 560)
        self.selected_supplies = []
        self.local_files = []
        self._supplies = []

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        trigu_tab = QWidget()
        trigu_layout = QVBoxLayout(trigu_tab)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Магазин:"))
        self.shop_filter = QComboBox()
        self.shop_filter.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(self.shop_filter)
        filters.addWidget(QLabel("Статус:"))
        self.status_filter = QComboBox()
        self.status_filter.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(self.status_filter)
        filters.addStretch()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._load_trigu)
        filters.addWidget(refresh)
        trigu_layout.addLayout(filters)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Номер", "Название", "Магазин", "Площадка", "Статус", "Позиций",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        trigu_layout.addWidget(self.table)
        self.trigu_status = QLabel("Нажмите «Обновить», чтобы получить активные поставки.")
        self.trigu_status.setWordWrap(True)
        trigu_layout.addWidget(self.trigu_status)
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
        self.shop_filter.blockSignals(True)
        self.status_filter.blockSignals(True)
        self.shop_filter.clear(); self.shop_filter.addItem("Все")
        self.status_filter.clear(); self.status_filter.addItem("Все")
        self.shop_filter.addItems(sorted({item.shop for item in self._supplies}, key=str.casefold))
        self.status_filter.addItems(sorted({item.status_label for item in self._supplies}, key=str.casefold))
        self.shop_filter.blockSignals(False)
        self.status_filter.blockSignals(False)
        self._apply_filters()

    def _apply_filters(self):
        shop, status = self.shop_filter.currentText(), self.status_filter.currentText()
        items = [item for item in self._supplies if (shop == "Все" or item.shop == shop) and (status == "Все" or item.status_label == status)]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = [item.number, item.name, item.shop, item.marketplace, item.status_label, str(item.items_count)]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.number)
                self.table.setItem(row, column, cell)
        self.table.setSortingEnabled(True)
        self.trigu_status.setText(f"Показано активных поставок: {len(items)}")

    def _choose_local_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите PDF-файлы поставок", "", "PDF files (*.pdf)")
        if files:
            self.local_files = files
            self.local_status.setText("Выбрано файлов: " + str(len(files)))

    def _accept(self):
        selected_numbers = {self.table.item(row.row(), 0).data(Qt.ItemDataRole.UserRole) for row in self.table.selectionModel().selectedRows()}
        self.selected_supplies = [item for item in self._supplies if item.number in selected_numbers]
        if not self.selected_supplies and not self.local_files:
            QMessageBox.information(self, "Нет выбора", "Выберите поставки Trigu или PDF-файлы.")
            return
        self.accept()
