"""Инкрементальная индексация файлов принтов в SQLite."""
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core.paths import get_data_dir
from core.yandex_disk import YandexDiskClient, normalise_disk_path, to_yandex_uri


INDEX_DIR = os.path.join(get_data_dir(), "index")
LEGACY_STATE_DIR = os.path.join(get_data_dir(), ".index_state")
DB_FILENAME = "indexes.sqlite3"
INDEX_CACHE = {}

_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".tif", ".bmp", ".webp"}
_SCHEMA_VERSION = 2
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
    return connection


def _normalise_path(path: str) -> str:
    """Return an absolute path without changing the original filename case."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def _roots(folder_path: str, override_path: str = None) -> list[dict]:
    roots = []
    for priority, path in enumerate((override_path, folder_path)):
        if path:
            roots.append({"path": _normalise_path(path), "priority": priority})
    return roots


def _is_network_path(path: str) -> bool:
    """Определяет распространённые сетевые пути без обращения к их содержимому."""
    normalised = _normalise_path(path)
    if normalised.startswith(("//", "\\\\")):
        return True

    lowered = normalised.casefold()
    if sys.platform == "darwin" and normalised.startswith("/Volumes/"):
        # WebDAV/Finder обычно монтирует сетевые ресурсы в /Volumes.
        return any(marker in lowered for marker in ("webdav", "yandex", "dav"))

    if os.name == "nt":
        try:
            import ctypes

            drive = os.path.splitdrive(normalised)[0] + "\\"
            return bool(drive.strip("\\")) and ctypes.windll.kernel32.GetDriveTypeW(drive) == 4
        except (AttributeError, OSError):
            return False
    return False


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
        index.setdefault(info["article"], []).append(file_path)
    return index


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
            "article": article,
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
    """Лениво загружает индекс магазина из SQLite в быстрый кэш памяти."""
    if store_name in INDEX_CACHE:
        return INDEX_CACHE[store_name]

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT article, path
            FROM files
            WHERE store = ?
            ORDER BY article COLLATE NOCASE, priority, path COLLATE NOCASE
            """,
            (store_name,),
        ).fetchall()
        has_state = connection.execute(
            "SELECT 1 FROM store_state WHERE store = ?", (store_name,)
        ).fetchone()

    if rows or has_state:
        index = {}
        for article, path in rows:
            index.setdefault(article, []).append(path)
    else:
        # Совместимость до первой SQLite-синхронизации.
        index = _load_legacy_index(store_name)

    INDEX_CACHE[store_name] = index
    return index


def has_yandex_index_for_store(store_name: str, cloud_path: str) -> bool:
    """Проверяет наличие готового индекса именно для облачной папки."""
    expected_roots = [{"path": to_yandex_uri(cloud_path), "priority": 1}]
    with _connect() as connection:
        state, roots, _ = _load_store_state(connection, store_name)
    return bool(
        state is not None
        and state[0] == _SCHEMA_VERSION
        and roots == expected_roots
    )


def update_index_for_store(
    store_name: str,
    folder_path: str,
    override_path: str = None,
    force: bool = False,
    progress_callback=None,
    cancel_token=None,
) -> bool:
    """Инкрементально синхронизирует файловый индекс магазина с SQLite."""
    started = time.perf_counter()
    roots = _roots(folder_path, override_path)

    with _connect() as connection:
        previous_state, previous_roots, previous_files = _load_store_state(connection, store_name)
        schema_is_valid = (
            previous_state is not None
            and previous_state[0] == _SCHEMA_VERSION
        )
        roots_are_current = previous_roots == roots
        is_network = any(_is_network_path(root["path"]) for root in roots)
        if (
            is_network
            and schema_is_valid
            and roots_are_current
            and not force
        ):
            index = _index_from_files(previous_files)
            INDEX_CACHE[store_name] = index
            print(
                f"[INDEX] {store_name}: сетевой SQLite-индекс, {len(index)} артикулов; "
                "WebDAV не сканируется при поиске"
            )
            return False

        unavailable_network_roots = [
            root["path"]
            for root in roots
            if _is_network_path(root["path"]) and not os.path.isdir(root["path"])
        ]
        if schema_is_valid and unavailable_network_roots:
            index = _index_from_files(previous_files)
            INDEX_CACHE[store_name] = index
            print(
                f"[INDEX] {store_name}: сетевой диск недоступен, сохранён SQLite-кэш "
                f"({len(index)} артикулов)"
            )
            return False

        try:
            current_files = _scan_files(roots, progress_callback=progress_callback, cancel_token=cancel_token)
        except NetworkScanError as error:
            if not schema_is_valid:
                raise
            index = _index_from_files(previous_files)
            INDEX_CACHE[store_name] = index
            print(
                f"[INDEX] {store_name}: неполный ответ WebDAV, сохранён прежний "
                f"SQLite-индекс ({len(index)} артикулов). Причина: {error}"
            )
            return False
        if cancel_token:
            cancel_token.raise_if_cancelled()
        scanned_at_ns = time.time_ns()
        full_rebuild = not schema_is_valid

        if full_rebuild:
            added_paths = set(current_files)
            changed_paths = set()
            removed_paths = set(previous_files)
        else:
            removed_paths = previous_files.keys() - current_files.keys()
            added_paths = current_files.keys() - previous_files.keys()
            changed_paths = {
                path
                for path in current_files.keys() & previous_files.keys()
                if any(
                    current_files[path][key] != previous_files[path][key]
                    for key in ("article", "mtime_ns", "size", "priority")
                )
            }

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
                if not roots_are_current:
                    connection.execute("DELETE FROM roots WHERE store = ?", (store_name,))
                    connection.executemany(
                        "INSERT INTO roots(store, priority, path) VALUES (?, ?, ?)",
                        ((store_name, root["priority"], root["path"]) for root in roots),
                    )
                connection.executemany(
                    "DELETE FROM files WHERE store = ? AND path = ?",
                    ((store_name, path) for path in removed_paths | changed_paths),
                )
                _upsert_files(
                    connection,
                    store_name,
                    current_files,
                    added_paths | changed_paths,
                )

    index_changed = bool(full_rebuild or added_paths or changed_paths or removed_paths)
    index = _index_from_files(current_files)
    INDEX_CACHE[store_name] = index
    _remove_legacy_files(store_name)

    elapsed = (time.perf_counter() - started) * 1000
    if index_changed:
        action = (
            "полная SQLite-сборка"
            if full_rebuild
            else f"SQLite +{len(added_paths)} ~{len(changed_paths)} -{len(removed_paths)}"
        )
    else:
        action = "SQLite актуален"
    print(
        f"[INDEX] {store_name}: {action}, {len(index)} артикулов, "
        f"{len(current_files)} файлов за {elapsed:.0f} мс"
    )
    return index_changed


def _modified_to_ns(value: str) -> int:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1_000_000_000)
    except (ValueError, OverflowError):
        return 0


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
    started = time.perf_counter()
    root_uri = to_yandex_uri(cloud_path)
    roots = [{"path": root_uri, "priority": 1}]

    with _connect() as connection:
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
        if extension.lower() not in _SUPPORTED_EXTENSIONS:
            continue
        current_files[resource_uri] = {
            "article": article,
            "mtime_ns": _modified_to_ns(resource.modified),
            "size": resource.size,
            "priority": 1,
        }

    with _connect() as connection:
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
        for position, task in group:
            if cancel_token:
                cancel_token.raise_if_cancelled()
            if on_store_started:
                on_store_started(position, total, task)
            group_results[task["key"]] = update_yandex_index_for_store(
                task["key"],
                task["folder"],
                client=client,
                force=True,
                progress_callback=task.get("progress_callback"),
                cancel_token=cancel_token,
            )
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
    with _connect() as connection:
        with connection:
            connection.execute("DELETE FROM store_state WHERE store = ?", (store_name,))
    _remove_legacy_files(store_name)


def rebuild_index_for_store(store_name: str, folder_path: str, override_path: str = None):
    remove_index_for_store(store_name)
    return update_index_for_store(store_name, folder_path, override_path)
