import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton,
    QHBoxLayout, QMessageBox, QFileDialog
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from core.logger import get_log_file_path


class LogWindow(QDialog):
    """Окно для отображения логов с авто-обновлением."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Логи программы")
        self.setGeometry(150, 150, 900, 600)
        self.setObjectName("logWindow")
        self.log_file = get_log_file_path()
        
        # ✅ Применяем тёмную тему ко всему окну
        self.setStyleSheet("""
            QDialog#logWindow {
                background-color: #1E1E1E;
            }
        """)
        
        self.setup_ui()
        self._use_system_theme()
        self.setup_auto_refresh()
        self.refresh_logs()

    def _use_system_theme(self):
        """Окно логов следует системной теме, как и остальные диалоги."""
        self.setStyleSheet("")
        for widget in self.findChildren(QPushButton) + self.findChildren(QTextEdit):
            widget.setStyleSheet("")
        self.save_button.setProperty("accent", True)
        self.clear_button.setProperty("destructive", True)
    
    def setup_ui(self):
        """Настраивает интерфейс в тёмной теме."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        # Текстовое поле для логов
        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("logText")
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Consolas", 10))
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #1E1E1E;
                color: #D4D4D4;
                border: 1px solid #3E3E3E;
                border-radius: 4px;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                padding: 6px;
                selection-background-color: #264F78;
                selection-color: #FFFFFF;
            }
            QScrollBar:vertical {
                background-color: #1E1E1E;
                width: 10px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #424242;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #616161;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background-color: #1E1E1E;
                height: 10px;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background-color: #424242;
                border-radius: 5px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #616161;
            }
        """)
        layout.addWidget(self.text_edit)
        
        # Статус-бар
        self.status_label = QPushButton("")
        self.status_label.setFlat(True)
        self.status_label.setStyleSheet("""
            QPushButton {
                color: #858585;
                font-size: 11px;
                text-align: left;
                padding: 2px;
                background-color: transparent;
                border: none;
            }
        """)
        layout.addWidget(self.status_label)
        
        # Кнопки
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        
        # ============================================
        # Общие стили для тёмных кнопок
        # ============================================
        
        # Базовая тёмная кнопка
        base_button_style = """
            QPushButton {
                background-color: #2D2D2D;
                color: #D4D4D4;
                border: 1px solid #3E3E3E;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3E3E3E;
                border-color: #555555;
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #1F1F1F;
            }
        """
        
        # ✅ Пункт 1: Кнопка "Сохранить TXT" — тёмно-зелёный акцент
        self.save_button = QPushButton("💾 Сохранить TXT")
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: #1B5E20;
                color: #FFFFFF;
                border: 1px solid #2E7D32;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #2E7D32;
                border-color: #388E3C;
            }
            QPushButton:pressed {
                background-color: #0D3311;
            }
        """)
        self.save_button.clicked.connect(self.save_logs_to_file)
        button_layout.addWidget(self.save_button)
        
        # Кнопка "Обновить"
        self.refresh_button = QPushButton("🔄 Обновить")
        self.refresh_button.setStyleSheet(base_button_style)
        self.refresh_button.clicked.connect(self.refresh_logs)
        button_layout.addWidget(self.refresh_button)
        
        # Кнопка "Очистить" — тёмно-красный акцент (предупреждающее действие)
        self.clear_button = QPushButton("🗑 Очистить")
        self.clear_button.setStyleSheet("""
            QPushButton {
                background-color: #424242;
                color: #D4D4D4;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #B71C1C;
                border-color: #C62828;
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #7F0000;
            }
        """)
        self.clear_button.clicked.connect(self.clear_logs)
        button_layout.addWidget(self.clear_button)
        
        button_layout.addStretch()
        
        # Кнопка авто-обновления
        self.auto_refresh_checkbox = QPushButton("⏸ Авто-обновление: ВКЛ")
        self.auto_refresh_checkbox.setCheckable(True)
        self.auto_refresh_checkbox.setChecked(True)
        self.auto_refresh_checkbox.setStyleSheet("""
            QPushButton {
                background-color: #2D2D2D;
                color: #D4D4D4;
                border: 1px solid #3E3E3E;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #3E3E3E;
                border-color: #555555;
            }
            QPushButton:checked {
                background-color: #1A3A5C;
                border-color: #2196F3;
                color: #64B5F6;
            }
            QPushButton:checked:hover {
                background-color: #1E4470;
            }
        """)
        self.auto_refresh_checkbox.clicked.connect(self.toggle_auto_refresh)
        button_layout.addWidget(self.auto_refresh_checkbox)
        
        # Кнопка "Закрыть"
        self.close_button = QPushButton("Закрыть")
        self.close_button.setStyleSheet(base_button_style)
        self.close_button.clicked.connect(self.close)
        button_layout.addWidget(self.close_button)
        
        layout.addLayout(button_layout)
    
    def setup_auto_refresh(self):
        """Настраивает таймер авто-обновления логов (каждые 500мс)."""
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self._auto_refresh_logs)
        self.refresh_timer.start()
    
    def _auto_refresh_logs(self):
        """Авто-обновление логов (без скролла если пользователь читает)."""
        if not self.isVisible():
            return
        
        cursor = self.text_edit.textCursor()
        at_end = cursor.atEnd()
        
        self.refresh_logs(scroll_to_end=at_end)
    
    def refresh_logs(self, scroll_to_end=True):
        """Обновляет содержимое логов."""
        if not os.path.exists(self.log_file):
            self.text_edit.setPlainText("Логов пока нет.")
            self.status_label.setText("Файл логов не существует")
            return
        
        try:
            with open(self.log_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            if self.text_edit.toPlainText() != content:
                self.text_edit.setPlainText(content)
                
                if scroll_to_end:
                    cursor = self.text_edit.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    self.text_edit.setTextCursor(cursor)
            
            line_count = content.count('\n') + 1
            file_size = os.path.getsize(self.log_file)
            size_str = self._format_size(file_size)
            self.status_label.setText(
                f"📄 {line_count} строк | 💾 {size_str} | "
                f"🕒 Обновлено: {self._get_current_time()}"
            )
            
        except UnicodeDecodeError:
            try:
                with open(self.log_file, 'r', encoding='cp1251', errors='replace') as f:
                    content = f.read()
                self.text_edit.setPlainText(content)
            except Exception:
                try:
                    with open(self.log_file, 'rb') as f:
                        raw = f.read()
                    content = raw.decode('utf-8', errors='replace')
                    self.text_edit.setPlainText(content)
                except Exception as e2:
                    self.text_edit.setPlainText(
                        f"Не удалось прочитать логи:\n{e2}"
                    )
        
        except Exception as e:
            self.text_edit.setPlainText(f"Ошибка чтения логов: {e}")
            self.status_label.setText(f"❌ Ошибка: {e}")
    
    def toggle_auto_refresh(self):
        """Включает/выключает авто-обновление."""
        if self.auto_refresh_checkbox.isChecked():
            self.refresh_timer.start()
            self.auto_refresh_checkbox.setText("⏸ Авто-обновление: ВКЛ")
        else:
            self.refresh_timer.stop()
            self.auto_refresh_checkbox.setText("▶ Авто-обновление: ВЫКЛ")
    
    def save_logs_to_file(self):
        """Сохраняет текущие логи в выбранный TXT файл."""
        content = self.text_edit.toPlainText()
        if not content or content == "Логов пока нет.":
            QMessageBox.information(self, "Информация", "Нет логов для сохранения")
            return
        
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default_name = f"logs_export_{timestamp}.txt"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить логи",
            default_name,
            "Текстовые файлы (*.txt);;Все файлы (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            QMessageBox.information(
                self, "Успех",
                f"Логи сохранены в:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось сохранить логи:\n{e}"
            )
    
    def clear_logs(self):
        """Очищает файл логов."""
        if not os.path.exists(self.log_file):
            QMessageBox.information(self, "Информация", "Файл логов не существует")
            return
        
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            "Очистить все логи?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        try:
            with open(self.log_file, 'w', encoding='utf-8') as f:
                f.write("")
            self.refresh_logs()
            QMessageBox.information(self, "Успех", "Логи очищены")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось очистить логи:\n{e}"
            )
    
    def _format_size(self, size_bytes: int) -> str:
        """Форматирует размер файла в читаемый вид."""
        if size_bytes < 1024:
            return f"{size_bytes} Б"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} КБ"
        else:
            return f"{size_bytes / (1024 * 1024):.2f} МБ"
    
    def _get_current_time(self) -> str:
        """Возвращает текущее время в формате ЧЧ:ММ:СС."""
        import datetime
        return datetime.datetime.now().strftime("%H:%M:%S")
