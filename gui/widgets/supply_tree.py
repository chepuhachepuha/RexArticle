from PySide6.QtWidgets import QTreeView, QAbstractItemView, QHeaderView
from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex, QSize
from PySide6.QtGui import QFont, QColor
from core.text_normalization import normalize_article


class SupplyItem:
    """Базовый класс для элемента дерева."""

    def __init__(self, parent=None):
        self.parent_item = parent
        self.child_items = []

    def append_child(self, child):
        self.child_items.append(child)

    def child(self, row):
        if 0 <= row < len(self.child_items):
            return self.child_items[row]
        return None

    def child_count(self):
        return len(self.child_items)

    def row(self):
        if self.parent_item:
            return self.parent_item.child_items.index(self)
        return 0

    def parent(self):
        return self.parent_item


class SupplyRootItem(SupplyItem):
    """Корневой элемент (невидимый)."""

    def __init__(self):
        super().__init__(None)


class SupplyDeliveryItem(SupplyItem):
    """Элемент поставки (parent-строка)."""

    def __init__(
        self, pdf_path, store_name, supply_type, folder, parent,
        display_store_name=None,
    ):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.store_name = store_name
        self.display_store_name = display_store_name or store_name
        self.supply_type = supply_type
        self.folder = folder
        self.article_count = 0
        self.found_count = 0
        self.status = "Ожидание"
        self.articles = []
        self.sizes = []
        self.locations = []
        self.output_folder = None

    def update_counts(self):
        self.article_count = self.child_count()
        self.found_count = sum(
            1 for child in self.child_items if child.status == "Найден"
        )

    def get_display_data(self):
        import os
        pdf_name = os.path.basename(self.pdf_path)
        store_info = f"{self.display_store_name} ({self.supply_type})"
        progress = f"{self.found_count}/{self.article_count}" if self.article_count > 0 else "0/0"

        return [
            pdf_name,       # Поставка / Артикул
            store_info,     # Магазин / Размер
            self.folder,    # Папка
            progress,       # Прогресс
            ""              # Действия
        ]


class SupplyArticleItem(SupplyItem):
    """Элемент артикула (child-строка)."""

    def __init__(self, article, size, location, status, parent, detected_article=None):
        super().__init__(parent)
        self.article = article
        self.detected_article = detected_article if detected_article is not None else article
        self.size = size
        self.location = location
        self.status = status
        self.copied_file = None

    def get_display_data(self):
        return [
            f"    {self.article}",  # Отступ для визуального вложения
            self.size,
            "",
            self.status,    # Статус в колонке "Прогресс"
            ""              # Действия
        ]


class SupplyTreeModel(QAbstractItemModel):
    """Модель данных для дерева поставок — 5 колонок."""

    HEADERS = [
        "Поставка / Артикул",
        "Магазин / Размер",
        "Папка",
        "Прогресс",
        "Действия"
    ]

    # ✅ Цвет для ненайденных (Material Red 800)
    COLOR_NOT_FOUND = "#C62828"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_item = SupplyRootItem()

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            parent_item = self.root_item
        else:
            parent_item = parent.internalPointer()
        return parent_item.child_count()

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        item = index.internalPointer()
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(item, (SupplyDeliveryItem, SupplyArticleItem)):
                data = item.get_display_data()
                return data[column] if column < len(data) else ""

        elif role == Qt.ItemDataRole.FontRole:
            font = QFont()
            if isinstance(item, SupplyDeliveryItem):
                font.setPointSize(13)
            else:
                font.setPointSize(12)
            return font

        # ✅ НОВОЕ: Красный цвет текста для ненайденных
        elif role == Qt.ItemDataRole.ForegroundRole:
            if column == 3:  # Колонка "Прогресс"
                if isinstance(item, SupplyDeliveryItem):
                    # Parent: красный если есть ненайденные
                    if item.article_count > 0 and item.found_count < item.article_count:
                        return QColor(self.COLOR_NOT_FOUND)
                elif isinstance(item, SupplyArticleItem):
                    # Child: красный если статус "Не найден"
                    if item.status == "Не найден":
                        return QColor(self.COLOR_NOT_FOUND)

        elif role == Qt.ItemDataRole.SizeHintRole:
            if isinstance(item, SupplyDeliveryItem):
                return QSize(0, 34)
            else:
                return QSize(0, 28)

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if column == 3:
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        elif orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.SizeHintRole:
            return QSize(0, 34)
        return None

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            parent_item = self.root_item
        else:
            parent_item = parent.internalPointer()
        child_item = parent_item.child(row)
        if child_item:
            return self.createIndex(row, column, child_item)
        return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        child_item = index.internalPointer()
        parent_item = child_item.parent()
        if parent_item == self.root_item or parent_item is None:
            return QModelIndex()
        return self.createIndex(parent_item.row(), 0, parent_item)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def add_delivery(
        self, pdf_path, store_name, supply_type, folder,
        display_store_name=None,
    ):
        """Добавляет новую поставку в дерево."""
        delivery_item = SupplyDeliveryItem(
            pdf_path, store_name, supply_type, folder, self.root_item,
            display_store_name,
        )
        self.beginInsertRows(
            QModelIndex(),
            self.root_item.child_count(),
            self.root_item.child_count()
        )
        self.root_item.append_child(delivery_item)
        self.endInsertRows()
        return delivery_item

    def add_articles(self, delivery_item, articles, sizes, locations, article_transform=None):
        """Добавляет артикулы к поставке."""
        delivery_index = self.createIndex(delivery_item.row(), 0, delivery_item)
        count = len(articles)
        if count == 0:
            return

        self.beginInsertRows(delivery_index, 0, count - 1)
        factual_articles = []
        for article, size, location in zip(articles, sizes, locations):
            factual_article = article_transform(article) if article_transform else article
            article_item = SupplyArticleItem(
                factual_article, size, location, "Ожидание", delivery_item,
                detected_article=article,
            )
            delivery_item.append_child(article_item)
            factual_articles.append(factual_article)
        self.endInsertRows()

        delivery_item.articles = factual_articles
        delivery_item.sizes = sizes
        delivery_item.locations = locations
        delivery_item.status = "Ожидание"
        delivery_item.update_counts()

        last_col_index = self.createIndex(
            delivery_item.row(), self.columnCount() - 1, delivery_item
        )
        self.dataChanged.emit(delivery_index, last_col_index)

    def update_articles_by_detected_article(
        self, delivery_item, detected_article, factual_article
    ):
        """Apply a correction to every matching raw article in one delivery."""
        target = normalize_article(detected_article)
        changed_rows = []
        for row in range(delivery_item.child_count()):
            item = delivery_item.child(row)
            if normalize_article(item.detected_article) != target:
                continue
            item.article = factual_article
            changed_rows.append(row)
            if row < len(delivery_item.articles):
                delivery_item.articles[row] = factual_article

        for row in changed_rows:
            index = self.createIndex(row, 0, delivery_item.child(row))
            self.dataChanged.emit(index, index)
        return changed_rows

    def update_delivery_status(self, delivery_item, status):
        """Обновляет статус поставки."""
        delivery_item.status = status
        delivery_index = self.createIndex(delivery_item.row(), 0, delivery_item)
        last_col_index = self.createIndex(
            delivery_item.row(), self.columnCount() - 1, delivery_item
        )
        self.dataChanged.emit(delivery_index, last_col_index)

    def update_article_status(self, delivery_item, article_index, status):
        """Обновляет статус артикула и пересчитывает прогресс поставки."""
        if article_index < delivery_item.child_count():
            article_item = delivery_item.child(article_index)
            article_item.status = status

            article_model_index = self.createIndex(
                article_item.row(), 0, article_item
            )
            last_col_index = self.createIndex(
                article_item.row(), self.columnCount() - 1, article_item
            )
            self.dataChanged.emit(article_model_index, last_col_index)

            delivery_item.update_counts()
            delivery_index = self.createIndex(delivery_item.row(), 0, delivery_item)
            last_col_parent = self.createIndex(
                delivery_item.row(), self.columnCount() - 1, delivery_item
            )
            self.dataChanged.emit(delivery_index, last_col_parent)

    def set_article_copied_file(self, delivery_item, article_index, file_path):
        """Сохраняет путь к скопированному файлу (для перезапуска)."""
        if article_index < delivery_item.child_count():
            delivery_item.child(article_index).copied_file = file_path

    def update_store_name(
        self, delivery_item, new_store_name, display_store_name=None
    ):
        """Обновляет имя магазина поставки."""
        if (delivery_item.store_name != new_store_name
                and new_store_name and new_store_name != "UNKNOWN"):
            delivery_item.store_name = new_store_name
            delivery_item.display_store_name = display_store_name or new_store_name
            delivery_index = self.createIndex(delivery_item.row(), 0, delivery_item)
            last_col_index = self.createIndex(
                delivery_item.row(), self.columnCount() - 1, delivery_item
            )
            self.dataChanged.emit(delivery_index, last_col_index)

    def refresh_store_display_names(self, display_names):
        """Обновляет видимые названия, не меняя внутренние ключи."""
        for delivery_item in self.root_item.child_items:
            delivery_item.display_store_name = (
                str(display_names.get(delivery_item.store_name, "")).strip()
                or delivery_item.store_name
            )
            delivery_index = self.createIndex(
                delivery_item.row(), 0, delivery_item
            )
            last_col_index = self.createIndex(
                delivery_item.row(), self.columnCount() - 1, delivery_item
            )
            self.dataChanged.emit(delivery_index, last_col_index)

    def clear(self):
        """Очищает всё дерево."""
        self.beginResetModel()
        self.root_item = SupplyRootItem()
        self.endResetModel()


class SupplyTreeView(QTreeView):
    """Виджет дерева поставок — залоченные колонки, компактный."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        self.setObjectName("supplyTree")

        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setRootIsDecorated(True)
        # Единая геометрия строк не даёт индикаторам раскрытия «прыгать» во
        # время анимации на длинном списке поставок. Саму анимацию сохраняем.
        self.setAnimated(True)
        self.setExpandsOnDoubleClick(False)
        self.setUniformRowHeights(True)
        self.setIndentation(20)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)

        for i in range(len(SupplyTreeModel.HEADERS)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

    def set_model(self, model):
        """Устанавливает модель и настраивает пропорции колонок."""
        self.setModel(model)
        self._update_column_widths()
        self.expandAll()

    def _update_column_widths(self):
        """
        Перераспределяет ширины колонок пропорционально окну.
        Колонки залочены — пользователь не может их менять мышью.
        Последняя колонка получает фиксированный минимум, чтобы заголовок
        «Действия» и кнопки никогда не обрезались.
        """
        if self.model() is None:
            return

        total_width = self.viewport().width()
        if total_width <= 0:
            return

        actions_width = 112
        progress_width = max(118, int(total_width * 0.10))
        remaining_width = max(0, total_width - actions_width - progress_width)
        first_width = int(remaining_width * 0.37)
        store_width = int(remaining_width * 0.22)
        folder_width = remaining_width - first_width - store_width
        widths = [first_width, store_width, folder_width, progress_width, actions_width]

        header = self.header()

        # Временно разрешаем resize для программного изменения
        for i in range(len(widths)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        # Устанавливаем ширины
        for i, width in enumerate(widths):
            if i < header.count():
                header.resizeSection(i, width)

        # Снова фиксируем
        for i in range(len(widths)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)

    def resizeEvent(self, event):
        """Пересчитывает ширины колонок при изменении размера окна."""
        super().resizeEvent(event)
        if self.model() is not None:
            self._update_column_widths()
