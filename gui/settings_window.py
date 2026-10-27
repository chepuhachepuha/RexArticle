import os
import json
import time
import sys
import re
from copy import deepcopy
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame, QLineEdit,
    QCheckBox, QFileDialog, QMessageBox, QGroupBox,
    QInputDialog, QSplitter, QTextEdit, QApplication, QProgressBar, QComboBox,
    QTabWidget, QTimeEdit, QTableWidget, QTableWidgetItem, QDialogButtonBox,
    QHeaderView, QListWidget
)
from PySide6.QtCore import (
    Qt, QTime, QTimer, QObject, QRunnable, QThreadPool, Signal, Slot, QUrl,
)
from PySide6.QtGui import QDesktopServices

from core.store_manager import (
    load_stores, normalise_additional_sources, prune_unused_indexes, save_stores,
)
from core.paths import get_data_path
from core.json_storage import atomic_write_json
from core.personalization import normalise_store_suffix_exclusions
from indexer import (
    normalise_source,
    remove_index_for_store,
    update_source_index,
    update_index_for_store,
    update_yandex_indexes,
)
from core.yandex_disk import (
    YandexDiskClient, YandexDiskError, build_authorization_url,
    configure_yandex_token, delete_saved_yandex_token,
    load_saved_yandex_token,
)
from gui.dialogs.yandex_disk_picker import YandexDiskPickerDialog
from matcher import get_matcher_options, resolve_matcher_name
from core.text_normalization import normalize_article
from core.ozon_sizes import (
    COMMON_GROUPED_SIZES, CONFIG_KEY as OZON_SIZES_CONFIG_KEY,
    STANDARD_SIZES, clean_custom_sizes, validate_custom_size,
)

OVERRIDES_FILE = get_data_path("manual_overrides.json")


def _parse_range_sizes(text):
    result = []
    seen = set()
    for value in re.split(r"[,;\n]+", text):
        value = value.strip()
        key = normalize_article(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value.upper())
    return result


class SizeRangeEditDialog(QDialog):
    def __init__(self, parent=None, item=None):
        super().__init__(parent)
        self.setWindowTitle("Диапазон размеров")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Паттерн в конце имени файла:"))
        self.pattern = QLineEdit((item or {}).get("pattern", ""))
        layout.addWidget(self.pattern)
        layout.addWidget(QLabel("Размеры через запятую, точку с запятой или новую строку:"))
        self.sizes = QTextEdit()
        self.sizes.setPlainText(", ".join((item or {}).get("sizes", [])))
        self.sizes.setFixedHeight(90)
        layout.addWidget(self.sizes)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        if not normalize_article(self.pattern.text()):
            QMessageBox.warning(self, "Ошибка", "Паттерн не может быть пустым.")
            return
        if not _parse_range_sizes(self.sizes.toPlainText()):
            QMessageBox.warning(self, "Ошибка", "Укажите хотя бы один размер.")
            return
        self.accept()

    def value(self):
        return {
            "pattern": self.pattern.text(),
            "sizes": _parse_range_sizes(self.sizes.toPlainText()),
        }


class SizeRangesDialog(QDialog):
    def __init__(self, ranges, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Диапазоны размеров")
        self.resize(620, 360)
        self.ranges = deepcopy(ranges or [])
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Паттерн", "Размеры"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for text_value, callback in (
            ("Добавить", self._add), ("Изменить", self._edit),
            ("Удалить", self._delete), ("↑", lambda: self._move(-1)),
            ("↓", lambda: self._move(1)),
        ):
            button = QPushButton(text_value)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self, selected=None):
        self.table.setRowCount(len(self.ranges))
        for row, item in enumerate(self.ranges):
            self.table.setItem(row, 0, QTableWidgetItem(item["pattern"]))
            self.table.setItem(row, 1, QTableWidgetItem(", ".join(item["sizes"])))
        if selected is not None and self.ranges:
            self.table.selectRow(max(0, min(selected, len(self.ranges) - 1)))

    def _current_row(self):
        return self.table.currentRow()

    def _validate_pattern(self, item, skip=-1):
        key = normalize_article(item["pattern"])
        return all(i == skip or normalize_article(value["pattern"]) != key for i, value in enumerate(self.ranges))

    def _add(self):
        dialog = SizeRangeEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            item = dialog.value()
            if not self._validate_pattern(item):
                QMessageBox.warning(self, "Ошибка", "Такой паттерн уже существует.")
                return
            self.ranges.append(item)
            self._refresh(len(self.ranges) - 1)

    def _edit(self):
        row = self._current_row()
        if row < 0:
            return
        dialog = SizeRangeEditDialog(self, self.ranges[row])
        if dialog.exec() == QDialog.Accepted:
            item = dialog.value()
            if not self._validate_pattern(item, row):
                QMessageBox.warning(self, "Ошибка", "Такой паттерн уже существует.")
                return
            self.ranges[row] = item
            self._refresh(row)

    def _delete(self):
        row = self._current_row()
        if row >= 0:
            del self.ranges[row]
            self._refresh(row)

    def _move(self, offset):
        row = self._current_row()
        target = row + offset
        if row < 0 or target < 0 or target >= len(self.ranges):
            return
        self.ranges[row], self.ranges[target] = self.ranges[target], self.ranges[row]
        self._refresh(target)


class SettingsIndexSignals(QObject):
    store_started = Signal(int, int, str)
    folder_scanned = Signal(str, int, int)
    store_finished = Signal(int, int, str)
    finished = Signal()
    error = Signal(str)


class SettingsIndexWorker(QRunnable):
    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks
        self.signals = SettingsIndexSignals()

    def _full_task(self, position, total, task, client=None):
        label = task["label"]
        self.signals.store_started.emit(position, total, label)
        print(f"[INDEX] Начало {position}/{total}: {label}")

        def on_folder(path, folder_number, files_found):
            self.signals.folder_scanned.emit(path, folder_number, files_found)

        if task.get("shared_only"):
            update_source_index(
                task.get("source", "local"), task["folder"],
                force_full=True, progress_callback=on_folder,
            )
        else:
            update_index_for_store(
                task["key"], task["folder"],
                override_path=task.get("override_path"), force=True,
                progress_callback=on_folder,
            )
        self.signals.store_finished.emit(position, total, label)
        print(f"[INDEX] Завершено {position}/{total}: {label}")

    def _run_full(self):
        total = len(self.tasks)
        indexed_tasks = list(enumerate(self.tasks, start=1))
        local_tasks = [
            item for item in indexed_tasks if item[1].get("source") != "yandex"
        ]
        cloud_tasks = [
            item for item in indexed_tasks if item[1].get("source") == "yandex"
        ]

        for position, task in local_tasks:
            self._full_task(position, total, task)

        def started(position, _total, task):
            label = task["label"]
            display_position = task.get("_display_position", position)
            self.signals.store_started.emit(display_position, total, label)
            print(f"[INDEX] Начало {display_position}/{total}: {label}")

        def finished(position, _total, task):
            label = task["label"]
            display_position = task.get("_display_position", position)
            self.signals.store_finished.emit(display_position, total, label)
            print(f"[INDEX] Завершено {display_position}/{total}: {label}")

        prepared_tasks = []
        for display_position, task in cloud_tasks:
            prepared = dict(task)
            prepared["_display_position"] = display_position

            def on_folder(path, folder_number, files_found):
                self.signals.folder_scanned.emit(path, folder_number, files_found)

            prepared["progress_callback"] = on_folder
            prepared_tasks.append(prepared)
        update_yandex_indexes(
            prepared_tasks,
            on_store_started=started,
            on_store_finished=finished,
        )

    @Slot()
    def run(self):
        started = time.perf_counter()
        try:
            total = len(self.tasks)
            print(f"[INDEX] Обновление: {total} индексов")
            self._run_full()
            elapsed = (time.perf_counter() - started) * 1000
            print(
                "[INDEX] Обновление всех выбранных индексов завершено: "
                f"{elapsed:.0f} мс"
            )
            self.signals.finished.emit()
        except Exception as error:
            elapsed = (time.perf_counter() - started) * 1000
            print(
                f"[INDEX] Ошибка ручного обновления через {elapsed:.0f} мс: "
                f"{error}"
            )
            self.signals.error.emit(str(error))


class IndexProgressDialog(QDialog):
    def __init__(self, total, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Обновление индексов")
        self.setModal(True)
        self.setFixedSize(560, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.title_label = QLabel("Подготовка индексации…")
        self.title_label.setProperty("settingsTitle", True)
        layout.addWidget(self.title_label)

        self.folder_label = QLabel("Ожидание первой папки")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.folder_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(24)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel(f"0 из {total} индексов")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

    def reject(self):
        # Не закрываем окно посреди сетевого обхода: worker должен закончить
        # транзакцию или корректно сохранить прежний индекс.
        return

    def store_started(self, position, total, label):
        self.title_label.setText(f"Индексируется: {label}")
        self.status_label.setText(f"Индекс {position} из {total}")
        self.folder_label.setText("Подключение к папке…")
        self.progress_bar.setRange(0, 0)

    def folder_scanned(self, path, folder_number, files_found):
        self.folder_label.setText(
            f"Папка {folder_number} · найдено файлов: {files_found}\n{path}"
        )

    def store_finished(self, position, total, label):
        self.status_label.setText(f"Готово {position} из {total}: {label}")

    def finish(self):
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.title_label.setText("Индексы обновлены")
        self.folder_label.setText("Все выбранные папки успешно обработаны")
        self.status_label.setText("Готово")


class StoreSuffixExclusionsDialog(QDialog):
    """Select stores that must not be appended to delivery folder names."""

    def __init__(
        self,
        stores,
        display_name,
        selected_store_names=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Не подписывать магазины")
        self.resize(520, 460)
        self._checkboxes = {}
        selected = normalise_store_suffix_exclusions(selected_store_names)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Отметьте магазины, название которых не нужно добавлять "
            "в квадратных скобках к папке поставки."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget(scroll)
        stores_layout = QVBoxLayout(container)

        for store_name in stores:
            visible_name = display_name(store_name)
            label = (
                visible_name
                if visible_name == store_name
                else f"{visible_name} ({store_name})"
            )
            checkbox = QCheckBox(label, container)
            checkbox.setChecked(store_name in selected)
            self._checkboxes[store_name] = checkbox
            stores_layout.addWidget(checkbox)

        if not self._checkboxes:
            empty_label = QLabel("Настроенных магазинов пока нет", container)
            empty_label.setObjectName("settingsSubtitle")
            stores_layout.addWidget(empty_label)

        stores_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_store_names(self):
        return {
            store_name
            for store_name, checkbox in self._checkboxes.items()
            if checkbox.isChecked()
        }


class AdditionalSourcesDialog(QDialog):
    def __init__(self, sources, known_sources, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Дополнительные источники")
        self.resize(720, 430)
        self.sources = deepcopy(sources or [])
        self.known_sources = list(known_sources)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Источники проверяются сверху вниз только после основной папки."
        )
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._update_actions)
        layout.addWidget(self.list, 1)
        add_row = QHBoxLayout()
        existing = QPushButton("Из настроенных папок…")
        existing.clicked.connect(self._add_existing)
        add_row.addWidget(existing)
        local = QPushButton("Добавить локальную…")
        local.clicked.connect(self._add_local)
        add_row.addWidget(local)
        cloud = QPushButton("Добавить Яндекс Диск…")
        cloud.setEnabled(not sys.platform.startswith("win"))
        cloud.clicked.connect(self._add_cloud)
        add_row.addWidget(cloud)
        add_row.addStretch()
        layout.addLayout(add_row)
        actions = QHBoxLayout()
        self.up = QPushButton("Вверх")
        self.down = QPushButton("Вниз")
        self.delete = QPushButton("Удалить")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.delete.clicked.connect(self._delete)
        actions.addWidget(self.up)
        actions.addWidget(self.down)
        actions.addWidget(self.delete)
        actions.addStretch()
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _label(self, source):
        kind = "Яндекс Диск" if source.get("source") == "yandex" else "Локальная"
        return f"{kind} · {source.get('path', '')}"

    def _refresh(self, selected=None):
        self.list.clear()
        self.list.addItems([self._label(item) for item in self.sources])
        if selected is not None and self.sources:
            self.list.setCurrentRow(max(0, min(selected, len(self.sources) - 1)))
        self._update_actions()

    def _append(self, source, path):
        candidate = {"source": source, "path": str(path or "").strip()}
        cleaned = normalise_additional_sources([*self.sources, candidate])
        if len(cleaned) == len(self.sources):
            QMessageBox.warning(self, "Источник", "Такая папка уже добавлена.")
            return
        self.sources = cleaned
        self._refresh(len(self.sources) - 1)

    def _add_existing(self):
        labels = [self._label(item) for item in self.known_sources]
        if not labels:
            QMessageBox.information(self, "Источники", "Других настроенных папок нет.")
            return
        label, accepted = QInputDialog.getItem(
            self, "Выберите источник", "Папка:", labels, 0, False
        )
        if accepted:
            self._append(**self.known_sources[labels.index(label)])

    def _add_local(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите папку с принтами")
        if path:
            self._append("local", path)

    def _add_cloud(self):
        picker = YandexDiskPickerDialog(parent=self)
        if picker.exec() == QDialog.Accepted:
            self._append("yandex", picker.selected_path)

    def _update_actions(self, *_):
        row = self.list.currentRow()
        self.up.setEnabled(row > 0)
        self.down.setEnabled(0 <= row < len(self.sources) - 1)
        self.delete.setEnabled(row >= 0)

    def _move(self, offset):
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= len(self.sources):
            return
        self.sources[row], self.sources[target] = self.sources[target], self.sources[row]
        self._refresh(target)

    def _delete(self):
        row = self.list.currentRow()
        if row >= 0:
            del self.sources[row]
            self._refresh(row)

class SettingsWindow(QDialog):
    """Окно настроек магазинов с авто-сохранением."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки магазинов")
        self.setGeometry(150, 150, 980, 640)
        self.setObjectName("settingsWindow")

        self.stores = load_stores()
        self.config_file = get_data_path("config.json")
        self.app_config = self._load_app_config()
        self.ozon_custom_sizes = clean_custom_sizes(
            self.app_config.get(OZON_SIZES_CONFIG_KEY, [])
        )
        self.store_display_names = dict(
            self.app_config.get("store_display_names", {}) or {}
        )
        self.store_suffix_exclusions = normalise_store_suffix_exclusions(
            self.app_config.get("personalization_store_suffix_exclusions", [])
        )
        self.selected_store = None
        self.needs_reset_cache = False
        self.type_vars = {}
        self.store_buttons = {}
        self.index_worker = None
        self.index_dialog = None
        self.index_thread_pool = QThreadPool.globalInstance()

        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._auto_save)

        self.setup_ui()

        if self.stores:
            first_store = next(iter(self.stores.keys()))
            self.select_store(first_store)

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setDocumentMode(True)
        main_layout.addWidget(self.tabs)
        stores_tab = QWidget()
        stores_layout = QHBoxLayout(stores_tab)
        stores_layout.setContentsMargins(16, 16, 16, 16)
        stores_layout.setSpacing(12)

        splitter = QSplitter(Qt.Horizontal)

        # ============================================
        # ЛЕВАЯ ПАНЕЛЬ: Список магазинов
        # ============================================
        left_widget = QWidget()
        left_widget.setObjectName("settingsSidebar")
        left_widget.setMinimumWidth(220)
        left_widget.setMaximumWidth(280)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(12, 14, 12, 12)
        left_layout.setSpacing(10)

        left_title = QLabel("Магазины")
        left_title.setObjectName("settingsSidebarTitle")
        left_layout.addWidget(left_title)

        sidebar_hint = QLabel("Папки и правила поиска")
        sidebar_hint.setObjectName("settingsSidebarHint")
        left_layout.addWidget(sidebar_hint)

        self.stores_scroll_area = QScrollArea()
        self.stores_scroll_area.setObjectName("storesScroll")
        self.stores_scroll_area.setWidgetResizable(True)
        self.stores_scroll_area.setFrameShape(QFrame.NoFrame)
        self.stores_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stores_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stores_scroll_area.viewport().setObjectName("storesViewport")

        self.stores_container = QWidget()
        self.stores_container.setObjectName("storesContainer")
        self.stores_layout = QVBoxLayout(self.stores_container)
        self.stores_layout.setContentsMargins(0, 0, 0, 0)
        self.stores_layout.setSpacing(4)
        self.stores_layout.setAlignment(Qt.AlignTop)

        self.stores_scroll_area.setWidget(self.stores_container)
        left_layout.addWidget(self.stores_scroll_area, stretch=1)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        left_layout.addWidget(separator)

        self.update_all_indexes_button = QPushButton("Обновить все индексы")
        self.update_all_indexes_button.setProperty("primary", True)
        self.update_all_indexes_button.setFixedHeight(36)
        self.update_all_indexes_button.clicked.connect(self.update_all_indexes)
        left_layout.addWidget(self.update_all_indexes_button)

        self.delete_store_button = QPushButton("Удалить магазин")
        self.delete_store_button.setProperty("destructive", True)
        self.delete_store_button.setFixedHeight(32)
        self.delete_store_button.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 6px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #F5F5F5;
                border-color: #9E9E9E;
            }
        """)
        self.delete_store_button.clicked.connect(self.delete_store)
        left_layout.addWidget(self.delete_store_button)

        splitter.addWidget(left_widget)

        # ============================================
        # ПРАВАЯ ПАНЕЛЬ: Настройки
        # ============================================
        right_widget = QWidget()
        right_widget.setObjectName("settingsContent")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 2, 2, 2)
        right_layout.setSpacing(4)

        title_layout = QHBoxLayout()
        self.store_title = QLabel("Выберите магазин")
        self.store_title.setObjectName("settingsTitle")
        title_layout.addWidget(self.store_title, 1)

        self.update_store_index_button = QPushButton("Обновить индексы")
        self.update_store_index_button.setProperty("primary", True)
        self.update_store_index_button.setFixedHeight(34)
        self.update_store_index_button.setEnabled(False)
        self.update_store_index_button.clicked.connect(self.update_selected_store_indexes)
        title_layout.addWidget(self.update_store_index_button)
        right_layout.addLayout(title_layout)

        self.store_subtitle = QLabel("Настройте папки принтов и правила обработки поставок")
        self.store_subtitle.setObjectName("settingsSubtitle")
        right_layout.addWidget(self.store_subtitle)

        settings_scroll = QScrollArea()
        self.settings_scroll = settings_scroll
        settings_scroll.setObjectName("settingsScroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        settings_scroll.viewport().setObjectName("settingsViewport")

        self.settings_container = QWidget()
        self.settings_container.setObjectName("settingsContainer")
        self.settings_layout = QVBoxLayout(self.settings_container)
        self.settings_layout.setSpacing(14)
        self.settings_layout.setContentsMargins(2, 12, 10, 12)
        self.settings_layout.setAlignment(Qt.AlignTop)

        self.types_container = QWidget()
        self.types_container.setObjectName("typesContainer")
        self.types_layout = QVBoxLayout(self.types_container)
        self.types_layout.setSpacing(8)
        self.types_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_layout.addWidget(self.types_container)

        # Секция overrides
        overrides_group = QGroupBox("Ручные уточнения (overrides)")
        overrides_group.setProperty("settingsCard", True)
        overrides_group.setStyleSheet("""
            QGroupBox {
                font-weight: 600;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                margin-top: 12px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        overrides_layout = QVBoxLayout(overrides_group)

        self.overrides_label = QLabel("—")
        overrides_layout.addWidget(self.overrides_label)

        overrides_buttons = QHBoxLayout()

        self.view_overrides_button = QPushButton("Просмотреть")
        self.view_overrides_button.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #F5F5F5;
            }
        """)
        self.view_overrides_button.clicked.connect(self.view_overrides)
        overrides_buttons.addWidget(self.view_overrides_button)

        self.clear_overrides_button = QPushButton("Очистить")
        self.clear_overrides_button.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #F5F5F5;
            }
        """)
        self.clear_overrides_button.clicked.connect(self.clear_overrides)
        overrides_buttons.addWidget(self.clear_overrides_button)

        overrides_buttons.addStretch()
        overrides_layout.addLayout(overrides_buttons)

        self.settings_layout.addWidget(overrides_group)

        settings_scroll.setWidget(self.settings_container)
        right_layout.addWidget(settings_scroll)

        splitter.addWidget(right_widget)
        splitter.setSizes([200, 600])

        stores_layout.addWidget(splitter)
        self.tabs.addTab(stores_tab, "Магазины")
        self.tabs.addTab(self._create_ozon_sizes_tab(), "Словарь размеров Ozon")
        self.tabs.addTab(self._create_personalization_tab(), "Персонализация")
        self.tabs.addTab(self._create_yandex_tab(), "Яндекс Диск")

        self.refresh_store_buttons()
        self._clear_inline_styles()

    def _create_ozon_sizes_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel("Словарь размеров Ozon")
        title.setObjectName("settingsTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Размеры используются при разборе поставок Ozon. "
            "Числовые размеры, кроме 0, отделяются только после запятой."
        )
        hint.setObjectName("settingsSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        columns = QHBoxLayout()
        columns.setSpacing(12)

        builtins_group = QGroupBox("Готовые размеры")
        builtins_group.setProperty("settingsCard", True)
        builtins_layout = QVBoxLayout(builtins_group)
        builtins_list = QListWidget()
        builtins_list.addItems([*STANDARD_SIZES, *COMMON_GROUPED_SIZES])
        builtins_list.setSelectionMode(QListWidget.NoSelection)
        builtins_layout.addWidget(builtins_list)
        builtin_hint = QLabel(
            "Также распознаются другие сочетания стандартных размеров "
            "в скобках, например S(M)."
        )
        builtin_hint.setWordWrap(True)
        builtins_layout.addWidget(builtin_hint)
        columns.addWidget(builtins_group, 1)

        custom_group = QGroupBox("Свои размеры")
        custom_group.setProperty("settingsCard", True)
        custom_layout = QVBoxLayout(custom_group)
        self.ozon_custom_sizes_list = QListWidget()
        self.ozon_custom_sizes_list.addItems(self.ozon_custom_sizes)
        self.ozon_custom_sizes_list.currentRowChanged.connect(
            self._update_ozon_size_actions
        )
        custom_layout.addWidget(self.ozon_custom_sizes_list)

        add_row = QHBoxLayout()
        self.ozon_size_input = QLineEdit()
        self.ozon_size_input.setPlaceholderText("Новый размер")
        self.ozon_size_input.returnPressed.connect(self._add_ozon_size)
        add_row.addWidget(self.ozon_size_input, 1)
        add_button = QPushButton("Добавить")
        add_button.clicked.connect(self._add_ozon_size)
        add_row.addWidget(add_button)
        custom_layout.addLayout(add_row)

        actions = QHBoxLayout()
        self.ozon_size_edit_button = QPushButton("Изменить")
        self.ozon_size_edit_button.clicked.connect(self._edit_ozon_size)
        actions.addWidget(self.ozon_size_edit_button)
        self.ozon_size_delete_button = QPushButton("Удалить")
        self.ozon_size_delete_button.setProperty("destructive", True)
        self.ozon_size_delete_button.clicked.connect(self._delete_ozon_size)
        actions.addWidget(self.ozon_size_delete_button)
        actions.addStretch()
        custom_layout.addLayout(actions)
        columns.addWidget(custom_group, 1)
        layout.addLayout(columns, 1)
        self._update_ozon_size_actions()
        return tab

    def _update_ozon_size_actions(self, *_):
        selected = self.ozon_custom_sizes_list.currentRow() >= 0
        self.ozon_size_edit_button.setEnabled(selected)
        self.ozon_size_delete_button.setEnabled(selected)

    def _save_ozon_sizes(self, values, selected=None):
        config = self._load_app_config()
        config[OZON_SIZES_CONFIG_KEY] = values
        try:
            atomic_write_json(self.config_file, config)
        except OSError as error:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить словарь: {error}")
            return False
        self.app_config = config
        self.ozon_custom_sizes = list(values)
        self.ozon_custom_sizes_list.clear()
        self.ozon_custom_sizes_list.addItems(values)
        if selected is not None and values:
            self.ozon_custom_sizes_list.setCurrentRow(min(selected, len(values) - 1))
        self._update_ozon_size_actions()
        return True

    def _add_ozon_size(self):
        try:
            value = validate_custom_size(
                self.ozon_size_input.text(), self.ozon_custom_sizes
            )
        except ValueError as error:
            QMessageBox.warning(self, "Некорректный размер", str(error))
            return
        values = [*self.ozon_custom_sizes, value]
        if self._save_ozon_sizes(values, len(values) - 1):
            self.ozon_size_input.clear()

    def _edit_ozon_size(self):
        row = self.ozon_custom_sizes_list.currentRow()
        if row < 0:
            return
        value, accepted = QInputDialog.getText(
            self, "Изменить размер", "Размер:",
            QLineEdit.Normal, self.ozon_custom_sizes[row],
        )
        if not accepted:
            return
        try:
            value = validate_custom_size(value, self.ozon_custom_sizes, skip=row)
        except ValueError as error:
            QMessageBox.warning(self, "Некорректный размер", str(error))
            return
        values = list(self.ozon_custom_sizes)
        values[row] = value
        self._save_ozon_sizes(values, row)

    def _delete_ozon_size(self):
        row = self.ozon_custom_sizes_list.currentRow()
        if row < 0:
            return
        values = list(self.ozon_custom_sizes)
        del values[row]
        self._save_ozon_sizes(values, row)

    def _create_personalization_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel("Персонализация")
        title.setObjectName("settingsTitle")
        layout.addWidget(title)

        hint = QLabel("Общее поведение программы для повседневной работы")
        hint.setObjectName("settingsSubtitle")
        layout.addWidget(hint)

        behavior_group = QGroupBox("Поведение поставок")
        behavior_group.setProperty("settingsCard", True)
        behavior_layout = QVBoxLayout(behavior_group)
        behavior_layout.setSpacing(12)

        self.auto_expand_delivery_checkbox = QCheckBox(
            "Автоматически раскрывать поставку после загрузки"
        )
        self.auto_expand_delivery_checkbox.setChecked(bool(
            self.app_config.get("personalization_auto_expand_delivery", True)
        ))
        behavior_layout.addWidget(self.auto_expand_delivery_checkbox)

        self.open_results_folder_checkbox = QCheckBox(
            "Автоматически открывать папку результатов после завершения"
        )
        self.open_results_folder_checkbox.setChecked(bool(
            self.app_config.get("personalization_open_results_folder", False)
        ))
        behavior_layout.addWidget(self.open_results_folder_checkbox)

        self.append_store_name_checkbox = QCheckBox(
            "Подписывать название магазина в [] в конце названия папки поставки"
        )
        self.append_store_name_checkbox.setChecked(bool(
            self.app_config.get(
                "personalization_append_store_to_delivery_folder", False
            )
        ))
        store_suffix_layout = QHBoxLayout()
        store_suffix_layout.addWidget(self.append_store_name_checkbox, 1)
        self.store_suffix_exclusions_button = QPushButton()
        self.store_suffix_exclusions_button.clicked.connect(
            self._edit_store_suffix_exclusions
        )
        store_suffix_layout.addWidget(self.store_suffix_exclusions_button)
        behavior_layout.addLayout(store_suffix_layout)
        self._update_store_suffix_exclusions_button()

        self.store_names_button = QPushButton("Названия магазинов")
        self.store_names_button.clicked.connect(self._edit_store_display_names)
        behavior_layout.addWidget(self.store_names_button)

        self.logs_button = QPushButton("Логи")
        self.logs_button.clicked.connect(self._open_logs)
        behavior_layout.addWidget(self.logs_button)

        layout.addWidget(behavior_group)
        layout.addStretch()

        for checkbox in (
            self.auto_expand_delivery_checkbox,
            self.open_results_folder_checkbox,
            self.append_store_name_checkbox,
        ):
            checkbox.toggled.connect(self._save_personalization_settings)
        self.append_store_name_checkbox.toggled.connect(
            self._update_store_suffix_exclusions_button
        )
        return tab

    def _display_store_name(self, store_name):
        display_name = str(self.store_display_names.get(store_name, "")).strip()
        return display_name or store_name
    def _update_store_suffix_exclusions_button(self, *_):
        valid_exclusions = self.store_suffix_exclusions.intersection(self.stores)
        count = len(valid_exclusions)
        text = "Кроме…" if count == 0 else f"Кроме… ({count})"
        self.store_suffix_exclusions_button.setText(text)
        self.store_suffix_exclusions_button.setEnabled(
            self.append_store_name_checkbox.isChecked()
        )

    def _edit_store_suffix_exclusions(self):
        dialog = StoreSuffixExclusionsDialog(
            self.stores,
            self._display_store_name,
            self.store_suffix_exclusions,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dialog.selected_store_names()
        config = self._load_app_config()
        config["personalization_store_suffix_exclusions"] = [
            store_name
            for store_name in self.stores
            if store_name in selected
        ]
        try:
            atomic_write_json(self.config_file, config)
        except OSError as error:
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось сохранить исключения магазинов: {error}",
            )
            return

        self.app_config = config
        self.store_suffix_exclusions = set(
            config["personalization_store_suffix_exclusions"]
        )
        self._update_store_suffix_exclusions_button()
    def _open_logs(self):

        from gui.dialogs.log_window import LogWindow

        LogWindow(self).exec()

    def _edit_store_display_names(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Названия магазинов")
        dialog.resize(620, 440)
        layout = QVBoxLayout(dialog)

        hint = QLabel(
            "Оригинальные названия используются внутренними алгоритмами. "
            "Изменяется только отображение в интерфейсе и в суффиксе папки."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table = QTableWidget(len(self.stores), 2, dialog)
        table.setHorizontalHeaderLabels([
            "Оригинальное название", "Отображаемое название"
        ])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )

        for row, store_name in enumerate(self.stores):
            original_item = QTableWidgetItem(store_name)
            original_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            table.setItem(row, 0, original_item)
            table.setItem(
                row, 1,
                QTableWidgetItem(self._display_store_name(store_name))
            )
        layout.addWidget(table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        aliases = {}
        for row in range(table.rowCount()):
            original = table.item(row, 0).text().strip()
            display_item = table.item(row, 1)
            display = display_item.text().strip() if display_item else ""
            if display and display != original:
                aliases[original] = display

        self.app_config = self._load_app_config()
        self.app_config["store_display_names"] = aliases
        try:
            atomic_write_json(self.config_file, self.app_config)
        except OSError as error:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось сохранить названия: {error}"
            )
            return
        self.store_display_names = aliases
        self.refresh_store_buttons()
        if self.selected_store:
            self.store_title.setText(
                f"Настройки: {self._display_store_name(self.selected_store)}"
            )

    def _save_personalization_settings(self, *_):
        self.app_config = self._load_app_config()
        self.app_config.update({
            "personalization_auto_expand_delivery": (
                self.auto_expand_delivery_checkbox.isChecked()
            ),
            "personalization_open_results_folder": (
                self.open_results_folder_checkbox.isChecked()
            ),
            "personalization_append_store_to_delivery_folder": (
                self.append_store_name_checkbox.isChecked()
            ),
            "personalization_store_suffix_exclusions": [
                store_name
                for store_name in self.stores
                if store_name in self.store_suffix_exclusions
            ],
        })
        try:
            atomic_write_json(self.config_file, self.app_config)
        except OSError as error:
            print(f"[SETTINGS] Не удалось сохранить персонализацию: {error}")

    def _create_yandex_tab(self):
        """Настройка интеграции с Яндекс Диском."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        title = QLabel("Яндекс Диск")
        title.setObjectName("settingsTitle")
        layout.addWidget(title)

        if sys.platform.startswith("win"):
            unavailable = QLabel(
                "Яндекс Диск временно недоступен в Windows-версии.\n"
                "Используйте локальную или сетевую папку Windows."
            )
            unavailable.setObjectName("unavailableNotice")
            unavailable.setWordWrap(True)
            layout.addWidget(unavailable)
            layout.addStretch()
            return tab

        hint = QLabel(
            "Подключите Яндекс Диск, чтобы выбирать облачные папки и искать принты."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.yandex_token_input = QLineEdit()
        self.yandex_token_input.setPlaceholderText("OAuth-токен Яндекс Диска")
        self.yandex_token_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.yandex_token_input)
        buttons = QHBoxLayout()
        authorize = QPushButton("Получить токен")
        authorize.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(build_authorization_url()))
        )
        buttons.addWidget(authorize)
        apply_token = QPushButton("Подключить")
        apply_token.setProperty("accent", True)
        apply_token.clicked.connect(self._apply_yandex_token)
        buttons.addWidget(apply_token)
        check_connection = QPushButton("Проверить подключение")
        check_connection.clicked.connect(self._check_yandex_connection)
        buttons.addWidget(check_connection)
        delete_token = QPushButton("Удалить токен")
        delete_token.setProperty("destructive", True)
        delete_token.clicked.connect(self._delete_yandex_token)
        buttons.addWidget(delete_token)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.yandex_status = QLabel(
            "Сохранённый токен будет использован автоматически"
            if load_saved_yandex_token() else "Не подключено"
        )
        layout.addWidget(self.yandex_status)
        layout.addStretch()
        return tab
    def _load_app_config(self):
        if not os.path.exists(self.config_file):
            return {}
        try:
            with open(self.config_file, "r", encoding="utf-8") as file:
                config = json.load(file)
                if isinstance(config, dict) and sys.platform.startswith("win"):
                    config.pop("yandex_oauth_token", None)
                return config
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_background_settings(self, *_):
        # Перечитываем файл, чтобы не затереть сохранённый OAuth-токен.
        self.app_config = self._load_app_config()
        self.app_config.update({
            "cloud_background_index_enabled": (
                self.cloud_background_enabled.isChecked()
            ),
            "cloud_background_index_time": (
                self.cloud_background_time.time().toString("HH:mm")
            ),
        })
        try:
            atomic_write_json(self.config_file, self.app_config)
        except OSError as error:
            print(f"[INDEX AUTO] Не удалось сохранить расписание: {error}")

    def _apply_yandex_token(self):
        token = self.yandex_token_input.text().strip()
        if not token:
            if load_saved_yandex_token():
                self.yandex_status.setText("Используется сохранённый токен")
                return True
            self.yandex_status.setText("Введите OAuth-токен")
            return False
        try:
            configure_yandex_token(token, persist=True)
        except YandexDiskError as error:
            self.yandex_status.setText(f"Токен применён, но не сохранён: {error}")
            return False
        self.yandex_token_input.clear()
        self.yandex_token_input.setPlaceholderText(
            "Токен сохранён в config.json"
        )
        self.yandex_status.setText("Токен сохранён и применён")
        return True

    def _check_yandex_connection(self):
        if not self._apply_yandex_token():
            return
        try:
            YandexDiskClient().list_directory("disk:/")
        except YandexDiskError as error:
            self.yandex_status.setText(f"Ошибка подключения: {error}")
        else:
            self.yandex_status.setText("Подключение к Яндекс Диску установлено")

    def _delete_yandex_token(self):
        delete_saved_yandex_token()
        self.yandex_token_input.clear()
        self.yandex_token_input.setPlaceholderText("OAuth-токен Яндекс Диска")
        self.yandex_status.setText("Сохранённый токен удалён")

    def _clear_inline_styles(self):
        """Оставляет оформление глобальной системной теме, а не жёстким цветам."""
        for widget in self.findChildren(QWidget):
            widget.setStyleSheet("")

    @staticmethod
    def _restore_scroll_value(scroll_area, value):
        scroll_bar = scroll_area.verticalScrollBar()
        scroll_bar.setValue(min(value, scroll_bar.maximum()))

    def _update_store_button_selection(self):
        for store_name, button in self.store_buttons.items():
            selected = store_name == self.selected_store
            if button.property("selected") != selected:
                button.setProperty("selected", selected)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()

    def refresh_store_buttons(self):
        scroll_value = self.stores_scroll_area.verticalScrollBar().value()

        while self.stores_layout.count():
            item = self.stores_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        self.store_buttons = {}
        for store_name in self.stores.keys():
            button = QPushButton(self._display_store_name(store_name))
            button.setFixedHeight(30)
            button.setProperty("sidebarItem", True)
            button.setProperty("selected", store_name == self.selected_store)
            button.clicked.connect(
                lambda checked=False, name=store_name: self.select_store(name)
            )
            self.store_buttons[store_name] = button
            self.stores_layout.addWidget(button)

        self.stores_layout.addStretch(1)
        QTimer.singleShot(
            0,
            lambda value=scroll_value: self._restore_scroll_value(
                self.stores_scroll_area, value
            ),
        )

    def select_store(self, store_name):
        left_scroll_value = self.stores_scroll_area.verticalScrollBar().value()
        right_scroll_value = self.settings_scroll.verticalScrollBar().value()
        self.selected_store = store_name
        self.store_title.setText(
            f"Настройки: {self._display_store_name(store_name)}"
        )
        self.update_store_index_button.setEnabled(True)

        self.refresh_types_for_store(store_name)
        self.update_overrides_stats()
        self._clear_inline_styles()
        self._update_store_button_selection()
        QTimer.singleShot(
            0,
            lambda value=left_scroll_value: self._restore_scroll_value(
                self.stores_scroll_area, value
            ),
        )
        QTimer.singleShot(
            0,
            lambda value=right_scroll_value: self._restore_scroll_value(
                self.settings_scroll, value
            ),
        )

    def refresh_types_for_store(self, store_name):
        while self.types_layout.count():
            item = self.types_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        if not store_name or store_name not in self.stores:
            return

        store_data = self.stores[store_name]

        if store_name not in self.type_vars:
            self.type_vars[store_name] = {}

        existing_types = list(store_data.keys())
        for supply_type in existing_types:
            type_data = store_data[supply_type]
            self.create_type_section(supply_type, type_data)

        for supply_type in ("WB", "OZON"):
            if supply_type not in store_data:
                add_button = QPushButton("+")
                add_button.setProperty("compact", True)
                add_button.setFixedSize(34, 30)
                add_button.setToolTip(f"Добавить настройки {supply_type}")
                add_button.clicked.connect(
                    lambda _checked=False, kind=supply_type: self.add_type_to_store(kind)
                )
                self.types_layout.addWidget(add_button, alignment=Qt.AlignLeft)
        self._clear_inline_styles()

    def add_type_to_store(self, supply_type):
        """Создаёт карточку отсутствующего формата только по нажатию пользователя."""
        store_name = self.selected_store
        if not store_name or supply_type in self.stores.get(store_name, {}):
            return
        values = {
            "path": "",
            "matcher": "Simple",
            "copy_on_override": False,
            "override_path": "",
            "source": "local",
            "png_only": False,
            "size_ranges": [],
            "additional_sources": [],
        }
        self.stores[store_name][supply_type] = values.copy()
        self.type_vars.setdefault(store_name, {})[supply_type] = values.copy()
        self._auto_save()
        self.refresh_types_for_store(store_name)

    def create_type_section(self, supply_type, type_data):
        store_name = self.selected_store

        if supply_type not in self.type_vars[store_name]:
            self.type_vars[store_name][supply_type] = {
                "path": type_data.get("path", ""),
                "matcher": resolve_matcher_name(
                    f"{store_name}_{supply_type}",
                    type_data.get("matcher"),
                ),
                "copy_on_override": type_data.get("copy_on_override", False),
                "override_path": type_data.get("override_path", ""),
                "source": type_data.get("source", "local"),
                "png_only": bool(type_data.get("png_only", False)),
                "size_ranges": deepcopy(type_data.get("size_ranges", [])),
                "additional_sources": deepcopy(
                    type_data.get("additional_sources", [])
                ),
            }

        vars_dict = self.type_vars[store_name][supply_type]

        group = QGroupBox(f"  {supply_type}  ")
        group.setProperty("settingsCard", True)
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                margin-top: 12px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)

        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)

        # Заголовок с кнопкой удаления
        header_layout = QHBoxLayout()
        header_label = QLabel(f"Настройки {supply_type}")
        header_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        delete_button = QPushButton("×")
        delete_button.setProperty("destructive", True)
        delete_button.setProperty("compact", True)
        delete_button.setFixedSize(28, 26)
        delete_button.setToolTip(f"Удалить тип {supply_type}")
        delete_button.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                font-weight: bold;
                font-size: 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
            QPushButton:pressed {
                background-color: #B71C1C;
            }
        """)
        delete_button.clicked.connect(
            lambda checked, t=supply_type: self.remove_type_from_store(t)
        )
        header_layout.addWidget(delete_button)

        group_layout.addLayout(header_layout)

        source_layout = QHBoxLayout()
        source_label = QLabel("Где искать файлы:")
        source_label.setFixedWidth(120)
        source_layout.addWidget(source_label)
        source_combo = QComboBox()
        source_combo.addItem("Локальная папка", "local")
        if not sys.platform.startswith("win"):
            source_combo.addItem("Яндекс Диск", "yandex")
        elif vars_dict["source"] == "yandex":
            source_combo.addItem("Яндекс Диск (недоступен)", "yandex")
            source_combo.setEnabled(False)
        source_combo.setCurrentIndex(0 if vars_dict["source"] == "local" else 1)
        source_combo.currentIndexChanged.connect(
            lambda _index, st=supply_type, combo=source_combo: self._on_source_changed(
                st, combo.currentData()
            )
        )
        source_layout.addWidget(source_combo, 1)
        group_layout.addLayout(source_layout)

        matcher_layout = QHBoxLayout()
        matcher_label = QLabel("Алгоритм поиска:")
        matcher_label.setFixedWidth(120)
        matcher_layout.addWidget(matcher_label)
        matcher_combo = QComboBox()
        for matcher_name, matcher_text in get_matcher_options():
            matcher_combo.addItem(matcher_text, matcher_name)
        matcher_index = matcher_combo.findData(vars_dict["matcher"])
        matcher_combo.setCurrentIndex(max(0, matcher_index))
        matcher_combo.setToolTip(
            "Алгоритм применяется только к этому магазину и типу поставки. "
            "Переиндексация файлов не требуется."
        )
        matcher_combo.currentIndexChanged.connect(
            lambda _index, st=supply_type, combo=matcher_combo: (
                self._on_matcher_changed(st, combo.currentData())
            )
        )
        matcher_layout.addWidget(matcher_combo, 1)
        group_layout.addLayout(matcher_layout)

        ranges_layout = QHBoxLayout()
        ranges_label = QLabel("Размерные файлы:")
        ranges_label.setFixedWidth(120)
        ranges_layout.addWidget(ranges_label)
        ranges_button = QPushButton(
            f"Диапазоны размеров… ({len(vars_dict.get('size_ranges', []))})"
        )
        ranges_button.clicked.connect(
            lambda _checked=False, st=supply_type: self._edit_size_ranges(st)
        )
        ranges_layout.addWidget(ranges_button, 1)
        group_layout.addLayout(ranges_layout)

        # Папка
        folder_layout = QHBoxLayout()
        folder_label = QLabel("Папка с принтами:")
        folder_label.setFixedWidth(120)
        folder_label.setStyleSheet("font-size: 12px;")
        folder_layout.addWidget(folder_label)

        path_input = QLineEdit(vars_dict["path"])
        path_input.setReadOnly(True)
        path_input.setStyleSheet("""
            QLineEdit {
                background-color: #F5F5F5;
                padding: 5px 8px;
                border: 1px solid #CCCCCC;
                border-radius: 3px;
                font-size: 12px;
            }
        """)
        folder_layout.addWidget(path_input, 1)

        browse_button = QPushButton("...")
        browse_button.setProperty("compact", True)
        browse_button.setFixedSize(36, 28)
        browse_button.setToolTip("Выбрать папку")
        browse_button.setStyleSheet("""
            QPushButton {
                background-color: #F5F5F5;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
                color: #424242;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
                border-color: #9E9E9E;
            }
            QPushButton:pressed {
                background-color: #D5D5D5;
            }
        """)
        browse_button.clicked.connect(
            lambda checked, st=supply_type: self.browse_folder(st)
        )
        folder_layout.addWidget(browse_button)

        group_layout.addLayout(folder_layout)

        additional_layout = QHBoxLayout()
        additional_label = QLabel("Доп. источники:")
        additional_label.setFixedWidth(120)
        additional_layout.addWidget(additional_label)
        additional_button = QPushButton(
            f"Дополнительные источники… "
            f"({len(vars_dict.get('additional_sources', []))})"
        )
        additional_button.clicked.connect(
            lambda _checked=False, st=supply_type: self._edit_additional_sources(st)
        )
        additional_layout.addWidget(additional_button, 1)
        group_layout.addLayout(additional_layout)

        # Разделитель
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        group_layout.addWidget(separator)

        # Настройки уточнения — ОДИН чекбокс
        png_only_checkbox = QCheckBox("Искать только PNG")
        png_only_checkbox.setObjectName("pngOnly")
        png_only_checkbox.setChecked(bool(vars_dict.get("png_only", False)))
        png_only_checkbox.setToolTip(
            "Автоматический поиск будет учитывать только файлы PNG. "
            "Ручные уточнения других форматов останутся доступными."
        )
        png_only_checkbox.toggled.connect(
            lambda checked, st=supply_type: self._on_png_only_toggled(st, checked)
        )
        group_layout.addWidget(png_only_checkbox)

        override_title = QLabel("Настройки уточнения:")
        override_title.setStyleSheet("font-weight: 600; font-size: 12px;")
        group_layout.addWidget(override_title)

        # ✅ Только один чекбокс — "Копировать и переименовывать"
        copy_checkbox = QCheckBox("Копировать и переименовывать при уточнении")
        copy_checkbox.setObjectName("copyOnOverride")
        copy_checkbox.setChecked(vars_dict["copy_on_override"])
        copy_checkbox.setStyleSheet("font-size: 12px;")
        copy_checkbox.setToolTip(
            "Если включено:\n"
            "• Файл извне → копируется в подпапку overrided с именем артикула\n"
            "• Файл уже в папке магазина → переименовывается на месте в имя артикула"
        )
        copy_checkbox.toggled.connect(
            lambda checked, st=supply_type: self._on_copy_toggled(st, checked)
        )
        group_layout.addWidget(copy_checkbox)

        # Путь для копирования показываем только в включённом режиме.
        override_container = QWidget()
        override_layout = QHBoxLayout(override_container)
        override_layout.setContentsMargins(0, 0, 0, 0)
        override_label = QLabel("Путь для копирования:")
        override_label.setFixedWidth(120)
        override_label.setStyleSheet("font-size: 12px;")
        override_layout.addWidget(override_label)

        override_input = QLineEdit(vars_dict["override_path"])
        override_input.setStyleSheet("""
            QLineEdit {
                padding: 5px 8px;
                border: 1px solid #CCCCCC;
                border-radius: 3px;
                font-size: 12px;
            }
        """)
        override_input.textChanged.connect(
            lambda text, st=supply_type: self._on_override_text_changed(st, text)
        )
        override_layout.addWidget(override_input, 1)

        override_browse = QPushButton("...")
        override_browse.setProperty("compact", True)
        override_browse.setFixedSize(36, 28)
        override_browse.setToolTip("Выбрать папку")
        override_browse.setStyleSheet("""
            QPushButton {
                background-color: #F5F5F5;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
                color: #424242;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
                border-color: #9E9E9E;
            }
        """)
        override_browse.clicked.connect(
            lambda checked, st=supply_type: self.browse_override_path(st)
        )
        override_layout.addWidget(override_browse)

        group_layout.addWidget(override_container)
        override_container.setVisible(vars_dict["copy_on_override"])
        copy_checkbox.toggled.connect(override_container.setVisible)

        # В облаке файл нельзя безопасно переименовать или скопировать на место:
        # при уточнении сохраняется только его облачный путь.
        is_cloud_source = vars_dict.get("source") == "yandex"
        separator.setVisible(not is_cloud_source)
        override_title.setVisible(not is_cloud_source)
        copy_checkbox.setVisible(not is_cloud_source)
        override_container.setVisible(
            not is_cloud_source and vars_dict["copy_on_override"]
        )

        self.types_layout.addWidget(group)

    # ============================================
    # Обработчики изменений
    # ============================================

    def _on_matcher_changed(self, supply_type, matcher_name):
        store_name = self.selected_store
        if (
            store_name in self.type_vars
            and supply_type in self.type_vars[store_name]
        ):
            current = self.type_vars[store_name][supply_type].get("matcher")
            if current == matcher_name:
                return
            self.type_vars[store_name][supply_type]["matcher"] = matcher_name
            self.needs_reset_cache = True
            self._auto_save()

    def _edit_size_ranges(self, supply_type):
        store_name = self.selected_store
        vars_dict = self.type_vars.get(store_name, {}).get(supply_type)
        if vars_dict is None:
            return
        dialog = SizeRangesDialog(vars_dict.get("size_ranges", []), self)
        if dialog.exec() == QDialog.Accepted:
            vars_dict["size_ranges"] = deepcopy(dialog.ranges)
            self.needs_reset_cache = True
            self._auto_save()
            self.refresh_types_for_store(store_name)

    def _known_sources(self, current_type):
        result = []
        seen = set()
        current = self.type_vars[self.selected_store][current_type]
        current_key = (
            current.get("source", "local"), current.get("path", "").strip().casefold()
        )
        for store_data in self.stores.values():
            for type_data in store_data.values():
                if not isinstance(type_data, dict):
                    continue
                source = "yandex" if type_data.get("source") == "yandex" else "local"
                path = type_data.get("path", "").strip()
                key = (source, path.casefold())
                if path and key != current_key and key not in seen:
                    seen.add(key)
                    result.append({"source": source, "path": path})
        return result

    def _edit_additional_sources(self, supply_type):
        store_name = self.selected_store
        values = self.type_vars.get(store_name, {}).get(supply_type)
        if values is None:
            return
        dialog = AdditionalSourcesDialog(
            values.get("additional_sources", []),
            self._known_sources(supply_type), self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values["additional_sources"] = normalise_additional_sources(
            dialog.sources, values.get("source"), values.get("path", "")
        )
        self.needs_reset_cache = True
        self._auto_save()
        self.refresh_types_for_store(store_name)

    def _on_png_only_toggled(self, supply_type, checked):
        store_name = self.selected_store
        if store_name in self.type_vars and supply_type in self.type_vars[store_name]:
            self.type_vars[store_name][supply_type]["png_only"] = bool(checked)
            self._auto_save()

    def _on_copy_toggled(self, supply_type, checked):
        store_name = self.selected_store
        if store_name in self.type_vars and supply_type in self.type_vars[store_name]:
            self.type_vars[store_name][supply_type]["copy_on_override"] = checked
            self._auto_save()

    def _on_source_changed(self, supply_type, source):
        store_name = self.selected_store
        if store_name in self.type_vars and supply_type in self.type_vars[store_name]:
            self.type_vars[store_name][supply_type]["source"] = source
            self._auto_save()
            # Облачный источник использует другой, упрощённый набор параметров.
            QTimer.singleShot(0, lambda: self.refresh_types_for_store(store_name))

    def _on_override_text_changed(self, supply_type, text):
        store_name = self.selected_store
        if store_name in self.type_vars and supply_type in self.type_vars[store_name]:
            self.type_vars[store_name][supply_type]["override_path"] = text
            self._save_timer.start()

    def _auto_save(self):
        if not self.selected_store:
            return

        store_name = self.selected_store
        old_store_data = self.stores.get(store_name, {})

        new_store_data = {}
        if store_name in self.type_vars:
            for supply_type, vars_dict in self.type_vars[store_name].items():
                path = vars_dict["path"].strip()
                new_store_data[supply_type] = {
                    "path": path,
                    "matcher": vars_dict.get("matcher", "Simple"),
                    "copy_on_override": vars_dict["copy_on_override"],
                    "override_path": vars_dict["override_path"].strip(),
                    "source": vars_dict.get("source", "local"),
                    "png_only": bool(vars_dict.get("png_only", False)),
                    "size_ranges": deepcopy(vars_dict.get("size_ranges", [])),
                    "additional_sources": normalise_additional_sources(
                        vars_dict.get("additional_sources", []),
                        vars_dict.get("source", "local"), path,
                    ),
                }

        for supply_type in set(list(old_store_data.keys()) + list(new_store_data.keys())):
            old_path = old_store_data.get(supply_type, {}).get("path", "")
            new_path = new_store_data.get(supply_type, {}).get("path", "")

            if old_path and old_path != new_path:
                # Старое состояние сохраняем: при ручном обновлении SQLite
                # вычислит разницу путей без удаления и полной пересборки базы.
                self.needs_reset_cache = True

        self.stores[store_name] = new_store_data
        save_stores(self.stores)

    def browse_folder(self, supply_type):
        vars_dict = self.type_vars[self.selected_store][supply_type]
        if vars_dict.get("source") == "yandex":
            picker = YandexDiskPickerDialog(
                initial_path=vars_dict.get("path", ""), parent=self
            )
            folder = picker.selected_path if picker.exec() == QDialog.Accepted else ""
        else:
            folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if folder:
            store_name = self.selected_store
            self.type_vars[store_name][supply_type]["path"] = folder
            self.refresh_types_for_store(store_name)
            self._auto_save()

    def browse_override_path(self, supply_type):
        vars_dict = self.type_vars[self.selected_store][supply_type]
        if vars_dict.get("source") == "yandex":
            picker = YandexDiskPickerDialog(
                initial_path=vars_dict.get("override_path", ""), parent=self
            )
            folder = picker.selected_path if picker.exec() == QDialog.Accepted else ""
        else:
            folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if folder:
            store_name = self.selected_store
            self.type_vars[store_name][supply_type]["override_path"] = folder
            self.refresh_types_for_store(store_name)
            self._auto_save()

    def remove_type_from_store(self, supply_type):
        if not self.selected_store:
            return

        store_name = self.selected_store

        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Удалить тип '{supply_type}' из магазина '{store_name}'?\n\n"
            f"Индекс и overrides для этого типа будут удалены.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        if store_name in self.stores and supply_type in self.stores[store_name]:
            del self.stores[store_name][supply_type]

            if not self.stores[store_name]:
                reply2 = QMessageBox.question(
                    self, "Внимание",
                    f"У магазина '{store_name}' не осталось типов.\nУдалить магазин полностью?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply2 == QMessageBox.Yes:
                    del self.stores[store_name]
                    self.selected_store = None
                    self.store_title.setText("Выберите магазин")
                    self.refresh_store_buttons()
                    self._clear_types()
                    save_stores(self.stores)
                    prune_unused_indexes(self.stores)
                    return

        remove_index_for_store(f"{store_name}_{supply_type}")

        if os.path.exists(OVERRIDES_FILE):
            try:
                with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                    overrides = json.loads(f.read().strip() or "{}")
                key = f"{store_name}_{supply_type}"
                if key in overrides:
                    del overrides[key]
                    atomic_write_json(OVERRIDES_FILE, overrides)
            except Exception as e:
                print(f"Ошибка при очистке overrides: {e}")

        if store_name in self.type_vars and supply_type in self.type_vars[store_name]:
            del self.type_vars[store_name][supply_type]

        save_stores(self.stores)
        prune_unused_indexes(self.stores)
        self.refresh_types_for_store(store_name)
        self.update_overrides_stats()

    def _clear_types(self):
        while self.types_layout.count():
            item = self.types_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def update_overrides_stats(self):
        if not self.selected_store:
            self.overrides_label.setText("—")
            return

        if not os.path.exists(OVERRIDES_FILE):
            self.overrides_label.setText("Файл overrides не существует")
            return

        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                overrides = json.loads(f.read().strip() or "{}")
        except Exception:
            self.overrides_label.setText("Ошибка чтения файла")
            return

        wb_key = f"{self.selected_store}_WB"
        ozon_key = f"{self.selected_store}_OZON"

        wb_count = len(overrides.get(wb_key, {}))
        ozon_count = len(overrides.get(ozon_key, {}))

        total = wb_count + ozon_count
        if total == 0:
            text = "Уточнений нет"
        else:
            text = f"Всего уточнений: {total}"
            if wb_count:
                text += f"  (WB: {wb_count})"
            if ozon_count:
                text += f"  (OZON: {ozon_count})"

        self.overrides_label.setText(text)

    def add_store(self):
        name, ok = QInputDialog.getText(
            self, "Новый магазин", "Название магазина:"
        )

        if not ok or not name.strip():
            return

        name = name.strip()

        if name in self.stores:
            QMessageBox.warning(
                self, "Ошибка",
                f"Магазин '{name}' уже существует"
            )
            return

        self.stores[name] = {
            "WB": {
                "path": "",
                "copy_on_override": False,
                "override_path": ""
            }
        }
        save_stores(self.stores)
        self.refresh_store_buttons()
        self.select_store(name)

    def delete_store(self):
        if not self.selected_store:
            QMessageBox.warning(self, "Ошибка", "Выберите магазин")
            return

        store_name = self.selected_store

        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Удалить магазин '{store_name}'?\n\n"
            f"Все настройки, индексы и overrides будут удалены.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        del self.stores[store_name]
        save_stores(self.stores)
        prune_unused_indexes(self.stores)

        if os.path.exists(OVERRIDES_FILE):
            try:
                with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                    overrides = json.loads(f.read().strip() or "{}")
                keys_to_remove = [k for k in overrides if k.startswith(f"{store_name}_")]
                for key in keys_to_remove:
                    del overrides[key]
                if keys_to_remove:
                    atomic_write_json(OVERRIDES_FILE, overrides)
            except Exception as e:
                print(f"Ошибка при очистке overrides: {e}")

        for suffix in ["WB", "OZON"]:
            remove_index_for_store(f"{store_name}_{suffix}")

        if store_name in self.type_vars:
            del self.type_vars[store_name]

        self.selected_store = None
        self.store_title.setText("Выберите магазин")
        self._clear_types()
        self.update_store_index_button.setEnabled(False)

        self.refresh_store_buttons()
        if self.stores:
            self.select_store(next(iter(self.stores.keys())))

    def _index_tasks(self, store_names):
        tasks_by_source = {}
        for store_name in store_names:
            store_data = self.stores.get(store_name, {})
            for supply_type, type_data in store_data.items():
                if sys.platform.startswith("win") and type_data.get("source") == "yandex":
                    print(f"[INDEX] Пропущен {store_name} ({supply_type}): Яндекс Диск недоступен")
                    continue
                primary = type_data.get("path", "").strip()
                if not primary:
                    print(f"[INDEX] Пропущен {store_name} ({supply_type}): папка не указана")
                    continue
                sources = [{
                    "source": type_data.get("source", "local"),
                    "path": primary,
                }]
                if type_data.get("copy_on_override"):
                    override = type_data.get("override_path", "").strip()
                    if override:
                        sources.insert(0, {"source": "local", "path": override})
                sources.extend(type_data.get("additional_sources", []))
                for source in sources:
                    source_type = source.get("source", "local")
                    folder = source.get("path", "").strip()
                    if not folder:
                        continue
                    spec = normalise_source(source_type, folder)
                    task = tasks_by_source.setdefault(spec["key"], {
                        "key": f"source:{spec['key']}",
                        "label": folder,
                        "folder": folder,
                        "source": source_type,
                        "shared_only": True,
                        "consumers": [],
                    })
                    task["consumers"].append(
                        f"{self._display_store_name(store_name)} ({supply_type})"
                    )
        for task in tasks_by_source.values():
            count = len(task["consumers"])
            task["label"] = f"{task['folder']} · используется: {count}"
        return list(tasks_by_source.values())

    def update_all_indexes(self):
        self._auto_save()
        self._start_indexing(self._index_tasks(self.stores.keys()))

    def update_selected_store_indexes(self):
        if not self.selected_store:
            QMessageBox.warning(self, "Индексация", "Выберите магазин")
            return
        self._auto_save()
        self._start_indexing(self._index_tasks([self.selected_store]))

    def _start_indexing(self, tasks):
        if not tasks:
            QMessageBox.information(
                self, "Индексация", "Нет настроенных папок для индексации"
            )
            return

        self.update_all_indexes_button.setEnabled(False)
        self.update_store_index_button.setEnabled(False)
        self.delete_store_button.setEnabled(False)

        self.index_dialog = IndexProgressDialog(len(tasks), self)
        self.index_worker = SettingsIndexWorker(tasks)
        signals = self.index_worker.signals
        signals.store_started.connect(self.index_dialog.store_started)
        signals.folder_scanned.connect(self.index_dialog.folder_scanned)
        signals.store_finished.connect(self.index_dialog.store_finished)
        signals.finished.connect(self._indexing_finished)
        signals.error.connect(self._indexing_error)
        self.index_dialog.show()
        self.index_thread_pool.start(self.index_worker)

    def _set_index_buttons_enabled(self, enabled):
        self.update_all_indexes_button.setEnabled(enabled)
        self.update_store_index_button.setEnabled(enabled and bool(self.selected_store))
        self.delete_store_button.setEnabled(enabled and bool(self.selected_store))

    def _indexing_finished(self):
        self.needs_reset_cache = True
        self._set_index_buttons_enabled(True)
        if self.index_dialog:
            self.index_dialog.finish()
            dialog = self.index_dialog
            QTimer.singleShot(900, dialog.accept)

    def _indexing_error(self, error_message):
        self._set_index_buttons_enabled(True)
        if self.index_dialog:
            self.index_dialog.accept()
        QMessageBox.critical(
            self, "Ошибка индексации",
            f"Индексация не завершена:\n{error_message}\n\n"
            "Подробности сохранены в логах."
        )

    def view_overrides(self):
        if not self.selected_store:
            return

        if not os.path.exists(OVERRIDES_FILE):
            QMessageBox.information(self, "Overrides", "Файл overrides не существует")
            return

        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                overrides = json.loads(f.read().strip() or "{}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать файл: {e}")
            return

        wb_key = f"{self.selected_store}_WB"
        ozon_key = f"{self.selected_store}_OZON"

        wb_overrides = overrides.get(wb_key, {})
        ozon_overrides = overrides.get(ozon_key, {})

        if not wb_overrides and not ozon_overrides:
            QMessageBox.information(self, "Overrides", "Уточнений для этого магазина нет")
            return

        view_dialog = QDialog(self)
        view_dialog.setWindowTitle(f"Overrides: {self.selected_store}")
        view_dialog.setGeometry(150, 150, 700, 500)

        layout = QVBoxLayout(view_dialog)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("""
            QTextEdit {
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                background-color: #1E1E1E;
                color: #D4D4D4;
                border: none;
            }
        """)

        content = ""
        if wb_overrides:
            content += f"=== WB ({len(wb_overrides)} шт.) ===\n\n"
            for key, path in wb_overrides.items():
                content += f"{key}:\n  {path}\n\n"

        if ozon_overrides:
            content += f"\n=== OZON ({len(ozon_overrides)} шт.) ===\n\n"
            for key, path in ozon_overrides.items():
                content += f"{key}:\n  {path}\n\n"

        text_edit.setPlainText(content)
        layout.addWidget(text_edit)

        close_button = QPushButton("Закрыть")
        close_button.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #F5F5F5;
            }
        """)
        close_button.clicked.connect(view_dialog.close)
        layout.addWidget(close_button)

        view_dialog.exec()

    def clear_overrides(self):
        if not self.selected_store:
            return

        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Удалить все ручные уточнения для '{self.selected_store}'?\n\n"
            f"Это действие необратимо.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        if not os.path.exists(OVERRIDES_FILE):
            return

        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                overrides = json.loads(f.read().strip() or "{}")

            wb_key = f"{self.selected_store}_WB"
            ozon_key = f"{self.selected_store}_OZON"

            removed = 0
            if wb_key in overrides:
                removed += len(overrides[wb_key])
                del overrides[wb_key]
            if ozon_key in overrides:
                removed += len(overrides[ozon_key])
                del overrides[ozon_key]
            atomic_write_json(OVERRIDES_FILE, overrides)

            QMessageBox.information(self, "Готово", f"Удалено уточнений: {removed}")
            self.update_overrides_stats()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось очистить: {e}")
