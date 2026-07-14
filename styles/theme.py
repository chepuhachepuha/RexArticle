"""Системная адаптивная тема в духе macOS Settings."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


class ThemeManager:
    """Применяет светлую или тёмную палитру, выбранную в ОС."""

    LIGHT = {
        "window": "#F5F5F7", "surface": "#FFFFFF", "surface_alt": "#F0F0F3",
        "text": "#1D1D1F", "muted": "#6E6E73", "border": "#D1D1D6",
        "accent": "#007AFF", "accent_hover": "#0066D6", "selection": "#0A84FF",
        "danger": "#FF3B30",
    }
    DARK = {
        "window": "#1C1C1E", "surface": "#2C2C2E", "surface_alt": "#3A3A3C",
        "text": "#F5F5F7", "muted": "#AEAEB2", "border": "#48484A",
        "accent": "#0A84FF", "accent_hover": "#409CFF", "selection": "#163B62",
        "danger": "#FF453A",
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
    def apply(cls, app, *_):
        colors = cls.DARK if cls.is_dark(app) else cls.LIGHT
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
        app.setPalette(palette)
        app.setStyleSheet(cls.stylesheet(colors))

    @staticmethod
    def stylesheet(c: dict) -> str:
        return f"""
            * {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; }}
            QMainWindow, QDialog {{ background: {c['window']}; color: {c['text']}; }}
            QToolBar#mainToolbar {{ background: {c['surface']}; border: none; border-bottom: 1px solid {c['border']}; spacing: 10px; padding: 0; }}
            QToolButton, QPushButton {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 6px 12px; min-height: 20px; }}
            QToolBar#mainToolbar QToolButton {{ padding: 6px 12px; min-height: 20px; font-size: 13px; font-weight: 600; border-radius: 8px; }}
            QToolButton:hover, QPushButton:hover {{ background: {c['surface_alt']}; border-color: {c['muted']}; }}
            QToolButton:pressed, QPushButton:pressed {{ background: {c['border']}; }}
            QPushButton[accent='true'] {{ background: {c['accent']}; color: #FFFFFF; border-color: {c['accent']}; font-weight: 600; }}
            QPushButton[accent='true']:hover {{ background: {c['accent_hover']}; }}
            QPushButton[destructive='true'] {{ color: {c['danger']}; }}
            QPushButton[sidebarItem='true'] {{ text-align: left; border: none; background: transparent; padding: 7px 10px; }}
            QPushButton[sidebarItem='true']:hover {{ background: {c['surface_alt']}; }}
            QPushButton[sidebarItem='true'][selected='true'] {{ background: {c['selection']}; color: #FFFFFF; font-weight: 600; }}
            QDialog#settingsWindow {{ min-width: 760px; }}
            QTabWidget#settingsTabs::pane {{ border: none; top: -1px; }}
            QTabWidget#settingsTabs > QTabBar {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
            QTabWidget#settingsTabs QTabBar::tab {{ background: transparent; color: {c['muted']}; border: none; border-bottom: 3px solid transparent; padding: 10px 18px 8px; margin: 0; font-size: 13px; font-weight: 600; min-width: 104px; }}
            QTabWidget#settingsTabs QTabBar::tab:hover {{ color: {c['text']}; background: {c['surface_alt']}; }}
            QTabWidget#settingsTabs QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; background: {c['surface']}; }}
            QWidget#settingsSidebar {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
            QLabel#settingsSidebarTitle {{ color: {c['text']}; font-size: 20px; font-weight: 700; padding: 2px 4px 0; }}
            QLabel#settingsSidebarHint {{ color: {c['muted']}; font-size: 12px; padding: 0 4px 6px; }}
            QLabel#settingsTitle {{ color: {c['text']}; font-size: 24px; font-weight: 700; padding: 4px 4px 0; }}
            QLabel#settingsSubtitle {{ color: {c['muted']}; font-size: 13px; padding: 0 4px 6px; }}
            QGroupBox[settingsCard='true'] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; margin-top: 14px; padding: 16px 14px 12px; }}
            QGroupBox[settingsCard='true']::title {{ background: transparent; color: {c['text']}; border: none; font-size: 14px; font-weight: 700; left: 12px; padding: 0 6px; }}
            QPushButton[compact='true'] {{ min-width: 26px; max-width: 36px; padding: 3px 6px; font-weight: 700; }}
            QPushButton[destructive='true'][compact='true'] {{ border-color: {c['danger']}; }}
            QPushButton#pathButton {{ background: {c['surface_alt']}; color: {c['muted']}; text-align: left; min-width: 200px; }}
            QTreeView#supplyTree {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 10px; outline: none; }}
            QTreeView#supplyTree::item {{ padding: 3px 7px; border-bottom: 1px solid {c['surface_alt']}; }}
            QTreeView#supplyTree::item:hover {{ background: {c['surface_alt']}; }}
            QHeaderView {{ background: {c['surface_alt']}; border-top-left-radius: 9px; border-top-right-radius: 9px; }}
            QHeaderView::section {{ background: {c['surface_alt']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']}; border-right: 1px solid {c['border']}; padding: 7px 9px; font-weight: 600; }}
            QHeaderView::section:first {{ border-top-left-radius: 9px; }}
            QHeaderView::section:last {{ border-top-right-radius: 9px; border-right: none; }}
            QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; margin-top: 12px; padding: 14px 12px 10px; font-weight: 600; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {c['muted']}; }}
            QLineEdit, QComboBox, QTextEdit, QTimeEdit {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 6px 8px; selection-background-color: {c['accent']}; }}
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QTimeEdit:focus {{ border: 2px solid {c['accent']}; }}
            QScrollArea, QScrollArea QWidget#storesViewport, QScrollArea QWidget#settingsViewport {{ border: none; background: transparent; }}
            QWidget#storesContainer, QWidget#settingsContainer, QWidget#typesContainer {{ background: transparent; }}
            QSplitter::handle {{ background: transparent; width: 12px; }}
            QProgressBar {{ background: {c['surface_alt']}; color: {c['text']}; border: none; border-radius: 6px; text-align: center; }}
            QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}
            QCheckBox {{ color: {c['text']}; spacing: 7px; font-size: 13px; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
            QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 28px; }}
        """

    @classmethod
    def follow_system(cls, app):
        cls.apply(app)
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: cls.apply(app))
