import os
import ntpath
import json
import shutil
import sys
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QToolButton, QFrame, QLabel, QFileDialog, QMessageBox, QSizePolicy,
    QDialog, QProgressDialog, QLineEdit, QDialogButtonBox,
)
from PySide6.QtCore import (
    Qt, QDateTime, QTime, QUrl, QRunnable, QThreadPool, Signal, QObject, Slot,
    QTimer, QStandardPaths,
)
from PySide6.QtGui import QAction, QDesktopServices
from core.paths import get_data_path
from core.json_storage import atomic_write_json
from core.cancellation import CancellationToken, OperationCancelled
from core.personalization import (
    normalise_store_suffix_exclusions,
    should_append_store_suffix,
)
from gui.widgets.supply_tree import (
    SupplyTreeView, SupplyTreeModel, SupplyDeliveryItem, SupplyArticleItem,
)
from gui.widgets.override_delegate import OverrideButtonDelegate
from gui.dialogs.parse_progress_dialog import ParseProgressDialog
from gui.dialogs.yandex_disk_picker import YandexDiskPickerDialog
from core.store_manager import load_stores, save_stores, get_type_data
from core.storage_backend import NetworkReadError, copy_resource
from core.network_paths import is_network_path
from parser import parse_wb_supply
from ozon_parser import parse_ozon_supply
from supply_detector import detect_supply_type
from indexer import (
    get_index_for_source,
    has_source_index,
    has_yandex_index_for_store,
    normalise_source,
    remove_missing_path_from_index,
    update_source_index,
    update_index_for_store,
    update_yandex_indexes,
)
from matcher import get_matcher_for_store
from manual_overrides import get_override, set_override, set_overrides
from article_corrections import get_corrected_article, set_article_correction


# ============================================
# Флаг отмены (разделяется между main thread и workers)
# ============================================


def _system_desktop_path():
    """Return the physical Windows desktop, including OneDrive redirection."""
    desktop = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DesktopLocation
    )
    if not desktop:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return os.path.normpath(os.path.expandvars(os.path.expanduser(desktop)))


def _normalise_output_folder(path):
    """Replace stale cross-platform paths with the current system desktop."""
    value = os.path.normpath(
        os.path.expandvars(os.path.expanduser(str(path or "").strip()))
    )
    if value and os.path.isdir(value):
        return value
    return _system_desktop_path()

def _override_dialog_directory(store_path, delivery_folder):
    """Return the first existing physical folder suitable for file selection."""
    candidates = [
        store_path,
        delivery_folder,
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        ),
        os.path.expanduser("~"),
    ]
    for candidate in candidates:
        raw_value = str(candidate or "").strip()
        raw_value = re.sub(
            r"%([^%]+)%",
            lambda match: os.environ.get(match.group(1), match.group(0)),
            raw_value,
        )
        value = os.path.normpath(
            os.path.expandvars(os.path.expanduser(raw_value))
        )
        if value and os.path.isdir(value):
            return value
    return os.path.normpath(os.path.expanduser("~"))


def _matching_article_indices(delivery_item, article):
    """Return every row in this delivery with the same trimmed article."""
    target = str(article or "").strip()
    return [
        index
        for index in range(delivery_item.child_count())
        if str(delivery_item.child(index).article or "").strip() == target
    ]

def _output_directory_dialog_options():
    """Use filesystem-only folder selection on Windows.

    The native Windows dialog can return a virtual Desktop shell object
    instead of its physical path. Qt's dialog always returns a path.
    """
    options = QFileDialog.Option.ShowDirsOnly
    if sys.platform.startswith("win"):
        options |= QFileDialog.Option.DontUseNativeDialog
    return options

def _sync_not_found_file(output_folder, not_found_items, cancel_token=None):
    """Atomically make ненайденные.txt reflect one completed search."""
    not_found_path = os.path.join(output_folder, "ненайденные.txt")
    temporary_path = not_found_path + ".part"

    if cancel_token:
        cancel_token.raise_if_cancelled()

    if not not_found_items:
        try:
            os.remove(not_found_path)
        except FileNotFoundError:
            pass
        return

    try:
        with open(temporary_path, "w", encoding="utf-8", newline="") as file:
            for position, article, size, location in not_found_items:
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                if size and location:
                    file.write(f"{position}.{article}[{size} {location}]\n")
                elif size:
                    file.write(f"{position}.{article}[{size}]\n")
                else:
                    file.write(f"{position}.{article}\n")
            file.flush()
            os.fsync(file.fileno())
        if cancel_token:
            cancel_token.raise_if_cancelled()
        os.replace(temporary_path, not_found_path)
    except Exception:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise

_WINDOWS_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _safe_filename_component(value, fallback="result", max_length=180):
    """Return one Windows-safe path component; never create nested paths."""
    name = _WINDOWS_INVALID_NAME.sub("_", str(value or ""))
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    name = name[:max_length].rstrip(" .")
    return name or fallback


def _delivery_output_folder_name(pdf_path, store_name, append_store_name):
    folder_name = _safe_filename_component(
        os.path.splitext(ntpath.basename(str(pdf_path)))[0], "Поставка", 120
    )
    if append_store_name and store_name:
        safe_store_name = _safe_filename_component(store_name, "", 80)
        if safe_store_name:
            folder_name = _safe_filename_component(
                f"{folder_name} [{safe_store_name}]", "Поставка", 200
            )
    return folder_name


def _result_file_base_name(file_path, article, manual_override_path):
    """Return a safe flat filename for automatic and manual matches."""
    original_name = os.path.splitext(ntpath.basename(str(file_path)))[0]
    source_name = (
        article
        if manual_override_path and file_path == manual_override_path
        else original_name
    )
    return _safe_filename_component(source_name, "print", 180)

class CancellationFlag(CancellationToken):
    """Backward-compatible parse cancellation flag."""

    def is_canceled(self):
        return self.is_cancelled()


class ArticleCorrectionDialog(QDialog):
    """Edit the factual article while keeping the parsed value visible."""

    def __init__(self, detected_article, factual_article, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Уточнить артикул")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Артикул, прочитанный программой:"))
        detected_label = QLabel(str(detected_article))
        detected_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        detected_label.setWordWrap(True)
        layout.addWidget(detected_label)

        layout.addWidget(QLabel("Фактический артикул:"))
        self.article_input = QLineEdit(str(factual_article))
        self.article_input.selectAll()
        layout.addWidget(self.article_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def factual_article(self):
        return self.article_input.text().strip()


# ============================================
# Сигналы и Worker'ы
# ============================================

class ParseSignals(QObject):
    started = Signal(object, str)       # delivery_item, pdf_path
    finished = Signal(object, list, list, list, str)
    error = Signal(object, str)


class ParseWorker(QRunnable):
    """Worker для парсинга PDF в фоне."""

    def __init__(self, pdf_path, delivery_item, supply_type, cancel_flag):
        super().__init__()
        self.setAutoDelete(True)
        self.pdf_path = pdf_path
        self.delivery_item = delivery_item
        self.supply_type = supply_type
        self.cancel_flag = cancel_flag
        self.signals = ParseSignals()

    @Slot()
    def run(self):
        # Проверяем отмену перед стартом
        if self.cancel_flag.is_canceled():
            return

        try:
            self.signals.started.emit(self.delivery_item, self.pdf_path)

            if self.supply_type == "WB":
                parsed = parse_wb_supply(self.pdf_path)
                articles = parsed["articles"]
                sizes = parsed["sizes"]
                locations = parsed["locations"]
                store_name = parsed.get("store", "UNKNOWN")
            elif self.supply_type == "OZON":
                articles, sizes, locations = parse_ozon_supply(self.pdf_path)
                store_name = "UNKNOWN"
            else:
                raise ValueError(f"Неизвестный тип поставки: {self.supply_type}")

            # Проверяем отмену после парсинга
            if self.cancel_flag.is_canceled():
                return

            self.signals.finished.emit(
                self.delivery_item, articles, sizes, locations, store_name
            )
        except Exception as e:
            if not self.cancel_flag.is_canceled():
                self.signals.error.emit(self.delivery_item, str(e))


class IndexRebuildSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal()
    error = Signal(str)


class IndexRebuildWorker(QRunnable):
    def __init__(self, stores_to_rebuild, cancel_token=None):
        super().__init__()
        self.setAutoDelete(True)
        self.stores_to_rebuild = stores_to_rebuild
        self.cancel_token = cancel_token
        self.signals = IndexRebuildSignals()

    @Slot()
    def run(self):
        try:
            total = len(self.stores_to_rebuild)
            updated_source_keys = set()
            for i, (key, info) in enumerate(self.stores_to_rebuild.items(), start=1):
                if self.cancel_token:
                    self.cancel_token.raise_if_cancelled()
                self.signals.progress.emit(i, total, f"{info['store_name']} ({info['supply_type']})")
                for source in info.get("additional_sources", []):
                    source_type = source.get("source", "local")
                    source_path = source.get("path", "").strip()
                    if not source_path or source_type == "yandex":
                        continue
                    spec = normalise_source(source_type, source_path)
                    if spec["key"] in updated_source_keys:
                        continue
                    update_source_index(
                        source_type, source_path, force_full=False,
                        cancel_token=self.cancel_token,
                    )
                    updated_source_keys.add(spec["key"])
                if info.get("source") == "yandex":
                    print(
                        f"[INDEX] {key}: облачная автоиндексация при поиске "
                        "отключена; используется сохранённый SQLite-индекс"
                    )
                    continue
                update_index_for_store(
                    key, info["folder"],
                    override_path=info.get("override_path"),
                    force=info.get("force", False),
                    cancel_token=self.cancel_token,
                    updated_source_keys=updated_source_keys,
                )
            self.signals.finished.emit()
        except OperationCancelled:
            return
        except Exception as e:
            self.signals.error.emit(str(e))


class CloudAutoIndexSignals(QObject):
    finished = Signal()


class CloudAutoIndexWorker(QRunnable):
    """По расписанию append-only обновляет подготовленные облачные индексы."""

    def __init__(self, cancel_token=None):
        super().__init__()
        self.setAutoDelete(True)
        self.signals = CloudAutoIndexSignals()
        self.cancel_token = cancel_token

    @Slot()
    def run(self):
        stores = load_stores()
        print("[INDEX AUTO] Запущено фоновое append-only обновление облака")
        tasks_by_source = {}
        for store_name, store_data in stores.items():
            for supply_type, type_data in store_data.items():
                candidates = list(type_data.get("additional_sources", []))
                if type_data.get("source", "local") == "yandex":
                    candidates.insert(0, {
                        "source": "yandex", "path": type_data.get("path", "")
                    })
                for source in candidates:
                    if source.get("source") != "yandex":
                        continue
                    cloud_path = source.get("path", "").strip()
                    if not cloud_path:
                        continue
                    spec = normalise_source("yandex", cloud_path)
                    tasks_by_source.setdefault(spec["key"], {
                        "key": f"source:{spec['key']}", "folder": cloud_path,
                    })
        total_cloud_indexes = len(tasks_by_source)
        tasks = []
        for task in tasks_by_source.values():
            if not has_source_index("yandex", task["folder"]):
                print(
                    f"[INDEX AUTO] {task['folder']}: пропущен — сначала "
                    "выполните ручную полную сверку"
                )
                continue
            tasks.append(task)
        try:
            results = update_yandex_indexes(tasks, cancel_token=self.cancel_token)
            changed_count = sum(bool(changed) for changed in results.values())
            skipped_count = total_cloud_indexes - len(results)
            print(
                f"[INDEX AUTO] Обработано {len(results)} из "
                f"{total_cloud_indexes} облачных индексов; "
                f"новые файлы добавлены в {changed_count}; "
                f"пропущено {skipped_count}"
            )
        except Exception as error:
            # Фоновая ошибка не должна останавливать поиск или показывать
            # навязчивое модальное окно пользователю.
            print(f"[INDEX AUTO] Ошибка пакетного обновления: {error}")
            print(
                f"[INDEX AUTO] Обновление выполнено не полностью; "
                f"запланировано индексов: {total_cloud_indexes}"
            )
        print("[INDEX AUTO] Фоновое обновление завершено")
        self.signals.finished.emit()


class SearchSignals(QObject):
    article_status = Signal(object, int, str)
    article_copied = Signal(object, int, str)
    delivery_finished = Signal(object)
    all_finished = Signal()
    error = Signal(str)


class SearchWorker(QRunnable):
    def __init__(
        self,
        delivery_items,
        output_folder,
        matcher_cache,
        append_store_name=False,
        cancel_token=None,
        store_suffix_exclusions=None,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self.delivery_items = delivery_items
        self.output_folder = output_folder
        self.matcher_cache = matcher_cache
        self.append_store_name = append_store_name
        self.cancel_token = cancel_token
        self.store_suffix_exclusions = normalise_store_suffix_exclusions(
            store_suffix_exclusions
        )
        self.signals = SearchSignals()

    @Slot()
    def run(self):
        try:
            for delivery_item in self.delivery_items:
                if self.cancel_token:
                    self.cancel_token.raise_if_cancelled()
                self._process_delivery(delivery_item)
            self.signals.all_finished.emit()
        except OperationCancelled:
            return
        except Exception as e:
            self.signals.error.emit(str(e))

    def _process_delivery(self, delivery_item):
        store_name = delivery_item.store_name
        supply_type = delivery_item.supply_type
        index_store_name = f"{store_name}_{supply_type}"

        type_data = get_type_data(store_name, supply_type)
        if index_store_name not in self.matcher_cache:
            self.matcher_cache[index_store_name] = get_matcher_for_store(
                index_store_name,
                type_data.get("matcher"),
            )
        matcher = self.matcher_cache[index_store_name]
        allowed_extensions = {".png"} if type_data.get("png_only", False) else None

        base_output_folder = os.path.join(self.output_folder, "Найденные_принты_все")
        os.makedirs(base_output_folder, exist_ok=True)

        folder_name = _delivery_output_folder_name(
            delivery_item.pdf_path,
            getattr(delivery_item, "display_store_name", store_name),
            should_append_store_suffix(
                store_name,
                self.append_store_name,
                self.store_suffix_exclusions,
            ),
        )
        output_folder = os.path.join(base_output_folder, folder_name)
        os.makedirs(output_folder, exist_ok=True)

        not_found_items = []
        delivery_item.index_warning = False
        additional_indexes = []
        for source in type_data.get("additional_sources", []):
            source_type = source.get("source", "local")
            source_path = source.get("path", "").strip()
            if not source_path:
                continue
            if source_type != "yandex" and not os.path.isdir(source_path):
                delivery_item.index_warning = True
                print(
                    f"[ПОИСК] Дополнительный источник недоступен: {source_path}"
                )
                continue
            if not has_source_index(source_type, source_path):
                delivery_item.index_warning = True
                print(
                    f"[ПОИСК] Дополнительный источник пропущен: "
                    f"индекс отсутствует ({source_path})"
                )
                continue
            additional_indexes.append(
                (source, get_index_for_source(source_type, source_path))
            )

        for i in range(delivery_item.child_count()):
            if self.cancel_token:
                self.cancel_token.raise_if_cancelled()
            article_item = delivery_item.child(i)
            article = article_item.article
            size = article_item.size
            location = article_item.location

            match_groups = []
            primary_matches = matcher.find_file_by_article_and_size(
                article, size, index_store_name,
                allowed_extensions=allowed_extensions,
                size_ranges=type_data.get("size_ranges", []),
                include_override=False,
            )
            if primary_matches:
                match_groups.append(primary_matches)

            for _source, source_index in additional_indexes:
                source_matches = matcher.find_file_by_article_and_size(
                    article, size, index_store_name,
                    allowed_extensions=allowed_extensions,
                    size_ranges=type_data.get("size_ranges", []),
                    index=source_index,
                    include_override=False,
                )
                if source_matches:
                    match_groups.append(source_matches)

            manual_matches = matcher.find_file_by_article_and_size(
                article, size, index_store_name,
                allowed_extensions=allowed_extensions,
                size_ranges=type_data.get("size_ranges", []),
                index={},
                include_override=True,
            )
            if manual_matches:
                match_groups.append(manual_matches)

            if match_groups:
                copied = False
                last_error = None
                for file in (
                    candidate for group in match_groups for candidate in group
                ):
                    try:
                        if self.cancel_token:
                            self.cancel_token.raise_if_cancelled()
                        _, ext = os.path.splitext(os.path.basename(file))
                        manual_override_path = get_override(
                            store_name, supply_type, article, size
                        )
                        base_name = _result_file_base_name(
                            file, article, manual_override_path
                        )
                        if location == "СПИНА":
                            suffix = f"[{size} СПИНА]" if size else "[СПИНА]"
                        else:
                            suffix = f"[{size}]" if size else ""

                        if suffix:
                            numbered_name = f"{i+1}. {base_name} {suffix}{ext}"
                        else:
                            numbered_name = f"{i+1}. {base_name}{ext}"

                        target_path = os.path.abspath(
                            os.path.join(output_folder, numbered_name)
                        )
                        output_root = os.path.abspath(output_folder)
                        if os.path.commonpath([output_root, target_path]) != output_root:
                            raise ValueError("Недопустимое имя итогового файла")
                        copy_resource(
                            file, target_path, cancel_token=self.cancel_token
                        )
                        self.signals.article_status.emit(
                            delivery_item, i, "Найден"
                        )
                        self.signals.article_copied.emit(
                            delivery_item, i, target_path
                        )
                        copied = True
                        break
                    except OperationCancelled:
                        raise
                    except Exception as error:
                        last_error = error
                        print(f"[ПОИСК] Ошибка копирования {file}: {error}")
                        if isinstance(error, FileNotFoundError):
                            remove_missing_path_from_index(index_store_name, file)

                if not copied:
                    not_found_items.append((i + 1, article, size, location))
                    self.signals.article_status.emit(
                        delivery_item, i,
                        "Ошибка WebDAV"
                        if isinstance(last_error, NetworkReadError)
                        else "Ошибка копирования",
                    )
                    print(
                        f"[ПОИСК] Ни один найденный файл не скопирован для "
                        f"{article}: {last_error}"
                    )
            else:
                not_found_items.append((i + 1, article, size, location))
                self.signals.article_status.emit(delivery_item, i, "Не найден")

        _sync_not_found_file(
            output_folder, not_found_items, cancel_token=self.cancel_token
        )
        self.signals.delivery_finished.emit(delivery_item)


# ============================================
# Главное окно
# ============================================

class MainWindow(QMainWindow):
    CLOUD_AUTO_INDEX_RETRY_MS = 15 * 60 * 1000

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Автосбор принтов по поставкам")
        self.setGeometry(100, 100, 1200, 700)

        self.config_file = get_data_path("config.json")
        self.load_config()

        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(6)
        self.shutdown_token = CancellationToken()
        self._shutdown_started = False
        self._shutdown_dialog = None
        self._shutdown_poll_timer = None
        self._shutdown_deadline_timer = None

        self.pdf_files = []
        self.delivery_items = []
        self.matcher_cache = {}
        self.is_searching = False
        self._pending_restart_item = None
        self._last_toggle_time = 0  # Для debounce кликов

        # ✅ Новое: диалог прогресса и флаг отмены
        self.parse_dialog = None
        self.cancel_flag = CancellationFlag()
        self.active_parse_workers = 0

        self.setup_ui()
        self.setup_toolbar()
        self.setup_drag_drop()
        self._cloud_auto_worker = None
        self.cloud_auto_timer = QTimer(self)
        self.cloud_auto_timer.setSingleShot(True)
        self.cloud_auto_timer.timeout.connect(self._start_cloud_auto_index)
        self._schedule_next_cloud_auto_index()

    def _schedule_next_cloud_auto_index(self):
        """Планирует ежедневное обновление на выбранное пользователем время."""
        self.cloud_auto_timer.stop()
        if sys.platform.startswith("win"):
            self.cloud_background_index_enabled = False
            return
        if not self.cloud_background_index_enabled:
            print("[INDEX AUTO] Фоновое обновление облака отключено в настройках")
            return

        scheduled_time = QTime.fromString(
            self.cloud_background_index_time, "HH:mm"
        )
        if not scheduled_time.isValid():
            scheduled_time = QTime(3, 0)
            self.cloud_background_index_time = "03:00"

        now = QDateTime.currentDateTime()
        next_run = QDateTime(now.date(), scheduled_time)
        if next_run <= now:
            next_run = next_run.addDays(1)
        delay_ms = max(1000, int(now.msecsTo(next_run)))
        self.cloud_auto_timer.start(delay_ms)
        print(
            "[INDEX AUTO] Следующее фоновое обновление облака: "
            f"{next_run.toString('dd.MM.yyyy HH:mm')}"
        )

    def _start_cloud_auto_index(self):
        if sys.platform.startswith("win") or self.shutdown_token.is_cancelled():
            return
        if not self.cloud_background_index_enabled:
            return
        if self.is_searching:
            print("[INDEX AUTO] Поиск занят; обновление отложено на 15 минут")
            self.cloud_auto_timer.start(self.CLOUD_AUTO_INDEX_RETRY_MS)
            return
        self._cloud_auto_worker = CloudAutoIndexWorker(self.shutdown_token)
        self._cloud_auto_worker.signals.finished.connect(
            self._on_cloud_auto_index_finished
        )
        self.thread_pool.start(self._cloud_auto_worker)

    def _on_cloud_auto_index_finished(self):
        self.matcher_cache.clear()
        self._cloud_auto_worker = None
        self._schedule_next_cloud_auto_index()

    # ============================================
    # Конфигурация
    # ============================================

    def load_config(self):
        config = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (OSError, json.JSONDecodeError):
                config = {}
        configured_output_folder = config.get("output_folder", "")
        self.output_folder = _normalise_output_folder(configured_output_folder)
        token_removed = config.pop("yandex_oauth_token", None) is not None
        if token_removed and sys.platform.startswith("win"):
            atomic_write_json(self.config_file, config)
        self.cloud_background_index_enabled = (
            False if sys.platform.startswith("win") else bool(
                config.get("cloud_background_index_enabled", True)
            )
        )
        self.cloud_background_index_time = str(
            config.get("cloud_background_index_time", "03:00")
        )
        self.auto_expand_delivery = bool(
            config.get("personalization_auto_expand_delivery", True)
        )
        self.open_results_folder = bool(
            config.get("personalization_open_results_folder", False)
        )
        self.append_store_to_delivery_folder = bool(config.get(
            "personalization_append_store_to_delivery_folder", False
        ))
        self.store_suffix_exclusions = normalise_store_suffix_exclusions(
            config.get("personalization_store_suffix_exclusions", [])
        )
        self.store_display_names = dict(
            config.get("store_display_names", {}) or {}
        )
        if (
            not os.path.exists(self.config_file)
            or configured_output_folder != self.output_folder
        ):
            self.save_config()

    def save_config(self):
        config = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (OSError, json.JSONDecodeError):
                config = {}
        config.pop("yandex_oauth_token", None)
        config.update({
            "search_folder": "",
            "output_folder": self.output_folder,
            "cloud_background_index_enabled": self.cloud_background_index_enabled,
            "cloud_background_index_time": self.cloud_background_index_time,
            "personalization_auto_expand_delivery": self.auto_expand_delivery,
            "personalization_open_results_folder": self.open_results_folder,
            "personalization_append_store_to_delivery_folder": (
                self.append_store_to_delivery_folder
            ),
            "personalization_store_suffix_exclusions": sorted(
                self.store_suffix_exclusions,
                key=str.casefold,
            ),
            "store_display_names": self.store_display_names,
        })
        atomic_write_json(self.config_file, config)


    # ============================================
    # UI Setup
    # ============================================

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.tree_model = SupplyTreeModel()
        self.tree_view = SupplyTreeView()
        self.tree_view.set_model(self.tree_model)

        self.override_delegate = OverrideButtonDelegate()
        self.override_delegate.override_clicked.connect(self.on_override_clicked)
        self.override_delegate.restart_clicked.connect(self.on_restart_clicked)
        self.tree_view.setItemDelegateForColumn(4, self.override_delegate)

        # ✅ Пункт 1: Клик по строке поставки раскрывает/сворачивает
        self.tree_view.clicked.connect(self.on_tree_clicked)

        layout.addWidget(self.tree_view, stretch=1)

    def on_tree_clicked(self, index):
        """
        Пункт 1: Клик по parent-строке (не в колонке "Действия")
        раскрывает/сворачивает поставку.
        """
        if not index.isValid():
            return

        item = index.internalPointer()
        if isinstance(item, SupplyArticleItem):
            if index.column() == 0:
                self.on_article_clicked(item)
            return
        if not isinstance(item, SupplyDeliveryItem):
            return

        # Колонка 4 = "Действия" — не обрабатываем
        if index.column() == 4:
            return

        # Toggle expand/collapse
        if self.tree_view.isExpanded(index):
            self.tree_view.collapse(index)
        else:
            self.tree_view.expand(index)

    def on_article_clicked(self, article_item):
        """Persist and apply a factual article correction for one store/type."""
        if self.is_searching:
            QMessageBox.information(
                self, "Поиск выполняется",
                "Дождитесь завершения поиска перед уточнением артикула.",
            )
            return

        delivery_item = article_item.parent()
        dialog = ArticleCorrectionDialog(
            article_item.detected_article, article_item.article, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        factual_article = dialog.factual_article()
        if not factual_article:
            QMessageBox.warning(
                self, "Пустой артикул", "Введите фактический артикул.",
            )
            return

        try:
            factual_article = set_article_correction(
                delivery_item.store_name,
                delivery_item.supply_type,
                article_item.detected_article,
                factual_article,
            )
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self, "Ошибка сохранения", f"Не удалось сохранить уточнение: {error}",
            )
            return

        changed_rows = self.tree_model.update_articles_by_detected_article(
            delivery_item, article_item.detected_article, factual_article
        )
        for row in changed_rows:
            self.tree_model.update_article_status(delivery_item, row, "Ожидание")
            delivery_item.child(row).copied_file = None

    def setup_toolbar(self):
        toolbar = QToolBar()
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.addToolBar(toolbar)

        # QToolBar игнорирует часть внутренних отступов на macOS. Обычный
        # контейнер Qt гарантирует ровно 10 px по верхнему и левому краю.
        toolbar_content = QWidget()
        toolbar_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar_layout = QHBoxLayout(toolbar_content)
        toolbar_layout.setContentsMargins(10, 10, 10, 10)
        toolbar_layout.setSpacing(10)
        toolbar.addWidget(toolbar_content)

        def add_action_button(action):
            button = QToolButton(toolbar_content)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toolbar_layout.addWidget(button)

        self.load_action = QAction("📂 Загрузить", self)
        self.load_action.triggered.connect(self.load_pdfs)
        add_action_button(self.load_action)

        self.run_action = QAction("▶️ Запустить", self)
        self.run_action.triggered.connect(self.run_all)
        add_action_button(self.run_action)

        self.clear_action = QAction("🗑 Очистить", self)
        self.clear_action.triggered.connect(self.clear_table)
        add_action_button(self.clear_action)

        separator = QFrame(toolbar_content)
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        toolbar_layout.addWidget(separator)

        self.settings_action = QAction("⚙️ Настройки", self)
        self.settings_action.triggered.connect(self.open_settings)
        add_action_button(self.settings_action)

        toolbar_layout.addStretch()

        self.path_button = QPushButton()
        self.path_button.setObjectName("pathButton")
        self.path_button.setMinimumWidth(200)
        self.path_button.setMaximumWidth(500)
        self.path_button.setToolTip("Нажмите, чтобы изменить папку для найденных принтов")
        self.path_button.clicked.connect(self.change_output_path)
        self._update_path_button_text()
        toolbar_layout.addWidget(self.path_button)

    def _update_path_button_text(self):
        path = self.output_folder
        if len(path) > 60:
            display = "..." + path[-57:]
        else:
            display = path
        self.path_button.setText(f"📁 {display}")
        self.path_button.setToolTip(f"Папка найденных принтов:\n{path}\n\nНажмите для изменения")

    def change_output_path(self):
        initial_folder = _normalise_output_folder(self.output_folder)
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для найденных принтов",
            initial_folder,
            _output_directory_dialog_options(),
        )
        if folder:
            self.output_folder = _normalise_output_folder(folder)
            self._update_path_button_text()
            self.save_config()

    # ============================================
    # Drag & Drop
    # ============================================

    def setup_drag_drop(self):
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        pdf_paths = [
            url.toLocalFile() for url in urls
            if url.toLocalFile().lower().endswith('.pdf')
        ]
        if pdf_paths:
            self.add_pdfs_to_table(pdf_paths)

    # ============================================
    # Загрузка PDF
    # ============================================

    def load_pdfs(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите PDF-файлы поставок", "", "PDF files (*.pdf)"
        )
        if files:
            self.add_pdfs_to_table(files)

    def add_pdfs_to_table(self, pdf_paths):
        """Добавляет PDF с показом диалога прогресса."""
        from PySide6.QtWidgets import QApplication

        # Сбрасываем флаг отмены
        self.cancel_flag = CancellationFlag()

        stores = load_stores()
        workers_to_start = []

        # Подготовка данных
        for pdf_path in pdf_paths:
            if pdf_path in self.pdf_files:
                continue

            QApplication.processEvents()

            detected = detect_supply_type(pdf_path)
            store_name = detected["store_name"]
            supply_type = detected["supply_type"]

            store_data = stores.get(store_name, {})
            type_data = store_data.get(supply_type) if isinstance(store_data, dict) else None

            if type_data:
                folder = type_data["path"]
            else:
                from gui.dialogs.new_store_dialog import NewStoreDialog
                existing_types = list(store_data.keys()) if isinstance(store_data, dict) else []

                QApplication.processEvents()
                dialog = NewStoreDialog(
                    self, pdf_path,
                    existing_store=bool(store_data),
                    existing_types=existing_types
                )

                if dialog.exec():
                    store_name = dialog.result["store_name"]
                    supply_type = dialog.result["supply_type"]
                    folder = dialog.result["folder"]
                    stores = load_stores()
                else:
                    continue

            self.pdf_files.append(pdf_path)

            delivery_item = self.tree_model.add_delivery(
                pdf_path,
                store_name,
                supply_type,
                folder,
                self._display_store_name(store_name),
            )
            self.tree_model.update_delivery_status(delivery_item, "Парсинг...")
            self.delivery_items.append(delivery_item)

            worker = ParseWorker(pdf_path, delivery_item, supply_type, self.cancel_flag)
            worker.signals.started.connect(self._on_parse_started)
            worker.signals.finished.connect(self._on_parse_finished)
            worker.signals.error.connect(self._on_parse_error)
            workers_to_start.append((worker, pdf_path))

            QApplication.processEvents()

        if not workers_to_start:
            return

        # ✅ Пункт 2: Создаём диалог прогресса
        self.active_parse_workers = len(workers_to_start)
        self.parse_dialog = ParseProgressDialog(len(workers_to_start), self)
        self.parse_dialog.canceled.connect(self.on_parse_canceled)
        self.parse_dialog.show()

        # Запускаем worker'ы
        for worker, pdf_path in workers_to_start:
            if self.cancel_flag.is_canceled():
                break
            self.thread_pool.start(worker)
            QApplication.processEvents()

    def _on_parse_started(self, delivery_item, pdf_path):
        """Worker начал парсинг — обновляем диалог."""
        if self.parse_dialog and not self.cancel_flag.is_canceled():
            self.parse_dialog.set_current_file(pdf_path)

    def _on_parse_finished(self, delivery_item, articles, sizes, locations, store_name):
        """Парсинг завершён — обновляем поставку и диалог."""
        # Игнорируем если была отмена
        if self.cancel_flag.is_canceled():
            return

        if store_name and store_name != "UNKNOWN" and store_name != delivery_item.store_name:
            self.tree_model.update_store_name(
                delivery_item,
                store_name,
                self._display_store_name(store_name),
            )

        if not articles:
            self.tree_model.update_delivery_status(delivery_item, "Пусто")
        else:
            self.tree_model.add_articles(
                delivery_item,
                articles,
                sizes,
                locations,
                article_transform=lambda article: get_corrected_article(
                    delivery_item.store_name, delivery_item.supply_type, article
                ),
            )
            self.tree_model.update_delivery_status(delivery_item, "Ожидание")
            delivery_index = self.tree_model.index(delivery_item.row(), 0)
            self.tree_view.setExpanded(
                delivery_index, self.auto_expand_delivery
            )

        # Обновляем диалог прогресса
        if self.parse_dialog:
            self.parse_dialog.on_file_completed(delivery_item.pdf_path)

    def _on_parse_error(self, delivery_item, error_message):
        """Ошибка парсинга."""
        if self.cancel_flag.is_canceled():
            return

        self.tree_model.update_delivery_status(delivery_item, "Ошибка")
        QMessageBox.warning(
            self, "Ошибка парсинга",
            f"Не удалось распарсить {os.path.basename(delivery_item.pdf_path)}:\n{error_message}"
        )

        if self.parse_dialog:
            self.parse_dialog.on_file_error(delivery_item.pdf_path)

    def on_parse_canceled(self):
        """
        Пункт 2: Отмена парсинга.
        - Устанавливает флаг
        - Очищает таблицу
        - Worker'ы при следующей проверке флага завершатся без действий
        """
        self.cancel_flag.cancel()

        # Очищаем список PDF, чтобы их можно было загрузить снова
        self.pdf_files.clear()
        self.delivery_items.clear()
        self.tree_model.clear()

        QMessageBox.information(
            self, "Отмена",
            "Парсинг отменён. Таблица очищена.\n\n"
            "Примечание: запущенные worker'ы завершат свою работу в фоне, "
            "но их результаты будут проигнорированы."
        )

    # ============================================
    # Очистка
    # ============================================

    def clear_table(self):
        if not self.delivery_items:
            return

        reply = QMessageBox.question(
            self, "Подтверждение",
            "Очистить все загруженные поставки?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self.pdf_files.clear()
        self.delivery_items.clear()
        self.tree_model.clear()

    # ============================================
    # Override
    # ============================================

    def _remember_override_for_article(
        self, delivery_item, article, file_path, status
    ):
        matching_indices = _matching_article_indices(delivery_item, article)
        article_sizes = [
            (delivery_item.child(index).article, delivery_item.child(index).size)
            for index in matching_indices
        ]
        set_overrides(
            delivery_item.store_name,
            delivery_item.supply_type,
            article_sizes,
            file_path,
        )
        for index in matching_indices:
            self.tree_model.update_article_status(delivery_item, index, status)
        return len(matching_indices)

    def on_override_clicked(self, delivery_item, article_index):
        """
        Обработка клика по кнопке 'Уточнить' для артикула.

        Логика:
        - Если copy_on_override = False → запоминаем путь в manual_overrides.json
        - Если copy_on_override = True:
            - Файл НЕ в override_path (и подпапках) → копируем в override_path/overrided/{article}{ext}
            - Файл УЖЕ в override_path (или подпапках) → переименовываем на месте в {article}{ext}
        """
        if article_index >= delivery_item.child_count():
            return

        article_item = delivery_item.child(article_index)
        article = article_item.article
        size = article_item.size
        store_name = delivery_item.store_name
        supply_type = delivery_item.supply_type

        # Синхронизируем буфер с нативным окном выбора файла до его открытия.
        QApplication.clipboard().setText(article)
        QApplication.processEvents()
        type_data = get_type_data(store_name, supply_type)
        if type_data.get("source") == "yandex":
            if sys.platform.startswith("win"):
                QMessageBox.information(
                    self, "Яндекс Диск недоступен",
                    "Яндекс Диск временно недоступен в Windows-версии."
                )
                return
            picker = YandexDiskPickerDialog(
                select_file=True,
                initial_path=type_data.get("path", ""),
                parent=self,
            )
            if picker.exec() != QDialog.Accepted:
                return
            linked_count = self._remember_override_for_article(
                delivery_item, article, picker.selected_path,
                "Уточнён (Яндекс Диск)",
            )
            QMessageBox.information(
                self, "Успех",
                f"Облачный файл привязан к {linked_count} поз.",
            )
            return

        initial_directory = _override_dialog_directory(
            type_data.get("path", ""), delivery_item.folder
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Укажите путь артикула {article}", initial_directory,
            "Изображения (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;Все файлы (*.*)"
        )
        if not file_path:
            return

        copy_on_override = type_data.get("copy_on_override", False)
        override_path = type_data.get("override_path", "")
        index_store_name = f"{store_name}_{supply_type}"

        if not copy_on_override:
            # === Режим "Только запомнить путь" ===
            linked_count = self._remember_override_for_article(
                delivery_item, article, file_path, "Уточнён (override)"
            )
            QMessageBox.information(
                self, "Успех",
                f"Путь сохранён для {linked_count} поз.:\n{file_path}",
            )
            return

        # === Режим "Копировать и переименовывать" ===

        # Определяем корневую папку для операций
        target_root = override_path if override_path else delivery_item.folder

        if not target_root:
            QMessageBox.warning(
                self, "Ошибка",
                "Не указан путь для копирования. Укажите его в настройках магазина."
            )
            return

        # Нормализуем пути для точного сравнения
        file_norm = os.path.normpath(os.path.realpath(file_path))
        root_norm = os.path.normpath(os.path.realpath(target_root))

        # Проверяем: находится ли файл внутри target_root (или его подпапок)
        # Используем разделитель для точного совпадения префикса
        is_in_root = (
            not is_network_path(file_path) and (
                file_norm == root_norm or
                file_norm.startswith(root_norm + os.sep)
            )
        )

        _, ext = os.path.splitext(file_path)
        new_filename = f"{article}{ext}"

        try:
            if is_in_root:
                # === Файл уже в папке магазина → переименовываем на месте ===
                current_dir = os.path.dirname(file_path)
                new_path = os.path.join(current_dir, new_filename)
                new_path_norm = os.path.normpath(new_path)

                # Если файл уже назван правильно — ничего не делаем
                if file_norm == new_path_norm:
                    QMessageBox.information(
                        self, "Готово",
                        f"Файл уже имеет правильное имя:\n{file_path}"
                    )
                    self.tree_model.update_article_status(
                        delivery_item, article_index, "Уточнён (уже на месте)"
                    )
                else:
                    # Проверка: нет ли конфликта имен
                    if os.path.exists(new_path):
                        reply = QMessageBox.question(
                            self, "Конфликт имен",
                            f"Файл уже существует:\n{new_path}\n\n"
                            f"Заменить его выбранным файлом?",
                            QMessageBox.Yes | QMessageBox.No
                        )
                        if reply != QMessageBox.Yes:
                            return

                        # Удаляем конфликтующий файл
                        try:
                            os.remove(new_path)
                        except Exception as e:
                            QMessageBox.critical(
                                self, "Ошибка",
                                f"Не удалось удалить конфликтующий файл:\n{e}"
                            )
                            return

                    # Переименовываем
                    os.rename(file_path, new_path)
                    print(f"[OVERRIDE] Переименован: {file_path} → {new_path}")
                    QMessageBox.information(
                        self, "Успех",
                        f"Файл переименован на месте:\n{new_path}"
                    )
                    self.tree_model.update_article_status(
                        delivery_item, article_index, "Уточнён (переименован)"
                    )
            else:
                # === Файл извне → копируем в overrided ===
                overrided_dir = os.path.join(target_root, "overrided")
                os.makedirs(overrided_dir, exist_ok=True)

                new_path = os.path.join(overrided_dir, new_filename)

                # Проверка: нет ли конфликта имен
                if os.path.exists(new_path):
                    reply = QMessageBox.question(
                        self, "Конфликт имен",
                        f"Файл уже существует:\n{new_path}\n\n"
                        f"Заменить его?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        return

                    try:
                        os.remove(new_path)
                    except Exception as e:
                        QMessageBox.critical(
                            self, "Ошибка",
                            f"Не удалось удалить конфликтующий файл:\n{e}"
                        )
                        return

                # Копируем
                copy_resource(
                    file_path, new_path, cancel_token=self.shutdown_token
                )
                print(f"[OVERRIDE] Скопирован: {file_path} → {new_path}")
                QMessageBox.information(
                    self, "Успех",
                    f"Файл скопирован в:\n{new_path}"
                )
                self.tree_model.update_article_status(
                    delivery_item, article_index, "Уточнён (скопирован)"
                )

            # После операций обновляем только изменившиеся файлы индекса.
            update_index_for_store(
                index_store_name,
                delivery_item.folder,
                override_path=override_path,
                force=True
            )

        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось обработать файл:\n{e}"
            )

    def on_restart_clicked(self, delivery_item):
        if self.is_searching:
            QMessageBox.information(self, "Информация", "Дождитесь завершения текущего поиска")
            return

        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Перезапустить поиск для поставки:\n{os.path.basename(delivery_item.pdf_path)}?\n\n"
            f"Все скопированные файлы будут удалены.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        if delivery_item.output_folder and os.path.exists(delivery_item.output_folder):
            try:
                shutil.rmtree(delivery_item.output_folder)
            except Exception as e:
                QMessageBox.critical(
                    self, "Ошибка повторного поиска",
                    "Не удалось очистить папку предыдущего результата.\n\n"
                    f"{delivery_item.output_folder}\n\n{e}",
                )
                return

        for i in range(delivery_item.child_count()):
            self.tree_model.update_article_status(delivery_item, i, "Ожидание")
            delivery_item.child(i).copied_file = None
        self.tree_model.update_delivery_status(delivery_item, "Ожидание")

        self.is_searching = True
        self._set_actions_enabled(False)

        store_name = delivery_item.store_name
        supply_type = delivery_item.supply_type
        key = f"{store_name}_{supply_type}"

        type_data = get_type_data(store_name, supply_type)
        override_path = type_data.get("override_path") if type_data.get("copy_on_override") else None

        unique_stores = {
            key: {
                "store_name": store_name,
                "supply_type": supply_type,
                "folder": delivery_item.folder,
                "override_path": override_path,
                "force": False,
                "source": type_data.get("source", "local"),
                "additional_sources": type_data.get("additional_sources", []),
            }
        }

        self._pending_restart_item = delivery_item

        if type_data.get("source", "local") == "yandex":
            if sys.platform.startswith("win"):
                self._pending_restart_item = None
                self.is_searching = False
                self._set_actions_enabled(True)
                QMessageBox.information(
                    self, "Яндекс Диск недоступен",
                    "Для этого магазина выберите локальную или сетевую папку."
                )
                return
            cloud_path = type_data.get("path", "").strip()
            if not has_yandex_index_for_store(key, cloud_path):
                self._pending_restart_item = None
                self.is_searching = False
                self._set_actions_enabled(True)
                QMessageBox.warning(
                    self, "Требуется индекс",
                    "Для этого облачного магазина ещё нет готового индекса.\n\n"
                    "Откройте настройки и выполните ручную полную сверку."
                )
                return
            print(
                f"[INDEX] {key}: повторный облачный поиск без индексации, "
                "используется SQLite"
            )
            if any(
                item.get("source", "local") != "yandex"
                for item in type_data.get("additional_sources", [])
            ):
                worker = IndexRebuildWorker(unique_stores, self.shutdown_token)
                worker.signals.finished.connect(self._on_restart_index_finished)
                worker.signals.error.connect(self._on_index_error)
                self.thread_pool.start(worker)
            else:
                self._on_restart_index_finished()
            return

        worker = IndexRebuildWorker(unique_stores, self.shutdown_token)
        worker.signals.finished.connect(self._on_restart_index_finished)
        worker.signals.error.connect(self._on_index_error)
        self.thread_pool.start(worker)

    def _on_restart_index_finished(self):
        delivery_item = self._pending_restart_item
        if not delivery_item:
            self.is_searching = False
            self._set_actions_enabled(True)
            return

        self.tree_model.update_delivery_status(delivery_item, "Поиск...")

        worker = SearchWorker(
            [delivery_item],
            self.output_folder,
            self.matcher_cache,
            self.append_store_to_delivery_folder,
            self.shutdown_token,
            store_suffix_exclusions=self.store_suffix_exclusions,
        )
        worker.signals.article_status.connect(self._on_article_status)
        worker.signals.article_copied.connect(self._on_article_copied)
        worker.signals.delivery_finished.connect(self._on_delivery_finished)
        worker.signals.all_finished.connect(self._on_search_finished)
        worker.signals.error.connect(self._on_search_error)
        self.thread_pool.start(worker)

        self._pending_restart_item = None

    def _on_index_error(self, error_message):
        if self._shutdown_started:
            return
        self.is_searching = False
        self._set_actions_enabled(True)
        QMessageBox.critical(self, "Ошибка", f"Ошибка обновления индексов:\n{error_message}")

    # ============================================
    # Запуск поиска
    # ============================================

    def run_all(self):
        if self.is_searching:
            QMessageBox.information(self, "Информация", "Поиск уже идёт")
            return

        if not self.delivery_items:
            QMessageBox.information(self, "Информация", "Нет загруженных поставок")
            return

        unique_stores = {}
        processed_store_keys = set()
        missing_cloud_indexes = []
        for delivery_item in self.delivery_items:
            store_name = delivery_item.store_name
            supply_type = delivery_item.supply_type
            key = f"{store_name}_{supply_type}"

            if key not in processed_store_keys:
                processed_store_keys.add(key)
                type_data = get_type_data(store_name, supply_type)
                source = type_data.get("source", "local")
                if source == "yandex":
                    if sys.platform.startswith("win"):
                        missing_cloud_indexes.append(
                            f"{store_name} ({supply_type}) — Яндекс Диск недоступен"
                        )
                        continue
                    cloud_path = type_data.get("path", "").strip()
                    if not has_yandex_index_for_store(key, cloud_path):
                        missing_cloud_indexes.append(
                            f"{store_name} ({supply_type})"
                        )
                    else:
                        print(
                            f"[INDEX] {key}: облачный поиск без индексации, "
                            "используется сохранённый SQLite-индекс"
                        )
                        unique_stores[key] = {
                            "store_name": store_name,
                            "supply_type": supply_type,
                            "folder": cloud_path,
                            "override_path": None,
                            "source": source,
                            "force": False,
                            "additional_sources": type_data.get(
                                "additional_sources", []
                            ),
                        }
                    continue
                override_path = type_data.get("override_path") if type_data.get("copy_on_override") else None

                unique_stores[key] = {
                    "store_name": store_name,
                    "supply_type": supply_type,
                    "folder": delivery_item.folder,
                    "override_path": override_path,
                    "source": source,
                    "force": False,
                    "additional_sources": type_data.get("additional_sources", []),
                }

        if missing_cloud_indexes:
            QMessageBox.warning(
                self, "Требуется индекс",
                "Для облачных магазинов не подготовлены индексы:\n\n"
                + "\n".join(missing_cloud_indexes)
                + "\n\nВ Windows-версии используйте локальную или сетевую папку."
            )
            return

        self.is_searching = True
        self._set_actions_enabled(False)
        if not unique_stores:
            self._on_index_finished()
            return

        worker = IndexRebuildWorker(unique_stores, self.shutdown_token)
        worker.signals.finished.connect(self._on_index_finished)
        worker.signals.error.connect(self._on_index_error)
        self.thread_pool.start(worker)

    def _set_actions_enabled(self, enabled):
        self.load_action.setEnabled(enabled)
        self.run_action.setEnabled(enabled)
        self.clear_action.setEnabled(enabled)
        self.settings_action.setEnabled(enabled)

    def _on_index_finished(self):
        for delivery_item in self.delivery_items:
            for i in range(delivery_item.child_count()):
                self.tree_model.update_article_status(delivery_item, i, "Ожидание")
                delivery_item.child(i).copied_file = None
            self.tree_model.update_delivery_status(delivery_item, "Поиск...")

        worker = SearchWorker(
            self.delivery_items,
            self.output_folder,
            self.matcher_cache,
            self.append_store_to_delivery_folder,
            self.shutdown_token,
            store_suffix_exclusions=self.store_suffix_exclusions,
        )
        worker.signals.article_status.connect(self._on_article_status)
        worker.signals.article_copied.connect(self._on_article_copied)
        worker.signals.delivery_finished.connect(self._on_delivery_finished)
        worker.signals.all_finished.connect(self._on_search_finished)
        worker.signals.error.connect(self._on_search_error)
        self.thread_pool.start(worker)

    def _on_article_status(self, delivery_item, article_index, status):
        self.tree_model.update_article_status(delivery_item, article_index, status)

    def _on_article_copied(self, delivery_item, article_index, file_path):
        self.tree_model.set_article_copied_file(delivery_item, article_index, file_path)

    def _on_delivery_finished(self, delivery_item):
        base_output_folder = os.path.join(self.output_folder, "Найденные_принты_все")
        folder_name = _delivery_output_folder_name(
            delivery_item.pdf_path,
            getattr(
                delivery_item,
                "display_store_name",
                delivery_item.store_name,
            ),
            should_append_store_suffix(
                delivery_item.store_name,
                self.append_store_to_delivery_folder,
                self.store_suffix_exclusions,
            ),
        )
        output_folder = os.path.join(base_output_folder, folder_name)

        delivery_item.output_folder = output_folder
        status = (
            "Готово с предупреждением"
            if getattr(delivery_item, "index_warning", False)
            else "Готово"
        )
        self.tree_model.update_delivery_status(delivery_item, status)

    def _on_search_finished(self):
        if self._shutdown_started:
            return
        self.is_searching = False
        self._set_actions_enabled(True)
        results_folder = os.path.join(
            self.output_folder, "Найденные_принты_все"
        )
        if self.open_results_folder and os.path.isdir(results_folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(results_folder))
        QMessageBox.information(self, "Готово", "Обработка всех поставок завершена")

    def _on_search_error(self, error_message):
        if self._shutdown_started:
            return
        self.is_searching = False
        self._set_actions_enabled(True)
        QMessageBox.critical(self, "Ошибка", f"Ошибка поиска:\n{error_message}")

    # ============================================
    # Диалоги
    # ============================================

    def _display_store_name(self, store_name):
        display_name = str(self.store_display_names.get(store_name, "")).strip()
        return display_name or store_name

    def open_settings(self):
        from gui.settings_window import SettingsWindow
        dialog = SettingsWindow(self)
        dialog.exec()
        if getattr(dialog, 'needs_reset_cache', False):
            self.matcher_cache.clear()
        self.load_config()
        self.tree_model.refresh_store_display_names(self.store_display_names)
        self._schedule_next_cloud_auto_index()

    def _finish_windows_shutdown(self):
        if self._shutdown_poll_timer:
            self._shutdown_poll_timer.stop()
        if self._shutdown_deadline_timer:
            self._shutdown_deadline_timer.stop()
        if self._shutdown_dialog:
            self._shutdown_dialog.close()
        QApplication.instance().quit()

    def _poll_windows_shutdown(self):
        pools_idle = (
            self.thread_pool.activeThreadCount() == 0
            and QThreadPool.globalInstance().activeThreadCount() == 0
        )
        if pools_idle:
            self._finish_windows_shutdown()

    def _force_windows_shutdown(self):
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(0)

    def _start_windows_shutdown(self):
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.shutdown_token.cancel()
        self.cancel_flag.cancel()
        self.cloud_auto_timer.stop()
        self.thread_pool.clear()
        QThreadPool.globalInstance().clear()
        self.hide()
        self._shutdown_dialog = QProgressDialog(
            "Останавливаются фоновые операции…", "", 0, 0, None
        )
        self._shutdown_dialog.setWindowTitle("Завершение работы")
        self._shutdown_dialog.setCancelButton(None)
        self._shutdown_dialog.setMinimumDuration(0)
        self._shutdown_dialog.setWindowModality(Qt.ApplicationModal)
        self._shutdown_dialog.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self._shutdown_dialog.show()
        self._shutdown_poll_timer = QTimer(self)
        self._shutdown_poll_timer.timeout.connect(self._poll_windows_shutdown)
        self._shutdown_poll_timer.start(100)
        self._shutdown_deadline_timer = QTimer(self)
        self._shutdown_deadline_timer.setSingleShot(True)
        self._shutdown_deadline_timer.timeout.connect(self._force_windows_shutdown)
        self._shutdown_deadline_timer.start(15_000)
        self._poll_windows_shutdown()

    def closeEvent(self, event):
        if sys.platform.startswith("win"):
            event.ignore()
            QTimer.singleShot(0, self._start_windows_shutdown)
            return
        if getattr(self, "allow_quit", False) or not event.spontaneous():
            event.accept()
            return
        event.ignore()
        self.hide()

    def open_logs(self):
        from gui.dialogs.log_window import LogWindow
        dialog = LogWindow(self)
        dialog.show()
