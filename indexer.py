"""Инкрементальная индексация файлов принтов в SQLite."""
from __future__ import annotations
import json
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from core.text_normalization import normalize_article
import sys  # Compatibility for existing platform-detection tests/callers.
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core.paths import get_data_dir
from core.network_paths import is_network_path, network_operation


def is_yandex_uri(path: str) -> bool:
    """Recognise legacy settings so they can be reported as unavailable."""
    return str(path or "").casefold().startswith(("disk:", "yadisk:"))


INDEX_DIR = os.path.join(get_data_dir(), "index")
LEGACY_STATE_DIR = os.path.join(get_data_dir(), ".index_state")
DB_FILENAME = "indexes.sqlite3"
INDEX_CACHE = {}
SOURCE_INDEX_CACHE = {}

_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".tif", ".bmp", ".webp"}
_SCHEMA_VERSION = 4
_NETWORK_SCAN_RETRIES = 4


class NetworkScanError(OSError):
    """Сетевой каталог не удалось прочитать полностью."""


def _get_db_path() -> str:
    return os.path.join(INDEX_DIR, DB_FILENAME)


def _get_legacy_index_path(store_name: str) -> str:
    return os.path.join(INDEX_DIR, f"{store_name}.json")


def _get_legacy_state_path(store_name: str) -> str:
    return os.path.join(LEGACY_STATE_DIR, f"{store_name}.json")


def _get_legacy_manifest_path(store_name: str) -> str:
    return os.path.join(INDEX_DIR, f"{store_name}.manifest.json")


def _connect() -> sqlite3.Connection:
    os.makedirs(INDEX_DIR, exist_ok=True)
    connection = sqlite3.connect(_get_db_path(), timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS store_state (
            store TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            last_scan_ns INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS roots (
            store TEXT NOT NULL,
            priority INTEGER NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY (store, priority),
            FOREIGN KEY (store) REFERENCES store_state(store) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS files (
            store TEXT NOT NULL,
            path TEXT NOT NULL,
            article TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            priority INTEGER NOT NULL,
            PRIMARY KEY (store, path),
            FOREIGN KEY (store) REFERENCES store_state(store) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_files_store_article
        ON files(store, article, priority, path);

        CREATE TABLE IF NOT EXISTS source_state (
            source_key TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            root_path TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            last_scan_ns INTEGER NOT NULL DEFAULT 0,
            snapshot_ready INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS source_directories (
            source_key TEXT NOT NULL,
            path TEXT NOT NULL,
            parent_path TEXT,
            mtime_ns INTEGER NOT NULL,
            PRIMARY KEY (source_key, path),
            FOREIGN KEY (source_key) REFERENCES source_state(source_key)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS source_files (
            source_key TEXT NOT NULL,
            path TEXT NOT NULL,
            directory TEXT NOT NULL,
            article TEXT NOT NULL,
            PRIMARY KEY (source_key, path),
            FOREIGN KEY (source_key) REFERENCES source_state(source_key)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_source_files_article
        ON source_files(source_key, article, path);
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(store_state)")
    }
    if "last_scan_ns" not in columns:
        connection.execute(
            "ALTER TABLE store_state ADD COLUMN last_scan_ns INTEGER NOT NULL DEFAULT 0"
        )
        connection.commit()
    _ensure_shared_migration(connection)
    legacy_stores = [
        row[0] for row in connection.execute(
            "SELECT store FROM store_state WHERE schema_version < ?",
            (_SCHEMA_VERSION,),
        )
    ]
    if legacy_stores:
        with connection:
            for store_name in legacy_stores:
                rows = connection.execute(
                    "SELECT path, article FROM files WHERE store = ?",
                    (store_name,),
                ).fetchall()
                connection.executemany(
                    "UPDATE files SET article = ? WHERE store = ? AND path = ?",
                    [
                        (normalize_article(article), store_name, path)
                        for path, article in rows
                    ],
                )
                connection.execute(
                    "UPDATE store_state SET schema_version = ? WHERE store = ?",
                    (_SCHEMA_VERSION, store_name),
                )
        INDEX_CACHE.clear()
        print(
            f"[INDEX] SQLite обновлён до v{_SCHEMA_VERSION} без сканирования: "
            f"{len(legacy_stores)} индексов"
        )
    return connection


@contextmanager
def _open_connection():
    """Commit/roll back an index operation and always close its SQLite file."""
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _normalise_path(path: str) -> str:
    """Return an absolute path without changing the original filename case."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def normalise_source(source_type: str, path: str) -> dict:
    kind = "yandex" if source_type == "yandex" else "local"
    # Legacy cloud records remain identifiable but are no longer fetched.
    root = str(path or "").strip() if kind == "yandex" else _normalise_path(path)
    identity = f"{kind}\0{root.casefold()}".encode("utf-8")
    return {
        "source": kind,
        "path": root,
        "key": hashlib.sha256(identity).hexdigest(),
    }


def _path_belongs_to_root(file_path: str, root_path: str) -> bool:
    if is_yandex_uri(root_path):
        root = root_path.rstrip("/")
        return file_path == root or file_path.startswith(root + "/")
    try:
        return os.path.commonpath([file_path, root_path]) == root_path
    except ValueError:
        return False


def _ensure_shared_migration(connection: sqlite3.Connection):
    """Копирует v3-индексы в общие источники, не удаляя старые таблицы."""
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if user_version >= 4:
        return

    legacy_count = connection.execute(
        "SELECT COUNT(*) FROM store_state"
    ).fetchone()[0]
    backup_path = _get_db_path() + ".v3-backup"
    if legacy_count and os.path.exists(_get_db_path()) and not os.path.exists(backup_path):
        backup = sqlite3.connect(backup_path)
        try:
            with backup:
                connection.backup(backup)
        finally:
            backup.close()
        print(f"[INDEX] Создана резервная копия SQLite v3: {backup_path}")

    roots_by_store = {}
    for store, priority, path in connection.execute(
        "SELECT store, priority, path FROM roots ORDER BY store, priority"
    ):
        source_type = "yandex" if is_yandex_uri(path) else "local"
        spec = normalise_source(source_type, path)
        roots_by_store.setdefault(store, []).append((priority, spec))

    with connection:
        for entries in roots_by_store.values():
            for _priority, spec in entries:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO source_state(
                        source_key, source_type, root_path, schema_version,
                        last_scan_ns, snapshot_ready
                    ) VALUES (?, ?, ?, ?, 0, 0)
                    """,
                    (spec["key"], spec["source"], spec["path"], _SCHEMA_VERSION),
                )

        for store, path, article in connection.execute(
            "SELECT store, path, article FROM files"
        ).fetchall():
            candidates = [
                spec for _priority, spec in roots_by_store.get(store, [])
                if _path_belongs_to_root(path, spec["path"])
            ]
            if not candidates:
                continue
            spec = max(candidates, key=lambda item: len(item["path"]))
            connection.execute(
                """
                INSERT OR IGNORE INTO source_files(
                    source_key, path, directory, article
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    spec["key"], path, os.path.dirname(path),
                    normalize_article(article),
                ),
            )
        connection.execute("PRAGMA user_version = 4")
    if roots_by_store:
        print(
            f"[INDEX] SQLite v4: общие источники подготовлены для "
            f"{len(roots_by_store)} индексов без повторного чтения файлов"
        )


def _roots(folder_path: str, override_path: str = None) -> list[dict]:
    roots = []
    for priority, path in enumerate((override_path, folder_path)):
        if path:
            roots.append({"path": _normalise_path(path), "priority": priority})
    return roots


_is_network_path = is_network_path


def _scan_files(roots: list[dict], progress_callback=None, cancel_token=None) -> dict:
    """Собирает метаданные без чтения содержимого файлов."""
    files = {}
    folders_scanned = 0

    def report_folder(folder_path: str):
        nonlocal folders_scanned
        folders_scanned += 1
        print(
            f"[INDEX] Начало папки {folders_scanned}: {folder_path} "
            f"(всего найдено: {len(files)})"
        )
        if progress_callback:
            progress_callback(folder_path, folders_scanned, len(files))
        return folders_scanned

    def read_entries(folder_path: str, is_network: bool):
        attempts = _NETWORK_SCAN_RETRIES if is_network else 1
        last_error = None
        for attempt in range(attempts):
            try:
                # Важно дочитать итератор целиком внутри try: WebDAV способен
                # оборвать перечисление уже после выдачи части каталога.
                with os.scandir(folder_path) as entries:
                    return list(entries)
            except OSError as error:
                last_error = error
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (2 ** attempt))
        if is_network:
            raise NetworkScanError(
                f"WebDAV не отдал каталог после {attempts} попыток: "
                f"{folder_path} ({last_error})"
            ) from last_error
        return []

    def scan_directory(folder_path: str, priority: int, is_network: bool):
        if cancel_token:
            cancel_token.raise_if_cancelled()
        folder_number = report_folder(folder_path)
        files_before = len(files)
        entries = read_entries(folder_path, is_network)
        for entry in entries:
            if cancel_token:
                cancel_token.raise_if_cancelled()
            try:
                article, extension = os.path.splitext(entry.name)
                article = normalize_article(article)

                if is_network and extension.lower() in _SUPPORTED_EXTENSIONS:
                    # На WebDAV stat/is_file часто означает отдельный сетевой
                    # запрос на каждый файл. Для индекса достаточно имени и пути.
                    file_path = _normalise_path(entry.path)
                    files.setdefault(file_path, {
                        "article": article,
                        "mtime_ns": 0,
                        "size": 0,
                        "priority": priority,
                    })
                    continue

                if entry.is_dir(follow_symlinks=False):
                    scan_directory(entry.path, priority, is_network)
                    continue
                if is_network:
                    # Неподдерживаемый файл не требует дополнительной проверки
                    # типа — это ещё один лишний запрос к WebDAV.
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                if extension.lower() not in _SUPPORTED_EXTENSIONS:
                    continue

                file_path = _normalise_path(entry.path)
                if file_path in files:
                    continue
                stat = entry.stat(follow_symlinks=False)
                files[file_path] = {
                    "article": article,
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "priority": priority,
                }
            except OSError as error:
                if is_network:
                    raise NetworkScanError(
                        f"WebDAV не отдал данные элемента: {entry.path} ({error})"
                    ) from error
                continue
        print(
            f"[INDEX] Завершена папка {folder_number}: {folder_path} "
            f"(добавлено: {len(files) - files_before}, всего: {len(files)})"
        )
        if progress_callback:
            progress_callback(folder_path, folder_number, len(files))

    for root_info in roots:
        if cancel_token:
            cancel_token.raise_if_cancelled()
        folder_path = root_info["path"]
        if not os.path.isdir(folder_path):
            continue
        scan_directory(
            folder_path,
            root_info["priority"],
            _is_network_path(folder_path),
        )
    return files


def _index_from_files(files: dict) -> dict:
    index = {}
    ordered = sorted(
        files.items(),
        key=lambda item: (item[1]["article"].casefold(), item[1]["priority"], item[0].casefold()),
    )
    for file_path, info in ordered:
        index.setdefault(normalize_article(info["article"]), []).append(file_path)
    return index


def _load_source_state(connection, source_key: str):
    state = connection.execute(
        """
        SELECT source_type, root_path, schema_version, last_scan_ns, snapshot_ready
        FROM source_state WHERE source_key = ?
        """,
        (source_key,),
    ).fetchone()
    files = {
        path: {"article": article, "directory": directory}
        for path, directory, article in connection.execute(
            "SELECT path, directory, article FROM source_files WHERE source_key = ?",
            (source_key,),
        )
    }
    directories = {
        path: {"parent": parent, "mtime_ns": mtime_ns}
        for path, parent, mtime_ns in connection.execute(
            """
            SELECT path, parent_path, mtime_ns FROM source_directories
            WHERE source_key = ?
            """,
            (source_key,),
        )
    }
    return state, files, directories


def _scan_local_source_full(
    root_path, progress_callback=None, cancel_token=None, network=False,
):
    files = {}
    directories = {}
    folder_number = 0

    def read_entries(folder_path):
        attempts = _NETWORK_SCAN_RETRIES if network else 1
        last_error = None
        for attempt in range(attempts):
            try:
                with os.scandir(folder_path) as iterator:
                    return list(iterator)
            except OSError as error:
                last_error = error
                if not network or attempt + 1 >= attempts:
                    break
                delay = 2 ** attempt
                print(
                    f"[WEBDAV] Не удалось прочитать каталог {folder_path}: "
                    f"попытка {attempt + 1}/{attempts}, повтор через {delay} с "
                    f"({error})"
                )
                time.sleep(delay)
        if network:
            raise NetworkScanError(
                f"WebDAV оборвал перечисление каталога после {attempts} "
                f"попыток: {folder_path} ({last_error})"
            ) from last_error
        raise last_error

    def walk(folder_path, parent_path=None):
        nonlocal folder_number
        if cancel_token:
            cancel_token.raise_if_cancelled()
        mtime_ns = 0 if network else os.stat(
            folder_path, follow_symlinks=False
        ).st_mtime_ns
        directories[folder_path] = {
            "parent": parent_path,
            "mtime_ns": mtime_ns,
        }
        folder_number += 1
        if progress_callback:
            progress_callback(folder_path, folder_number, len(files))
        entries = read_entries(folder_path)
        for entry in entries:
            if cancel_token:
                cancel_token.raise_if_cancelled()
            try:
                extension = os.path.splitext(entry.name)[1].lower()
                if network and extension in _SUPPORTED_EXTENSIONS:
                    path = _normalise_path(entry.path)
                    files[path] = {
                        "article": normalize_article(
                            os.path.splitext(entry.name)[0]
                        ),
                        "directory": folder_path,
                    }
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(_normalise_path(entry.path), folder_path)
                    continue
                if extension not in _SUPPORTED_EXTENSIONS:
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                path = _normalise_path(entry.path)
                files[path] = {
                    "article": normalize_article(os.path.splitext(entry.name)[0]),
                    "directory": folder_path,
                }
            except OSError:
                continue

    walk(root_path)
    return files, directories, folder_number, 0


def _scan_local_source_incremental(
    root_path, previous_files, previous_directories,
    progress_callback=None, cancel_token=None,
):
    files = {}
    directories = {}
    children = {}
    direct_files = {}
    for path, info in previous_directories.items():
        children.setdefault(info["parent"], []).append(path)
    for path, info in previous_files.items():
        direct_files.setdefault(info["directory"], {})[path] = info

    checked = 0
    reused_files = 0

    def walk(folder_path, parent_path=None):
        nonlocal checked, reused_files
        if cancel_token:
            cancel_token.raise_if_cancelled()
        checked += 1
        stat = os.stat(folder_path, follow_symlinks=False)
        previous = previous_directories.get(folder_path)
        current = {"parent": parent_path, "mtime_ns": stat.st_mtime_ns}
        directories[folder_path] = current
        if progress_callback:
            progress_callback(folder_path, checked, len(files))

        if previous and previous["mtime_ns"] == stat.st_mtime_ns:
            known_files = direct_files.get(folder_path, {})
            files.update(known_files)
            reused_files += len(known_files)
            for child in children.get(folder_path, []):
                try:
                    walk(child, folder_path)
                except FileNotFoundError:
                    continue
            return

        with os.scandir(folder_path) as iterator:
            entries = list(iterator)
        for entry in entries:
            if cancel_token:
                cancel_token.raise_if_cancelled()
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(_normalise_path(entry.path), folder_path)
                    continue
                extension = os.path.splitext(entry.name)[1].lower()
                if extension not in _SUPPORTED_EXTENSIONS:
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                path = _normalise_path(entry.path)
                files[path] = {
                    "article": normalize_article(os.path.splitext(entry.name)[0]),
                    "directory": folder_path,
                }
            except OSError:
                continue

    walk(root_path)
    return files, directories, checked, reused_files


def get_index_for_source(source_type: str, path: str) -> dict:
    spec = normalise_source(source_type, path)
    if spec["key"] in SOURCE_INDEX_CACHE:
        return SOURCE_INDEX_CACHE[spec["key"]]
    with _open_connection() as connection:
        rows = connection.execute(
            """
            SELECT article, path FROM source_files
            WHERE source_key = ? ORDER BY article COLLATE NOCASE, path COLLATE NOCASE
            """,
            (spec["key"],),
        ).fetchall()
    index = {}
    for article, file_path in rows:
        index.setdefault(normalize_article(article), []).append(file_path)
    SOURCE_INDEX_CACHE[spec["key"]] = index
    return index


def has_source_index(source_type: str, path: str) -> bool:
    spec = normalise_source(source_type, path)
    with _open_connection() as connection:
        return connection.execute(
            "SELECT 1 FROM source_state WHERE source_key = ?",
            (spec["key"],),
        ).fetchone() is not None


def prune_unreferenced_sources(referenced_sources) -> int:
    referenced_keys = {
        normalise_source(item.get("source", "local"), item.get("path", ""))["key"]
        for item in referenced_sources
        if isinstance(item, dict) and str(item.get("path", "")).strip()
    }
    with _open_connection() as connection:
        existing = {
            row[0] for row in connection.execute("SELECT source_key FROM source_state")
        }
        unused = existing - referenced_keys
        with connection:
            connection.executemany(
                "DELETE FROM source_state WHERE source_key = ?",
                ((key,) for key in unused),
            )
    for key in unused:
        SOURCE_INDEX_CACHE.pop(key, None)
    if unused:
        INDEX_CACHE.clear()
        print(f"[INDEX] Удалено неиспользуемых общих источников: {len(unused)}")
    return len(unused)


def update_source_index(
    source_type: str,
    path: str,
    *,
    force_full=False,
    progress_callback=None,
    cancel_token=None,
) -> bool:
    spec = normalise_source(source_type, path)
    if spec["source"] == "yandex":
        return _update_yandex_source_index(
            spec, force=force_full, progress_callback=progress_callback,
            cancel_token=cancel_token,
        )

    started = time.perf_counter()
    with _open_connection() as connection:
        state, previous_files, previous_directories = _load_source_state(
            connection, spec["key"]
        )
    network = _is_network_path(spec["path"])
    if network and state and not force_full:
        get_index_for_source(spec["source"], spec["path"])
        print(f"[INDEX] Общий сетевой индекс используется без обхода: {spec['path']}")
        return False
    with network_operation(spec["path"]):
        source_available = os.path.isdir(spec["path"])
    if not source_available:
        if state:
            if network and force_full:
                raise NetworkScanError(
                    f"WebDAV-источник недоступен; прежний индекс сохранён: "
                    f"{spec['path']}"
                )
            print(
                f"[INDEX] Источник временно недоступен, сохранён индекс: "
                f"{spec['path']}"
            )
            return False
        raise FileNotFoundError(spec["path"])

    snapshot_ready = bool(state and state[4])
    with network_operation(spec["path"]):
        if force_full or not snapshot_ready:
            new_files, new_directories, checked, reused = _scan_local_source_full(
                spec["path"], progress_callback, cancel_token, network=network,
            )
            mode = "полная проверка WebDAV" if network else "полная проверка"
        else:
            new_files, new_directories, checked, reused = _scan_local_source_incremental(
                spec["path"], previous_files, previous_directories,
                progress_callback, cancel_token,
            )
            mode = "быстрая проверка"

    removed = set(previous_files) - set(new_files)
    added = set(new_files) - set(previous_files)
    changed = {
        file_path for file_path in set(previous_files) & set(new_files)
        if previous_files[file_path] != new_files[file_path]
    }
    with _open_connection() as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO source_state(
                    source_key, source_type, root_path, schema_version,
                    last_scan_ns, snapshot_ready
                ) VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_key) DO UPDATE SET
                    source_type=excluded.source_type,
                    root_path=excluded.root_path,
                    schema_version=excluded.schema_version,
                    last_scan_ns=excluded.last_scan_ns,
                    snapshot_ready=1
                """,
                (
                    spec["key"], spec["source"], spec["path"],
                    _SCHEMA_VERSION, time.time_ns(),
                ),
            )
            connection.executemany(
                "DELETE FROM source_files WHERE source_key = ? AND path = ?",
                ((spec["key"], value) for value in removed | changed),
            )
            connection.executemany(
                """
                INSERT OR REPLACE INTO source_files(
                    source_key, path, directory, article
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        spec["key"], value, new_files[value]["directory"],
                        new_files[value]["article"],
                    )
                    for value in added | changed
                ),
            )
            connection.execute(
                "DELETE FROM source_directories WHERE source_key = ?",
                (spec["key"],),
            )
            connection.executemany(
                """
                INSERT INTO source_directories(
                    source_key, path, parent_path, mtime_ns
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        spec["key"], directory, info["parent"], info["mtime_ns"]
                    )
                    for directory, info in new_directories.items()
                ),
            )
    SOURCE_INDEX_CACHE.pop(spec["key"], None)
    elapsed = (time.perf_counter() - started) * 1000
    print(
        f"[INDEX] {mode}: {spec['path']} · +{len(added)} ~{len(changed)} "
        f"-{len(removed)}, файлов {len(new_files)}, каталогов {checked}, "
        f"переиспользовано {reused} за {elapsed:.0f} мс"
    )
    return bool(added or changed or removed or not snapshot_ready)


def _load_legacy_index(store_name: str) -> dict:
    try:
        with open(_get_legacy_index_path(store_name), "r", encoding="utf-8") as file:
            index = json.load(file)
        return index if isinstance(index, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _remove_legacy_files(store_name: str):
    for path in (
        _get_legacy_index_path(store_name),
        _get_legacy_state_path(store_name),
        _get_legacy_manifest_path(store_name),
    ):
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as error:
            print(f"[INDEX] Не удалось удалить старый {os.path.basename(path)}: {error}")


def _load_store_state(connection: sqlite3.Connection, store_name: str):
    state = connection.execute(
        "SELECT schema_version, last_scan_ns FROM store_state WHERE store = ?", (store_name,)
    ).fetchone()
    roots = [
        {"path": path, "priority": priority}
        for priority, path in connection.execute(
            "SELECT priority, path FROM roots WHERE store = ? ORDER BY priority", (store_name,)
        )
    ]
    files = {
        path: {
            "article": normalize_article(article),
            "mtime_ns": mtime_ns,
            "size": size,
            "priority": priority,
        }
        for path, article, mtime_ns, size, priority in connection.execute(
            "SELECT path, article, mtime_ns, size, priority FROM files WHERE store = ?",
            (store_name,),
        )
    }
    return state, roots, files


def _upsert_files(connection: sqlite3.Connection, store_name: str, files: dict, paths):
    connection.executemany(
        """
        INSERT INTO files(store, path, article, mtime_ns, size, priority)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(store, path) DO UPDATE SET
            article = excluded.article,
            mtime_ns = excluded.mtime_ns,
            size = excluded.size,
            priority = excluded.priority
        """,
        (
            (
                store_name,
                path,
                files[path]["article"],
                files[path]["mtime_ns"],
                files[path]["size"],
                files[path]["priority"],
            )
            for path in paths
        ),
    )


def load_all_indexes_to_memory():
    """Устаревшая совместимая заглушка: индексы загружаются лениво."""


def get_index_for_store(store_name: str) -> dict:
    """Совместимый объединённый вид основных источников магазина."""
    if store_name in INDEX_CACHE:
        return INDEX_CACHE[store_name]

    with _open_connection() as connection:
        roots = connection.execute(
            "SELECT path FROM roots WHERE store = ? ORDER BY priority",
            (store_name,),
        ).fetchall()
    index = {}
    for (root_path,) in roots:
        source_type = "yandex" if is_yandex_uri(root_path) else "local"
        source_index = get_index_for_source(source_type, root_path)
        for article, paths in source_index.items():
            bucket = index.setdefault(article, [])
            for file_path in paths:
                if file_path not in bucket:
                    bucket.append(file_path)
    if not roots:
        with _open_connection() as connection:
            legacy_rows = connection.execute(
                """
                SELECT article, path FROM files WHERE store = ?
                ORDER BY article COLLATE NOCASE, priority, path COLLATE NOCASE
                """,
                (store_name,),
            ).fetchall()
        for article, file_path in legacy_rows:
            index.setdefault(normalize_article(article), []).append(file_path)
        if not legacy_rows:
            legacy_index = _load_legacy_index(store_name)
            for article, paths in legacy_index.items():
                index.setdefault(normalize_article(article), []).extend(paths)

    INDEX_CACHE[store_name] = index
    return index


def remove_missing_path_from_index(store_name: str, file_path: str) -> bool:
    """Удаляет подтверждённо отсутствующий локальный файл из SQLite и кэша."""
    if not file_path or is_yandex_uri(file_path) or os.path.exists(file_path):
        return False

    with _open_connection() as connection:
        with connection:
            legacy_cursor = connection.execute(
                "DELETE FROM files WHERE store = ? AND path = ?",
                (store_name, file_path),
            )
            source_cursor = connection.execute(
                "DELETE FROM source_files WHERE path = ?", (file_path,)
            )
    removed = legacy_cursor.rowcount > 0 or source_cursor.rowcount > 0
    if removed:
        INDEX_CACHE.pop(store_name, None)
        SOURCE_INDEX_CACHE.clear()
        print(f"[INDEX] Удалён недоступный путь {store_name}: {file_path}")
    return removed


def has_yandex_index_for_store(store_name: str, cloud_path: str) -> bool:
    """Проверяет наличие готового индекса именно для облачной папки."""
    return has_source_index("yandex", cloud_path)


def update_index_for_store(
    store_name: str,
    folder_path: str,
    override_path: str = None,
    force: bool = False,
    progress_callback=None,
    cancel_token=None,
    updated_source_keys=None,
) -> bool:
    """Обновляет общие корни и сохраняет совместимую привязку магазина."""
    roots = _roots(folder_path, override_path)
    changed = False
    for root in roots:
        spec = normalise_source("local", root["path"])
        if updated_source_keys is not None and spec["key"] in updated_source_keys:
            continue
        changed = update_source_index(
            "local", root["path"], force_full=force,
            progress_callback=progress_callback, cancel_token=cancel_token,
        ) or changed
        if updated_source_keys is not None:
            updated_source_keys.add(spec["key"])
    with _open_connection() as connection:
        with connection:
            connection.execute("DELETE FROM roots WHERE store = ?", (store_name,))
            connection.execute("DELETE FROM store_state WHERE store = ?", (store_name,))
            connection.execute(
                "INSERT INTO store_state(store, schema_version, last_scan_ns) VALUES (?, ?, ?)",
                (store_name, _SCHEMA_VERSION, time.time_ns()),
            )
            connection.executemany(
                "INSERT INTO roots(store, priority, path) VALUES (?, ?, ?)",
                ((store_name, root["priority"], root["path"]) for root in roots),
            )
    INDEX_CACHE.pop(store_name, None)
    _remove_legacy_files(store_name)
    return changed


def _modified_to_ns(value: str) -> int:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1_000_000_000)
    except (ValueError, OverflowError):
        return 0


def _update_yandex_source_index(
    spec, *, force=False, client=None, token=None,
    progress_callback=None, cancel_token=None,
):
    started = time.perf_counter()
    with _open_connection() as connection:
        state, previous_files, _directories = _load_source_state(
            connection, spec["key"]
        )
    if not force:
        if state:
            get_index_for_source("yandex", spec["path"])
            print(f"[INDEX API] Используется общий SQLite-индекс: {spec['path']}")
        else:
            print(f"[INDEX API] Общий индекс ещё не создан: {spec['path']}")
        return False

    client = client or YandexDiskClient(token)
    additions = {}

    def api_progress(page, remote_seen, accepted):
        if progress_callback:
            progress_callback(
                f"{spec['path']} · проверка API {page}", page, accepted
            )

    for resource in client.iter_files(
        spec["path"], progress_callback=api_progress
    ):
        if cancel_token:
            cancel_token.raise_if_cancelled()
        if resource.uri in previous_files:
            continue
        article, extension = os.path.splitext(resource.name)
        if extension.lower() not in _SUPPORTED_EXTENSIONS:
            continue
        additions[resource.uri] = {
            "article": normalize_article(article),
            "directory": os.path.dirname(resource.uri),
        }

    with _open_connection() as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO source_state(
                    source_key, source_type, root_path, schema_version,
                    last_scan_ns, snapshot_ready
                ) VALUES (?, 'yandex', ?, ?, ?, 0)
                ON CONFLICT(source_key) DO UPDATE SET
                    last_scan_ns=excluded.last_scan_ns,
                    schema_version=excluded.schema_version
                """,
                (spec["key"], spec["path"], _SCHEMA_VERSION, time.time_ns()),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO source_files(
                    source_key, path, directory, article
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (spec["key"], path, info["directory"], info["article"])
                    for path, info in additions.items()
                ),
            )
    SOURCE_INDEX_CACHE.pop(spec["key"], None)
    elapsed = (time.perf_counter() - started) * 1000
    print(
        f"[INDEX API] Общий источник {spec['path']}: +{len(additions)}, "
        f"всего {len(previous_files) + len(additions)} за {elapsed:.0f} мс"
    )
    return bool(additions or not state)


def update_yandex_index_for_store(
    store_name: str,
    cloud_path: str,
    *,
    token: str | None = None,
    client: YandexDiskClient | None = None,
    force: bool = False,
    progress_callback=None,
    cancel_token=None,
) -> bool:
    """Инкрементально синхронизирует SQLite через REST API Яндекс Диска."""
    spec = normalise_source("yandex", cloud_path)
    changed = _update_yandex_source_index(
        spec, force=force, client=client, token=token,
        progress_callback=progress_callback, cancel_token=cancel_token,
    )
    with _open_connection() as connection:
        with connection:
            connection.execute("DELETE FROM roots WHERE store = ?", (store_name,))
            connection.execute("DELETE FROM store_state WHERE store = ?", (store_name,))
            connection.execute(
                "INSERT INTO store_state(store, schema_version, last_scan_ns) VALUES (?, ?, ?)",
                (store_name, _SCHEMA_VERSION, time.time_ns()),
            )
            connection.execute(
                "INSERT INTO roots(store, priority, path) VALUES (?, 1, ?)",
                (store_name, spec["path"]),
            )
    INDEX_CACHE.pop(store_name, None)
    return changed

    # Legacy v3 implementation kept below as a readable migration reference.
    started = time.perf_counter()
    root_uri = to_yandex_uri(cloud_path)
    roots = [{"path": root_uri, "priority": 1}]

    with _open_connection() as connection:
        previous_state, previous_roots, previous_files = _load_store_state(
            connection, store_name
        )
    schema_is_valid = (
        previous_state is not None and previous_state[0] == _SCHEMA_VERSION
    )
    roots_are_current = previous_roots == roots
    full_rebuild = not schema_is_valid or not roots_are_current
    if not force:
        if schema_is_valid and roots_are_current:
            index = _index_from_files(previous_files)
            INDEX_CACHE[store_name] = index
            print(
                f"[INDEX API] {store_name}: локальный SQLite-индекс, "
                f"{len(index)} артикулов; облако не опрашивается при поиске"
            )
        else:
            INDEX_CACHE[store_name] = {}
            print(
                f"[INDEX API] {store_name}: готового индекса для {root_uri} нет; "
                "облако не опрашивается — выполните ручную полную сверку"
            )
        return False

    client = client or YandexDiskClient(token)
    current_files = {}
    print(
        f"[INDEX API] {store_name}: поиск новых папок и файлов в облаке; "
        f"текущая база содержит {len(previous_files)} файлов, "
        "удаления не проверяются"
    )

    def api_progress(page, remote_seen, accepted):
        message = f"{root_uri} · проверка API {page}"
        print(
            f"[INDEX API] {store_name}: проверка {page}, "
            f"просмотрено {remote_seen}, найдено в выбранной папке {accepted}; "
            "SQLite пока не изменяется"
        )
        if progress_callback:
            progress_callback(message, page, accepted)

    for resource in client.iter_files(
        cloud_path, progress_callback=api_progress
    ):
        if cancel_token:
            cancel_token.raise_if_cancelled()
        resource_uri = resource.uri
        if not full_rebuild and resource_uri in previous_files:
            # Известный путь сразу отбрасывается: полный удалённый/изменённый
            # снимок папки в памяти больше не строится.
            continue
        article, extension = os.path.splitext(resource.name)
        article = normalize_article(article)
        if extension.lower() not in _SUPPORTED_EXTENSIONS:
            continue
        current_files[resource_uri] = {
            "article": article,
            "mtime_ns": _modified_to_ns(resource.modified),
            "size": resource.size,
            "priority": 1,
        }

    with _open_connection() as connection:
        if full_rebuild:
            added_paths = set(current_files)
            changed_paths = set()
            removed_paths = set(previous_files)
        else:
            # Повторное облачное обновление намеренно append-only. Исчезнувшие
            # и изменившиеся пути не ищем: добавляем только ранее неизвестные.
            removed_paths = set()
            added_paths = set(current_files)
            changed_paths = set()

        if cancel_token:
            cancel_token.raise_if_cancelled()
        scanned_at_ns = time.time_ns()
        with connection:
            if full_rebuild:
                connection.execute("DELETE FROM store_state WHERE store = ?", (store_name,))
                connection.execute(
                    "INSERT INTO store_state(store, schema_version, last_scan_ns) VALUES (?, ?, ?)",
                    (store_name, _SCHEMA_VERSION, scanned_at_ns),
                )
                connection.executemany(
                    "INSERT INTO roots(store, priority, path) VALUES (?, ?, ?)",
                    ((store_name, root["priority"], root["path"]) for root in roots),
                )
                _upsert_files(connection, store_name, current_files, current_files.keys())
            else:
                connection.execute(
                    "UPDATE store_state SET last_scan_ns = ? WHERE store = ?",
                    (scanned_at_ns, store_name),
                )
                _upsert_files(
                    connection,
                    store_name,
                    current_files,
                    added_paths,
                )

    changed = bool(full_rebuild or added_paths or changed_paths or removed_paths)
    if not full_rebuild:
        merged_files = dict(previous_files)
        for path in added_paths:
            merged_files[path] = current_files[path]
        current_files = merged_files
    index = _index_from_files(current_files)
    INDEX_CACHE[store_name] = index
    elapsed = (time.perf_counter() - started) * 1000
    if full_rebuild:
        action = (
            "первая SQLite-сборка: прежнее состояние отсутствовало"
            if not schema_is_valid
            else "корневая папка изменена: индекс создан для нового пути"
        )
    elif added_paths:
        action = (
            f"append-only SQLite: +{len(added_paths)}; "
            "удаления и изменения не проверялись"
        )
    else:
        action = (
            "новых файлов нет; существующая SQLite-база сохранена, "
            "удаления не проверялись"
        )
    print(
        f"[INDEX API] {store_name}: {action}, {len(index)} артикулов, "
        f"{len(current_files)} файлов за {elapsed:.0f} мс"
    )
    return changed


def _cloud_paths_overlap(first: str, second: str) -> bool:
    first = normalise_disk_path(first).rstrip("/")
    second = normalise_disk_path(second).rstrip("/")
    return (
        first == second
        or first.startswith(second + "/")
        or second.startswith(first + "/")
    )


def _cloud_task_groups(indexed_tasks):
    groups = []
    for indexed_task in indexed_tasks:
        matching = [
            group
            for group in groups
            if any(
                _cloud_paths_overlap(
                    indexed_task[1]["folder"], other[1]["folder"]
                )
                for other in group
            )
        ]
        if not matching:
            groups.append([indexed_task])
            continue
        destination = matching[0]
        destination.append(indexed_task)
        for extra in matching[1:]:
            destination.extend(extra)
            groups.remove(extra)
    return groups


def update_yandex_indexes(
    tasks,
    *,
    max_workers: int = 4,
    on_store_started=None,
    on_store_finished=None,
    cancel_token=None,
) -> dict:
    """Append-only обновляет облачные магазины четырьмя независимыми потоками."""
    started = time.perf_counter()
    tasks = list(tasks)
    if not tasks:
        return {}
    total = len(tasks)
    groups = _cloud_task_groups(list(enumerate(tasks, start=1)))
    for group in groups:
        if len(group) > 1:
            paths = ", ".join(task["folder"] for _, task in group)
            print(
                "[INDEX API] Пересекающиеся папки будут обработаны "
                f"последовательно: {paths}"
            )

    results = {}

    def run_group(group):
        client = YandexDiskClient()
        group_results = {}
        processed_sources = {}
        for position, task in group:
            if cancel_token:
                cancel_token.raise_if_cancelled()
            if on_store_started:
                on_store_started(position, total, task)
            source_key = normalise_source("yandex", task["folder"])["key"]
            duplicate = source_key in processed_sources
            changed = update_yandex_index_for_store(
                task["key"],
                task["folder"],
                client=client,
                force=not duplicate,
                progress_callback=task.get("progress_callback"),
                cancel_token=cancel_token,
            )
            if not duplicate:
                processed_sources[source_key] = changed
            group_results[task["key"]] = processed_sources[source_key]
            if on_store_finished:
                on_store_finished(position, total, task)
        return group_results

    workers = min(max(1, int(max_workers)), 4, len(groups))
    print(
        f"[INDEX API] Append-only обновление: {len(groups)} независимых групп, "
        f"одновременно не больше {workers}"
    )
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="yandex-index",
    ) as executor:
        futures = [executor.submit(run_group, group) for group in groups]
        for future in futures:
            results.update(future.result())
    elapsed = (time.perf_counter() - started) * 1000
    changed_count = sum(bool(changed) for changed in results.values())
    print(
        f"[INDEX API] Обновление завершено: {len(results)} индексов, "
        f"изменены {changed_count}, время {elapsed:.0f} мс"
    )
    return results


def build_index_for_store(store_name: str, folder_path: str, override_path: str = None):
    return update_index_for_store(store_name, folder_path, override_path)


def remove_index_for_store(store_name: str):
    """Удаляет из SQLite только индекс выбранного магазина/типа."""
    print(f"[INDEX] Явное удаление индекса магазина: {store_name}")
    INDEX_CACHE.pop(store_name, None)
    with _open_connection() as connection:
        with connection:
            connection.execute("DELETE FROM store_state WHERE store = ?", (store_name,))
    _remove_legacy_files(store_name)


def rebuild_index_for_store(store_name: str, folder_path: str, override_path: str = None):
    remove_index_for_store(store_name)
    return update_index_for_store(store_name, folder_path, override_path)
