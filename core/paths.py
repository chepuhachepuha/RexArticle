"""Application resource, settings and runtime-data paths."""
import json
import os
import shutil
import sys


APP_NAME = "RexArticles"
SETTINGS_FILES = ("config.json", "stores.json", "manual_overrides.json")


def get_app_dir() -> str:
    """Return the source directory or the directory containing the executable."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__ + "/.."))


def get_settings_dir() -> str:
    """Return sidecar settings on Windows and the native data dir elsewhere."""
    if getattr(sys, "frozen", False) and not sys.platform.startswith("win"):
        return get_runtime_data_dir()
    return get_app_dir()


def get_runtime_data_dir() -> str:
    """Return the directory for indexes, logs and other runtime data."""
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


def get_data_dir() -> str:
    """Backward-compatible runtime-data path used by indexes and logs."""
    return get_runtime_data_dir()


def resource_path(relative_path: str) -> str:
    """Return a bundled resource path for source and PyInstaller runs."""
    base_path = getattr(sys, "_MEIPASS", get_app_dir())
    return os.path.join(base_path, relative_path)


def _migrate_setting(filename: str, destination: str) -> bool:
    legacy = os.path.join(get_runtime_data_dir(), filename)
    if not (
        sys.platform.startswith("win")
        and getattr(sys, "frozen", False)
        and filename in SETTINGS_FILES
        and os.path.isfile(legacy)
        and os.path.abspath(legacy) != os.path.abspath(destination)
    ):
        return False
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if filename == "config.json":
        try:
            with open(legacy, "r", encoding="utf-8") as file:
                config = json.load(file)
            if not isinstance(config, dict):
                config = {}
            config.pop("yandex_oauth_token", None)
            temporary = destination + ".tmp"
            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(config, file, ensure_ascii=False, indent=2)
            os.replace(temporary, destination)
        except (OSError, json.JSONDecodeError):
            return False
    else:
        shutil.copy2(legacy, destination)
    return os.path.exists(destination)


def get_data_path(filename: str) -> str:
    """Return a sidecar setting path and seed/migrate it on first use."""
    data_path = os.path.join(get_settings_dir(), filename)
    if os.path.exists(data_path) or _migrate_setting(filename, data_path):
        return data_path
    template_path = resource_path(filename)
    if os.path.isfile(template_path) and os.path.abspath(template_path) != os.path.abspath(data_path):
        os.makedirs(os.path.dirname(data_path), exist_ok=True)
        shutil.copy2(template_path, data_path)
    return data_path


def ensure_settings_writable() -> None:
    """Fail clearly when settings cannot be written beside the executable."""
    directory = get_settings_dir()
    os.makedirs(directory, exist_ok=True)
    probe = os.path.join(directory, ".rexarticles-write-test.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as file:
            file.write("ok")
        os.remove(probe)
    except OSError as error:
        try:
            os.remove(probe)
        except OSError:
            pass
        raise PermissionError(
            "Папка программы недоступна для записи. Переустановите RexArticles "
            "в каталог, доступный текущему пользователю."
        ) from error