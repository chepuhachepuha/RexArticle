"""Backend-клиент REST API Яндекс Диска без GUI и внешних зависимостей."""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from core.paths import resource_path


API_BASE_URL = "https://cloud-api.yandex.net/v1/disk"
OAUTH_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
DEFAULT_CLIENT_ID = "77312c3ec72a418ebb59fd460c6940f9"
DEFAULT_REDIRECT_URI = "https://oauth.yandex.ru/verification_code"
TOKEN_ENVIRONMENT_VARIABLE = "REXARTICLES_YANDEX_TOKEN"
_session_token = None


class YandexDiskError(RuntimeError):
    """Безопасная ошибка API, не содержащая OAuth-токен."""


class AdaptiveRequestLimiter:
    """Общий лимит частоты и параллельности запросов к API одного аккаунта."""

    def __init__(self, requests_per_second: float = 4.0, concurrency: int = 4):
        self._interval = 1.0 / max(float(requests_per_second), 0.1)
        self._initial_concurrency = max(1, int(concurrency))
        self._max_concurrency = self._initial_concurrency
        self._active = 0
        self._next_request_at = 0.0
        self._blocked_until = 0.0
        self._condition = threading.Condition()

    @property
    def concurrency_limit(self) -> int:
        with self._condition:
            return self._max_concurrency

    def acquire(self) -> None:
        with self._condition:
            while True:
                now = time.monotonic()
                ready_at = max(self._next_request_at, self._blocked_until)
                if self._active < self._max_concurrency and now >= ready_at:
                    self._active += 1
                    self._next_request_at = now + self._interval
                    return
                wait_for = max(0.01, ready_at - now)
                if self._active >= self._max_concurrency:
                    wait_for = min(wait_for, 0.25)
                self._condition.wait(timeout=wait_for)

    def release(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def rate_limited(self, retry_after: float) -> None:
        """После первого 429 оставляет один запрос и блокирует до Retry-After."""
        with self._condition:
            was_parallel = self._max_concurrency > 1
            self._max_concurrency = 1
            self._blocked_until = max(
                self._blocked_until,
                time.monotonic() + max(0.0, float(retry_after)),
            )
            self._condition.notify_all()
        if was_parallel:
            print(
                "[YANDEX API] Получен HTTP 429: параллельность снижена "
                "до одного запроса до перезапуска программы"
            )


_REQUEST_LIMITER = AdaptiveRequestLimiter(requests_per_second=4.0, concurrency=4)


def _retry_after_seconds(value: str | None, fallback: float) -> float:
    if not value:
        return fallback
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return fallback


def _build_ssl_context() -> tuple[ssl.SSLContext, str]:
    """Создаёт проверяющий TLS-контекст для Python и PyInstaller-сборок."""
    candidates = [
        os.environ.get("SSL_CERT_FILE", ""),
        resource_path(os.path.join("certificates", "ca-bundle.pem")),
    ]
    candidates.extend((
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
    ))
    for certificate_file in candidates:
        if certificate_file and os.path.isfile(certificate_file):
            try:
                context = ssl.create_default_context(cafile=certificate_file)
                return context, certificate_file
            except (OSError, ssl.SSLError):
                continue
    return ssl.create_default_context(), "системное хранилище OpenSSL"


@dataclass(frozen=True)
class YandexDiskFile:
    name: str
    path: str
    size: int
    modified: str
    md5: str

    @property
    def uri(self) -> str:
        return to_yandex_uri(self.path)


def configure_yandex_token(token: str | None) -> None:
    """Задаёт токен только в памяти процесса; на диск и в логи он не попадает."""
    global _session_token
    _session_token = token.strip() if token else None


def get_yandex_token(token: str | None = None) -> str:
    """Берёт токен из аргумента, памяти или окружения, не сохраняя его на диск."""
    value = (
        token
        or _session_token
        or os.environ.get(TOKEN_ENVIRONMENT_VARIABLE, "")
    ).strip()
    if not value:
        raise YandexDiskError(
            f"OAuth-токен не задан. Передайте token или установите "
            f"{TOKEN_ENVIRONMENT_VARIABLE}."
        )
    return value


def build_authorization_url(
    client_id: str = DEFAULT_CLIENT_ID,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    """Формирует URL implicit OAuth для desktop-приложения."""
    parameters = {
        "response_type": "token",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "force_confirm": "yes",
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(parameters)}"


def normalise_disk_path(path: str) -> str:
    value = (path or "disk:/").strip().replace("\\", "/")
    if value.startswith("yadisk:"):
        value = "disk:" + value[len("yadisk:"):]
    if not value.startswith("disk:"):
        value = "disk:/" + value.lstrip("/")
    suffix = value[len("disk:"):]
    value = "disk:/" + suffix.lstrip("/")
    return value.rstrip("/") if value != "disk:/" else value


def to_yandex_uri(path: str) -> str:
    return "yadisk:" + normalise_disk_path(path)[len("disk:"):]


def is_yandex_uri(path: str) -> bool:
    return isinstance(path, str) and path.startswith("yadisk:")


def uri_to_disk_path(uri: str) -> str:
    if not is_yandex_uri(uri):
        raise ValueError(f"Не облачный путь Яндекс Диска: {uri}")
    return normalise_disk_path(uri)


class YandexDiskClient:
    """Минимальный REST-клиент с пагинацией и повторами временных ошибок."""

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 120.0,
        max_retries: int = 4,
        open_url: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep,
        request_limiter: AdaptiveRequestLimiter | None = None,
    ):
        self._token = get_yandex_token(token)
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self._open_url = open_url
        self._sleep = sleep
        self._request_limiter = request_limiter or _REQUEST_LIMITER
        self._ssl_context, self.ssl_certificate_source = _build_ssl_context()

    def _open(self, request):
        if self._open_url is not None:
            return self._open_url(request, timeout=self.timeout)
        return urlopen(
            request,
            timeout=self.timeout,
            context=self._ssl_context,
        )

    def _request_json(self, endpoint: str, params: dict | None = None) -> dict:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{API_BASE_URL}/{endpoint.lstrip('/')}{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"OAuth {self._token}",
                "Accept": "application/json",
                "User-Agent": "RexArticles/1.0",
            },
        )
        last_error = None
        for attempt in range(self.max_retries):
            self._request_limiter.acquire()
            try:
                with self._open(request) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                last_error = error
                retryable = error.code == 429 or 500 <= error.code < 600
                retry_after = (
                    error.headers.get("Retry-After") if error.headers else None
                )
                delay = _retry_after_seconds(retry_after, 2 ** attempt)
                if error.code == 429:
                    self._request_limiter.rate_limited(delay)
                if retryable and attempt + 1 < self.max_retries:
                    if error.code != 429:
                        self._sleep(min(delay, 30))
                    continue
                try:
                    payload = json.loads(error.read().decode("utf-8"))
                    description = payload.get("description") or payload.get("message")
                except Exception:
                    description = None
                raise YandexDiskError(
                    f"Яндекс Диск API вернул HTTP {error.code}"
                    + (f": {description}" if description else "")
                ) from error
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt + 1 < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
                    continue
            finally:
                self._request_limiter.release()
        raise YandexDiskError(f"Яндекс Диск недоступен: {last_error}") from last_error

    def list_directory(self, path: str = "disk:/") -> list[dict]:
        """Возвращает непосредственное содержимое папки для облачного браузера."""
        payload = self._request_json(
            "resources", {"path": normalise_disk_path(path), "limit": 1000}
        )
        items = (payload.get("_embedded") or {}).get("items") or []
        return [
            {
                "name": item.get("name") or "Без названия",
                "path": normalise_disk_path(item.get("path", "")),
                "type": item.get("type", "file"),
            }
            for item in items
        ]

    def iter_files(
        self,
        root_path: str = "disk:/",
        *,
        page_size: int = 500,
        progress_callback=None,
    ) -> Iterator[YandexDiskFile]:
        """Рекурсивно обходит только выбранную папку, не читая весь Диск."""
        root = normalise_disk_path(root_path)
        folders = deque([root])
        visited_folders = set()
        request_number = 0
        remote_seen = 0
        accepted = 0
        page_size = min(max(int(page_size), 1), 500)

        while folders:
            folder = folders.popleft()
            if folder in visited_folders:
                continue
            visited_folders.add(folder)
            offset = 0

            while True:
                payload = self._request_json(
                    "resources",
                    {
                        "path": folder,
                        "limit": page_size,
                        "offset": offset,
                    },
                )
                embedded = payload.get("_embedded") or {}
                items = embedded.get("items") or []
                total = int(embedded.get("total") or 0)
                request_number += 1
                remote_seen += len(items)

                for item in items:
                    item_path = normalise_disk_path(item.get("path", ""))
                    if item.get("type") == "dir":
                        if item_path not in visited_folders:
                            folders.append(item_path)
                        continue
                    if item.get("type") not in (None, "file"):
                        continue
                    accepted += 1
                    yield YandexDiskFile(
                        name=item.get("name") or item_path.rsplit("/", 1)[-1],
                        path=item_path,
                        size=int(item.get("size") or 0),
                        modified=item.get("modified") or "",
                        md5=item.get("md5") or "",
                    )

                if progress_callback:
                    progress_callback(request_number, remote_seen, accepted)
                offset += len(items)
                if not items or len(items) < page_size or (total and offset >= total):
                    break

    def get_last_uploaded(self, limit: int = 1000) -> list[YandexDiskFile]:
        """Возвращает последние загруженные файлы, отсортированные API по дате."""
        limit = min(max(int(limit), 1), 1000)
        payload = self._request_json(
            "resources/last-uploaded", {"limit": limit}
        )
        items = payload.get("items") or []
        self.last_uploaded_response_limit = int(payload.get("limit") or limit)
        return [
            YandexDiskFile(
                name=item.get("name") or normalise_disk_path(
                    item.get("path", "")
                ).rsplit("/", 1)[-1],
                path=normalise_disk_path(item.get("path", "")),
                size=int(item.get("size") or 0),
                modified=item.get("modified") or "",
                md5=item.get("md5") or "",
            )
            for item in items
            if item.get("type") in (None, "file")
        ]

    def get_download_url(self, path: str) -> str:
        payload = self._request_json(
            "resources/download", {"path": normalise_disk_path(path)}
        )
        href = payload.get("href")
        if not href:
            raise YandexDiskError("API не вернул ссылку для скачивания файла")
        return href

    def download(self, path: str, destination: str) -> str:
        """Скачивает облачный файл атомарно через временный файл."""
        href = self.get_download_url(path)
        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        temporary = destination + ".part"
        request = Request(href, headers={"User-Agent": "RexArticles/1.0"})
        try:
            with self._open(request) as response:
                with open(temporary, "wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            os.replace(temporary, destination)
            return destination
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise

    def download_uri(self, uri: str, destination: str) -> str:
        return self.download(uri_to_disk_path(uri), destination)
