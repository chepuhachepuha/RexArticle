"""System-aware Qt theme for Windows 11 while preserving the macOS palette."""
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


class ThemeManager:
    MAC_LIGHT = {
        "window": "#F5F5F7", "surface": "#FFFFFF", "surface_alt": "#F0F0F3",
        "text": "#1D1D1F", "muted": "#6E6E73", "border": "#D1D1D6",
        "accent": "#007AFF", "accent_hover": "#0066D6", "selection": "#0A84FF",
        "danger": "#FF3B30", "notice": "#FFF4CE", "notice_text": "#5C4400",
    }
    MAC_DARK = {
        "window": "#1C1C1E", "surface": "#2C2C2E", "surface_alt": "#3A3A3C",
        "text": "#F5F5F7", "muted": "#AEAEB2", "border": "#48484A",
        "accent": "#0A84FF", "accent_hover": "#409CFF", "selection": "#163B62",
        "danger": "#FF453A", "notice": "#4A3B12", "notice_text": "#FFE69A",
    }
    WINDOWS_LIGHT = {
        "window": "#F3F3F3", "surface": "#FFFFFF", "surface_alt": "#F9F9F9",
        "text": "#1A1A1A", "muted": "#616161", "border": "#D1D1D1",
        "accent": "#0067C0", "accent_hover": "#005A9E", "selection": "#CFE8FF",
        "danger": "#C42B1C", "notice": "#FFF4CE", "notice_text": "#5C4400",
    }
    WINDOWS_DARK = {
        "window": "#202020", "surface": "#2B2B2B", "surface_alt": "#323232",
        "text": "#FFFFFF", "muted": "#C8C8C8", "border": "#454545",
        "accent": "#60CDFF", "accent_hover": "#76D5FF", "selection": "#094771",
        "danger": "#FF99A4", "notice": "#433519", "notice_text": "#FFF0B3",
    }

    @classmethod
    def is_dark(cls, app) -> bool:
        hints = app.styleHints()
        if hasattr(hints, "colorScheme"):
            scheme = hints.colorScheme()
            if scheme != Qt.ColorScheme.Unknown:
                return scheme == Qt.ColorScheme.Dark
        return app.palette().color(QPalette.ColorRole.Window).lightness() < 128

    @classmethod
    def colors(cls, app) -> dict:
        if sys.platform.startswith("win"):
            return cls.WINDOWS_DARK if cls.is_dark(app) else cls.WINDOWS_LIGHT
        return cls.MAC_DARK if cls.is_dark(app) else cls.MAC_LIGHT

    @classmethod
    def apply(cls, app, *_):
        colors = cls.colors(app)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["surface"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["muted"]))
        app.setPalette(palette)
        app.setStyleSheet(cls.stylesheet(colors, windows=sys.platform.startswith("win")))

    @staticmethod
    def stylesheet(c: dict, *, windows: bool) -> str:
        font = "'Segoe UI Variable', 'Segoe UI', Arial" if windows else "-apple-system, BlinkMacSystemFont, 'Segoe UI', Arial"
        radius = 4 if windows else 7
        return f"""
            * {{ font-family: {font}; }}
            QMainWindow, QDialog {{ background: {c['window']}; color: {c['text']}; }}
            QWidget:disabled {{ color: {c['muted']}; }}
            QToolBar#mainToolbar {{ background: {c['surface']}; border: none; border-bottom: 1px solid {c['border']}; spacing: 8px; padding: 4px; }}
            QToolButton, QPushButton {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: {radius}px; padding: 6px 12px; min-height: 20px; }}
            QToolButton:hover, QPushButton:hover {{ background: {c['surface_alt']}; border-color: {c['muted']}; }}
            QToolButton:pressed, QPushButton:pressed {{ background: {c['border']}; }}
            QToolButton:disabled, QPushButton:disabled {{ background: {c['surface_alt']}; color: {c['muted']}; border-color: {c['border']}; }}
            QPushButton[accent='true'], QPushButton[primary='true'] {{ background: {c['accent']}; color: #FFFFFF; border-color: {c['accent']}; font-weight: 600; }}
            QPushButton[accent='true']:hover, QPushButton[primary='true']:hover {{ background: {c['accent_hover']}; }}
            QPushButton[destructive='true'] {{ color: {c['danger']}; }}
            QPushButton[sidebarItem='true'] {{ text-align: left; border: none; background: transparent; padding: 7px 10px; }}
            QPushButton[sidebarItem='true']:hover {{ background: {c['surface_alt']}; }}
            QPushButton[sidebarItem='true'][selected='true'] {{ background: {c['selection']}; color: {c['text']}; font-weight: 600; }}
            QLabel#unavailableNotice {{ background: {c['notice']}; color: {c['notice_text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 14px; }}
            QLabel#settingsTitle {{ color: {c['text']}; font-size: 24px; font-weight: 600; padding: 4px 4px 0; }}
            QLabel#settingsSubtitle, QLabel#settingsSidebarHint {{ color: {c['muted']}; font-size: 13px; }}
            QWidget#settingsSidebar {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
            QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; margin-top: 12px; padding: 14px 12px 10px; font-weight: 600; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {c['muted']}; }}
            QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QTimeEdit {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: {radius}px; padding: 6px 8px; selection-background-color: {c['accent']}; }}
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus, QTimeEdit:focus {{ border: 2px solid {c['accent']}; padding: 5px 7px; }}
            QTreeView, QTableView, QTableWidget {{ background: {c['surface']}; alternate-background-color: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; outline: none; gridline-color: {c['border']}; }}
            QTreeView::item, QTableView::item {{ padding: 4px 7px; }}
            QTreeView::item:hover, QTableView::item:hover {{ background: {c['surface_alt']}; }}
            QTreeView::item:selected, QTableView::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
            QHeaderView::section {{ background: {c['surface_alt']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']}; border-right: 1px solid {c['border']}; padding: 7px 9px; font-weight: 600; }}
            QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['surface']}; }}
            QTabBar::tab {{ background: transparent; color: {c['muted']}; border: none; border-bottom: 2px solid transparent; padding: 9px 16px; }}
            QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
            QProgressBar {{ background: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 4px; text-align: center; }}
            QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
            QMenu {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
            QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 3px; }}
            QMenu::item:selected {{ background: {c['selection']}; }}
            QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 28px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """

    @classmethod
    def follow_system(cls, app):
        cls.apply(app)
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: cls.apply(app))