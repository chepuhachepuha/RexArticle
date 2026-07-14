import os
import sys

# ✅ ВАЖНО: setup_logging() ДО создания QApplication!
from core.logger import setup_logging
setup_logging()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from styles.theme import ThemeManager


def load_stylesheet(filename):
    from core.paths import resource_path
    styles_dir = resource_path('styles')
    filepath = os.path.join(styles_dir, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


if __name__ == "__main__":
    # Нативные macOS file dialogs (sheets) и корректное имя приложения в menu bar.
    if sys.platform == "darwin":
        os.environ.setdefault("QT_MAC_WANTS_BEST_RESOLUTION_OPENGL_SURFACE", "1")
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    app = QApplication(sys.argv)
    app.setApplicationName("RexArticles")
    app.setOrganizationName("RexArticles")
    
    # Не задаём Fusion: Qt использует нативный macOS style (Aqua).
    ThemeManager.follow_system(app)
    
    from gui.main_window import MainWindow
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())
