"""Диалог выбора папки или файла на Яндекс Диске без Finder/Проводника."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from core.yandex_disk import (
    YandexDiskClient, YandexDiskError, normalise_disk_path, to_yandex_uri,
)


class YandexDiskPickerDialog(QDialog):
    def __init__(self, *, select_file=False, initial_path="", parent=None):
        super().__init__(parent)
        self.select_file = select_file
        self.current_path = normalise_disk_path(initial_path or "disk:/")
        self.selected_path = ""
        self.setWindowTitle(
            "Выберите файл на Яндекс Диске"
            if select_file else "Выберите папку на Яндекс Диске"
        )
        self.resize(620, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Яндекс Диск"))
        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Найти артикул в этой папке и её подпапках")
        self.search_input.returnPressed.connect(self.search_files)
        search_layout.addWidget(self.search_input, 1)
        self.search_button = QPushButton("Найти")
        self.search_button.clicked.connect(self.search_files)
        search_layout.addWidget(self.search_button)
        self.reset_button = QPushButton("Сбросить")
        self.reset_button.clicked.connect(self.reset_search)
        search_layout.addWidget(self.reset_button)
        layout.addLayout(search_layout)

        controls = QHBoxLayout()
        self.up_button = QPushButton("← Вверх")
        self.up_button.clicked.connect(self.go_up)
        controls.addWidget(self.up_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(self.open_item)
        layout.addWidget(self.items, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.choose_button = buttons.addButton(
            "Выбрать файл" if select_file else "Выбрать эту папку",
            QDialogButtonBox.AcceptRole,
        )
        self.choose_button.clicked.connect(self.choose_current)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_directory()

    def load_directory(self):
        self.path_label.setText(self.current_path)
        self.up_button.setEnabled(self.current_path != "disk:/")
        self.items.clear()
        try:
            entries = YandexDiskClient().list_directory(self.current_path)
        except YandexDiskError as error:
            QMessageBox.warning(self, "Яндекс Диск", str(error))
            return
        for entry in sorted(
            entries, key=lambda item: (item["type"] != "dir", item["name"].lower())
        ):
            prefix = "📁 " if entry["type"] == "dir" else "📄 "
            item = QListWidgetItem(prefix + entry["name"])
            item.setData(Qt.UserRole, entry)
            self.items.addItem(item)

    def search_files(self):
        """Ищет подстроку только в текущей папке и её вложениях."""
        query = self.search_input.text().strip().lower()
        if not query:
            self.load_directory()
            return

        self.items.clear()
        self.path_label.setText(f"Поиск «{query}» в {self.current_path}")
        self.search_button.setEnabled(False)
        try:
            matches = [
                file for file in YandexDiskClient().iter_files(self.current_path)
                if query in file.name.lower()
            ]
        except YandexDiskError as error:
            QMessageBox.warning(self, "Яндекс Диск", str(error))
            return
        finally:
            self.search_button.setEnabled(True)

        for file in matches:
            item = QListWidgetItem(f"📄 {file.name}    —    {file.path}")
            item.setData(
                Qt.UserRole,
                {"name": file.name, "path": file.path, "type": "file"},
            )
            self.items.addItem(item)
        if not matches:
            self.items.addItem(QListWidgetItem("Ничего не найдено"))

    def reset_search(self):
        self.search_input.clear()
        self.load_directory()

    def go_up(self):
        parent = self.current_path.rsplit("/", 1)[0] or "disk:"
        self.current_path = normalise_disk_path(parent)
        self.load_directory()

    def open_item(self, item):
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        if entry["type"] == "dir":
            self.current_path = entry["path"]
            self.search_input.clear()
            self.load_directory()
        elif self.select_file:
            self.selected_path = to_yandex_uri(entry["path"])
            self.accept()
        else:
            self.current_path = normalise_disk_path(entry["path"].rsplit("/", 1)[0])
            self.search_input.clear()
            self.load_directory()

    def choose_current(self):
        if not self.select_file:
            self.selected_path = self.current_path
            self.accept()
            return
        item = self.items.currentItem()
        entry = item.data(Qt.UserRole) if item else None
        if entry and entry["type"] == "file":
            self.selected_path = to_yandex_uri(entry["path"])
            self.accept()
        else:
            QMessageBox.information(self, "Яндекс Диск", "Выберите файл в списке.")
