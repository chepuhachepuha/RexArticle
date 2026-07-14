"""
Определение рабочих путей для приложения.
Работает одинаково и при запуске из исходников, и из собранного .exe.
"""
import os
import shutil
import sys


APP_NAME = "RexArticles"


def get_app_dir() -> str:
    """
    Возвращает директорию с программой.
    
    - При запуске из исходников: папка с main.py
    - При запуске из .exe (PyInstaller onedir): папка с .exe
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__ + "/.."))


def get_data_dir() -> str:
    """
    Возвращает директорию для данных (конфиги, индексы, логи).
    
    На macOS используется Application Support. На Windows и Linux —
    стандартная пользовательская папка данных. В режиме разработки данные
    остаются рядом с исходниками.
    """
    if not getattr(sys, "frozen", False):
        return get_app_dir()

    if sys.platform == "darwin":
        data_dir = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", APP_NAME
        )
    elif sys.platform.startswith("win"):
        data_dir = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME
        )
    else:
        data_dir = os.path.join(
            os.environ.get(
                "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
            ),
            APP_NAME,
        )

    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def resource_path(relative_path: str) -> str:
    """
    Получает абсолютный путь к ресурсу (для .qss и других файлов данных).
    Работает и в dev-режиме, и в PyInstaller bundle.
    """
    # PyInstaller хранит добавленные ресурсы в _MEIPASS.
    base_path = getattr(sys, "_MEIPASS", get_app_dir())
    return os.path.join(base_path, relative_path)


def get_data_path(filename: str) -> str:
    """Возвращает путь к пользовательскому файлу и переносит шаблон при первом запуске."""
    data_path = os.path.join(get_data_dir(), filename)
    if os.path.exists(data_path):
        return data_path

    template_path = resource_path(filename)
    if os.path.isfile(template_path) and os.path.abspath(template_path) != os.path.abspath(data_path):
        os.makedirs(os.path.dirname(data_path), exist_ok=True)
        shutil.copy2(template_path, data_path)
    return data_path
