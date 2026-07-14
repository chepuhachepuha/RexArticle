from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionButton, QApplication
from PySide6.QtCore import Qt, QEvent, Signal, QRect
from PySide6.QtGui import QPainter


class OverrideButtonDelegate(QStyledItemDelegate):
    """Делегат для отрисовки двух кнопок в колонке "Действия"."""
    
    override_clicked = Signal(object, int)
    restart_clicked = Signal(object)
    
    BUTTON_TEXT_OVERRIDE = "🔍"
    BUTTON_TEXT_RESTART = "🔄"
    
    def __init__(self, parent=None):
        super().__init__(parent)
    
    def paint(self, painter, option, index):
        item = index.internalPointer()
        
        if item is None:
            super().paint(painter, option, index)
            return
        
        from gui.widgets.supply_tree import SupplyArticleItem, SupplyDeliveryItem
        
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        painter.restore()
        
        if isinstance(item, SupplyArticleItem):
            button_option = QStyleOptionButton()
            button_option.text = self.BUTTON_TEXT_OVERRIDE
            button_option.rect = self.get_override_button_rect(option.rect)
            button_option.state = QStyle.State_Enabled
            if option.state & QStyle.State_MouseOver:
                button_option.state |= QStyle.State_MouseOver
            
            widget = option.widget
            style = widget.style() if widget else QApplication.style()
            style.drawControl(QStyle.CE_PushButton, button_option, painter, widget)
        
        elif isinstance(item, SupplyDeliveryItem):
            button_option = QStyleOptionButton()
            button_option.text = self.BUTTON_TEXT_RESTART
            button_option.rect = self.get_restart_button_rect(option.rect)
            button_option.state = QStyle.State_Enabled
            if option.state & QStyle.State_MouseOver:
                button_option.state |= QStyle.State_MouseOver
            
            widget = option.widget
            style = widget.style() if widget else QApplication.style()
            style.drawControl(QStyle.CE_PushButton, button_option, painter, widget)
    
    def editorEvent(self, event, model, option, index):
        from gui.widgets.supply_tree import SupplyArticleItem, SupplyDeliveryItem
        
        item = index.internalPointer()
        if item is None:
            return False
        
        if event.type() != QEvent.MouseButtonRelease:
            return False
        
        if isinstance(item, SupplyArticleItem):
            button_rect = self.get_override_button_rect(option.rect)
            if button_rect.contains(event.pos()):
                delivery_item = item.parent()
                article_index = item.row()
                self.override_clicked.emit(delivery_item, article_index)
                return True
        
        elif isinstance(item, SupplyDeliveryItem):
            button_rect = self.get_restart_button_rect(option.rect)
            if button_rect.contains(event.pos()):
                self.restart_clicked.emit(item)
                return True
        
        return False
    
    def get_override_button_rect(self, cell_rect):
        """
        Прямоугольник кнопки 🔍 для артикула.
        ✅ Пункт 3: сдвиг на 8px влево
        """
        button_width = 28
        button_height = 22
        
        # Было: x = cell_rect.x() + 4
        # Стало: сдвигаем на 8px влево (внутри ячейки)
        x = cell_rect.x() + cell_rect.width() - button_width - 8
        y = cell_rect.y() + (cell_rect.height() - button_height) // 2
        
        return QRect(x, y, button_width, button_height)
    
    def get_restart_button_rect(self, cell_rect):
        """
        Прямоугольник кнопки 🔄 для поставки.
        ✅ Пункт 3: сдвиг на 8px влево от центра
        """
        button_width = 36
        button_height = 22
        
        # Было: x = cell_rect.x() + (cell_rect.width() - button_width) // 2
        # Стало: сдвигаем чуть левее центра
        x = cell_rect.x() + cell_rect.width() - button_width - 8
        y = cell_rect.y() + (cell_rect.height() - button_height) // 2
        
        return QRect(x, y, button_width, button_height)