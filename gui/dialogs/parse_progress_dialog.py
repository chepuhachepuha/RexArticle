import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton
)
from PySide6.QtCore import Qt, Signal


class ParseProgressDialog(QDialog):
    """
    Диалог прогресса парсинга.
    Modeless (не блокирует основное окно), всегда сверху.
    """
    
    canceled = Signal()
    
    def __init__(self, total_files, parent=None):
        super().__init__(parent)
        self.total_files = total_files
        self.completed = 0
        self.errors = 0
        
        self.setWindowTitle("Парсинг поставок")
        self.setFixedSize(450, 200)
        # Всегда поверх + modeless (не блокирует main window)
        self.setWindowFlags(
            Qt.Window | 
            Qt.WindowStaysOnTopHint | 
            Qt.CustomizeWindowHint | 
            Qt.WindowTitleHint
        )
        self.setModal(False)
        
        self.setup_ui()
        self._use_system_theme()

    def _use_system_theme(self):
        for widget in self.findChildren(QPushButton) + self.findChildren(QLabel) + self.findChildren(QProgressBar):
            widget.setStyleSheet("")
        self.cancel_button.setProperty("destructive", True)
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Заголовок
        self.title_label = QLabel("Парсинг PDF файлов...")
        self.title_label.setStyleSheet(
            "font-weight: 600; font-size: 14px; color: #212121;"
        )
        layout.addWidget(self.title_label)
        
        # Текущий файл
        self.file_label = QLabel("Подготовка...")
        self.file_label.setStyleSheet(
            "font-size: 12px; color: #757575; padding: 4px;"
        )
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        
        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(self.total_files)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(24)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                text-align: center;
                background-color: #F5F5F5;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)
        
        # Статус
        self.status_label = QLabel(f"0 из {self.total_files}")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 12px; color: #424242;")
        layout.addWidget(self.status_label)
        
        # Кнопка отмены
        self.cancel_button = QPushButton("✖ Отмена")
        self.cancel_button.setFixedHeight(36)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
            QPushButton:pressed {
                background-color: #B71C1C;
            }
        """)
        self.cancel_button.clicked.connect(self.on_cancel)
        layout.addWidget(self.cancel_button)
    
    def set_current_file(self, file_path):
        """Устанавливает имя файла, который сейчас парсится."""
        file_name = os.path.basename(file_path)
        self.file_label.setText(f"📄 Парсится: {file_name}")
    
    def on_file_completed(self, file_path):
        """Вызывается когда один PDF успешно распарсен."""
        self.completed += 1
        self.progress_bar.setValue(self.completed)
        self.status_label.setText(
            f"{self.completed} из {self.total_files} "
            f"(ошибок: {self.errors})"
        )
        file_name = os.path.basename(file_path)
        self.file_label.setText(f"✅ {file_name}")
        
        self._check_done()
    
    def on_file_error(self, file_path):
        """Вызывается при ошибке парсинга."""
        self.errors += 1
        self.completed += 1
        self.progress_bar.setValue(self.completed)
        self.status_label.setText(
            f"{self.completed} из {self.total_files} "
            f"(ошибок: {self.errors})"
        )
        file_name = os.path.basename(file_path)
        self.file_label.setText(f"❌ {file_name}")
        
        self._check_done()
    
    def _check_done(self):
        """Закрывает диалог когда все файлы обработаны."""
        if self.completed >= self.total_files:
            self.close()
    
    def on_cancel(self):
        """Отмена — эмитит сигнал и закрывает диалог."""
        self.canceled.emit()
        self.close()
