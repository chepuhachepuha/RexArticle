import os
import sys
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QFileDialog, QMessageBox, QGridLayout, QWidget,
    QFrame, QRadioButton
)
from PySide6.QtCore import Qt

from supply_detector import detect_supply_type
from core.store_manager import load_stores, save_stores
from core.pdf_cache import get_pdf_text


# ============================================
# Стили для комбобокса (проверенные, без SVG)
# ============================================

COMBOBOX_STYLE = """
    /* Основной комбобокс */
    QComboBox {
        background-color: #FFFFFF;
        border: 1px solid #CCCCCC;
        border-radius: 4px;
        padding: 6px 32px 6px 10px;
        font-size: 13px;
        color: #212121;
        min-height: 24px;
    }

    QComboBox:hover {
        border-color: #9E9E9E;
    }

    QComboBox:focus {
        border: 1px solid #2196F3;
    }

    /* Убираем встроенный фрейм Qt (решает проблему "прямоугольника") */
    QComboBox > QAbstractItemView {
        background-color: #FFFFFF;
        border: 1px solid #CCCCCC;
        selection-background-color: #E3F2FD;
        selection-color: #212121;
        outline: none;
        padding: 4px;
    }

    /* Кнопка раскрытия (drop-down) */
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 28px;
        border: none;
        border-left: 1px solid #E0E0E0;
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
        background-color: transparent;
    }

    QComboBox::drop-down:hover {
        background-color: #F0F7FF;
    }

    /* ✅ Стрелка через border (надёжно работает во всех Qt 6) */
    QComboBox::down-arrow {
        image: none;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid #616161;
        margin-right: 8px;
    }

    QComboBox::drop-down:hover QComboBox::down-arrow {
        border-top-color: #2196F3;
    }

    /* Элементы выпадающего списка */
    QComboBox QAbstractItemView {
        background-color: #FFFFFF;
        border: 1px solid #CCCCCC;
        border-radius: 4px;
        selection-background-color: #2196F3;
        selection-color: #FFFFFF;
        outline: none;
        padding: 4px;
    }

    QComboBox QAbstractItemView::item {
        padding: 6px 12px;
        min-height: 24px;
        border-radius: 3px;
    }

    QComboBox QAbstractItemView::item:hover {
        background-color: #F5F5F5;
        color: #212121;
    }

    QComboBox QAbstractItemView::item:selected {
        background-color: #2196F3;
        color: #FFFFFF;
    }
"""


class NewStoreDialog(QDialog):
    """Диалог для добавления нового магазина или нового типа поставки."""

    def __init__(self, parent, pdf_path=None, existing_store=False, existing_types=None,
                 store_name=None, supply_type=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.existing_store = existing_store
        self.existing_types = existing_types or []
        self.result = None
        self.stores = load_stores()

        self.pdf_text = get_pdf_text(pdf_path) if pdf_path else ""

        initial = detect_supply_type(pdf_path) if pdf_path else {
            "store_name": store_name or "UNKNOWN", "supply_type": supply_type or "WB",
        }
        self.store_name = initial["store_name"]
        self.supply_type = initial["supply_type"]

        self.setWindowTitle("Настройки магазина")
        self.setMinimumWidth(550)
        self.setModal(True)

        self.setup_ui()
        for widget in self.findChildren(QWidget):
            widget.setStyleSheet("")
        self.save_button.setProperty("accent", True)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Заголовок
        if self.existing_store:
            title_text = f"Магазин '{self.store_name}' уже существует.\nДобавьте путь для нового типа поставки."
        else:
            title_text = "Обнаружен новый магазин"

        self.title_label = QLabel(title_text)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-weight: 600; font-size: 14px; margin-bottom: 8px;")
        layout.addWidget(self.title_label)

        mode_layout = QHBoxLayout()
        self.create_mode = QRadioButton("Создать новый магазин")
        self.profile_mode = QRadioButton("Использовать существующий профиль")
        self.profile_mode.setEnabled(bool(self.stores))
        mode_layout.addWidget(self.create_mode)
        mode_layout.addWidget(self.profile_mode)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        # Форма
        form_layout = QGridLayout()
        form_layout.setSpacing(10)
        form_layout.setHorizontalSpacing(12)

        # Магазин
        store_label = QLabel("Магазин:")
        store_label.setStyleSheet("font-weight: 500;")
        form_layout.addWidget(store_label, 0, 0)

        self.store_name_input = QLineEdit(self.store_name)
        self.store_name_input.setStyleSheet("""
            QLineEdit {
                padding: 6px 10px;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                font-size: 13px;
                background-color: #FFFFFF;
                color: #212121;
            }
            QLineEdit:hover {
                border-color: #9E9E9E;
            }
            QLineEdit:focus {
                border: 1px solid #2196F3;
            }
        """)
        form_layout.addWidget(self.store_name_input, 0, 1, 1, 2)

        # Тип поставки — ОБЫЧНЫЙ QComboBox (без анимации)
        type_label = QLabel("Тип поставки:")
        type_label.setStyleSheet("font-weight: 500;")
        form_layout.addWidget(type_label, 1, 0)

        self.type_combo = QComboBox()  # ✅ Обычный, без подкласса
        self.type_combo.addItems(["WB", "OZON"])
        self.type_combo.setCurrentText(self.supply_type)
        self.type_combo.setStyleSheet(COMBOBOX_STYLE)

        # ✅ ВАЖНО: убираем внутренний frame Qt
        self.type_combo.setFrame(False)
        self.type_combo.setEditable(False)

        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        form_layout.addWidget(self.type_combo, 1, 1)

        source_label = QLabel("Источник файлов:")
        source_label.setStyleSheet("font-weight: 500;")
        form_layout.addWidget(source_label, 2, 0)

        self.source_combo = QComboBox()
        self.source_combo.addItem("Локально или сетевая папка", "local")
        self.source_combo.setStyleSheet(COMBOBOX_STYLE)
        self.source_combo.setFrame(False)
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        form_layout.addWidget(self.source_combo, 2, 1, 1, 2)

        # Папка
        self.folder_label = QLabel("Папка с принтами:")
        self.folder_label.setStyleSheet("font-weight: 500;")
        form_layout.addWidget(self.folder_label, 3, 0)

        self.folder_input = QLineEdit()
        self.folder_input.setReadOnly(True)
        self.folder_input.setStyleSheet("""
            QLineEdit {
                padding: 6px 10px;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                font-size: 12px;
                background-color: #F5F5F5;
                color: #424242;
            }
        """)
        form_layout.addWidget(self.folder_input, 3, 1)

        self.browse_button = QPushButton("...")
        self.browse_button.setFixedWidth(40)
        self.browse_button.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #F0F0F0;
                border-color: #9E9E9E;
            }
            QPushButton:pressed {
                background-color: #E0E0E0;
            }
        """)
        self.browse_button.clicked.connect(self.choose_folder)
        form_layout.addWidget(self.browse_button, 3, 2)

        layout.addLayout(form_layout)

        self.create_widgets = [
            store_label, self.store_name_input, type_label, self.type_combo,
            source_label, self.source_combo, self.folder_label,
            self.folder_input, self.browse_button,
        ]

        self.profile_container = QFrame()
        profile_layout = QGridLayout(self.profile_container)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(10)
        profile_layout.addWidget(QLabel("Профиль магазина:"), 0, 0)
        self.profile_store_combo = QComboBox()
        self.profile_store_combo.addItems(sorted(self.stores, key=str.casefold))
        self.profile_store_combo.setStyleSheet(COMBOBOX_STYLE)
        self.profile_store_combo.setFrame(False)
        profile_layout.addWidget(self.profile_store_combo, 0, 1)
        profile_layout.addWidget(QLabel("Тип профиля:"), 1, 0)
        self.profile_type_combo = QComboBox()
        self.profile_type_combo.setStyleSheet(COMBOBOX_STYLE)
        self.profile_type_combo.setFrame(False)
        profile_layout.addWidget(self.profile_type_combo, 1, 1)
        self.profile_info = QLabel()
        self.profile_info.setWordWrap(True)
        self.profile_info.setStyleSheet("color: #757575; font-size: 11px;")
        profile_layout.addWidget(self.profile_info, 2, 0, 1, 2)
        layout.addWidget(self.profile_container)

        self.profile_store_combo.currentTextChanged.connect(
            self._refresh_profile_types
        )
        self.profile_type_combo.currentTextChanged.connect(
            self._refresh_profile_info
        )

        # Информация о существующих типах
        if self.existing_store and self.existing_types:
            self.existing_info_label = QLabel(
                f"Уже добавленные типы: {', '.join(self.existing_types)}"
            )
            self.existing_info_label.setStyleSheet("color: #757575; font-size: 11px; margin-top: 8px;")
            layout.addWidget(self.existing_info_label)
        else:
            self.existing_info_label = None

        # Разделитель
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #E0E0E0;")
        layout.addWidget(line)

        # Кнопки
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #F0F0F0;
                border-color: #9E9E9E;
            }
        """)
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)

        self.save_button = QPushButton("Сохранить")
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
                color: white;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
        """)
        self.save_button.clicked.connect(self.save)
        button_layout.addWidget(self.save_button)

        layout.addLayout(button_layout)

        self.create_mode.toggled.connect(self._on_mode_changed)
        if self.store_name == "UNKNOWN" and self.stores:
            self.profile_mode.setChecked(True)
        else:
            self.create_mode.setChecked(True)
        if self.store_name in self.stores:
            self.profile_store_combo.setCurrentText(self.store_name)
        self._refresh_profile_types(self.profile_store_combo.currentText())
        self._on_mode_changed()

    def _on_mode_changed(self, *_):
        temporary = self.profile_mode.isChecked()
        for widget in self.create_widgets:
            widget.setVisible(not temporary)
        self.profile_container.setVisible(temporary)
        if self.existing_info_label:
            self.existing_info_label.setVisible(not temporary)
        self.save_button.setText("Использовать" if temporary else "Сохранить")
        self.title_label.setText(
            "Выберите существующий профиль для текущего PDF"
            if temporary else (
                f"Магазин '{self.store_name}' уже существует.\n"
                "Добавьте путь для нового типа поставки."
                if self.existing_store else "Обнаружен новый магазин"
            )
        )

    def _refresh_profile_types(self, store_name):
        current = self.profile_type_combo.currentText()
        self.profile_type_combo.blockSignals(True)
        self.profile_type_combo.clear()
        store_data = self.stores.get(store_name, {})
        available = [
            value for value in ("WB", "OZON") if value in store_data
        ]
        self.profile_type_combo.addItems(available)
        preferred = self.supply_type if self.supply_type in available else current
        if preferred in available:
            self.profile_type_combo.setCurrentText(preferred)
        self.profile_type_combo.blockSignals(False)
        self._refresh_profile_info()

    def _refresh_profile_info(self, *_):
        store_name = self.profile_store_combo.currentText()
        supply_type = self.profile_type_combo.currentText()
        data = self.stores.get(store_name, {}).get(supply_type, {})
        if not data:
            self.profile_info.setText("У выбранного профиля нет доступного типа.")
            return
        source = "Локальная или подключённая сетевая папка"
        self.profile_info.setText(
            f"Источник: {source}\nПапка: {data.get('path', '')}"
        )

    def on_type_changed(self, new_type):
        new_store = self.extract_store_by_type(new_type)
        self.store_name_input.setText(new_store)

    def on_source_changed(self):
        self.folder_label.setText("Папка с принтами:")
        self.folder_input.clear()

    def extract_store_by_type(self, supply_type):
        lines = self.pdf_text.splitlines()

        if supply_type == "WB":
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("Магазин:"):
                    return stripped[len("Магазин:"):].strip()
        elif supply_type == "OZON":
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("Склад:"):
                    return stripped[len("Склад:"):].strip()

        return "UNKNOWN"

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Выберите папку с принтами магазина"
        )
        if folder:
            self.folder_input.setText(folder)

    def save(self):
        if self.profile_mode.isChecked():
            store_name = self.profile_store_combo.currentText()
            supply_type = self.profile_type_combo.currentText()
            type_data = self.stores.get(store_name, {}).get(supply_type)
            if not type_data:
                QMessageBox.warning(
                    self, "Ошибка", "Выберите магазин и тип существующего профиля"
                )
                return
            self.result = {
                "store_name": store_name,
                "supply_type": supply_type,
                "folder": type_data.get("path", ""),
                "source": type_data.get("source", "local"),
                "temporary_profile": True,
            }
            self.accept()
            return

        store_name = self.store_name_input.text().strip()
        supply_type = self.type_combo.currentText()
        source = self.source_combo.currentData()
        folder = self.folder_input.text().strip()

        if not store_name or store_name == "UNKNOWN":
            QMessageBox.warning(
                self, "Ошибка",
                "Не удалось определить название магазина"
            )
            return

        if not folder or (source == "local" and not os.path.isdir(folder)):
            QMessageBox.warning(
                self, "Ошибка",
                "Выберите корректную папку с принтами"
            )
            return

        stores = load_stores()

        if store_name in stores and supply_type in stores[store_name]:
            reply = QMessageBox.question(
                self, "Подтверждение",
                f"Магазин '{store_name}' уже имеет тип '{supply_type}'.\nПерезаписать путь?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        if store_name not in stores:
            stores[store_name] = {}
        stores[store_name][supply_type] = {
            "path": folder,
            "matcher": "Simple",
            "copy_on_override": False,
            "override_path": "",
            "source": source,
            "png_only": False,
            "size_ranges": [],
            "additional_sources": [],
        }

        save_stores(stores)

        self.result = {
            "store_name": store_name,
            "supply_type": supply_type,
            "folder": folder,
            "source": source,
        }
        self.accept()
