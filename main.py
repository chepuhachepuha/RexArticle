import os
import sys

from core.logger import setup_logging
setup_logging()

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QEvent, QObject, QTimer
from PySide6.QtGui import QAction, QKeySequence
from styles.theme import ThemeManager
from core.paths import ensure_settings_writable


class DockActivationFilter(QObject):
    """Возвращает скрытое окно при активации приложения из Dock."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, _object, event):
        if event.type() == QEvent.Type.ApplicationActivate and self.window.isHidden():
            QTimer.singleShot(0, self.show_window)
        return False

    def show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()


if __name__ == "__main__":
    if sys.platform == "darwin":
        os.environ.setdefault("QT_MAC_WANTS_BEST_RESOLUTION_OPENGL_SURFACE", "1")
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    app = QApplication(sys.argv)
    app.setApplicationName("RexArticles")
    app.setOrganizationName("RexArticles")
    if sys.platform.startswith("win"):
        app.setStyle("Fusion")

    try:
        ensure_settings_writable()
    except PermissionError as error:
        QMessageBox.critical(None, "RexArticles — нет доступа к папке", str(error))
        raise SystemExit(1)

    ThemeManager.follow_system(app)

    from gui.main_window import MainWindow
    window = MainWindow()

    # Dock activation and explicit Quit are macOS concepts. On Windows the
    # title-bar close button always starts the shutdown sequence.
    if sys.platform == "darwin":
        app.setQuitOnLastWindowClosed(False)
        activation_filter = DockActivationFilter(window)
        app.installEventFilter(activation_filter)

        def quit_application():
            window.allow_quit = True
            app.quit()

        quit_action = QAction("Завершить RexArticles", window)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(quit_application)
        window.menuBar().addAction(quit_action)

    window.show()
    sys.exit(app.exec())