"""Read-only client for the Trigu supplies API."""
from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.paths import get_data_path, resource_path
from core.json_storage import atomic_write_json


API_URL = "https://wbpar.ru/users/api/v1/supplies.php"
TOKEN_CONFIG_KEY = "trigu_api_key"
MARKETPLACE_ORDER_CONFIG_KEY = "trigu_marketplace_order"
CATEGORY_ORDER_CONFIG_KEY = "trigu_category_order"
DEFAULT_MARKETPLACE_ORDER = ["WB", "OZON"]


class TriguApiError(RuntimeError):
    """A user-safe Trigu API error; never contains the API key."""


def _build_ssl_context() -> ssl.SSLContext:
    """Use a verified CA bundle in source and PyInstaller builds."""
    candidates = [
        os.environ.get("SSL_CERT_FILE", ""),
        resource_path(os.path.join("certificates", "ca-bundle.pem")),
        resource_path(os.path.join("certificates", "cert.pem")),
        resource_path(os.path.join("certificates", "ca-certificates.crt")),
        ssl.get_default_verify_paths().cafile or "",
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
    ]
    for certificate_file in candidates:
        if certificate_file and os.path.isfile(certificate_file):
            try:
                return ssl.create_default_context(cafile=certificate_file)
            except (OSError, ssl.SSLError):
                continue
    return ssl.create_default_context()


def _read_config() -> dict:
    try:
        with open(get_data_path("config.json"), encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return config if isinstance(config, dict) else {}


def _normalise_marketplace_order(value) -> list[str]:
    values = [str(item).strip().upper() for item in value or []]
    if values and values[0] == "OZON":
        return ["OZON", "WB"]
    return list(DEFAULT_MARKETPLACE_ORDER)


def _normalise_category_order(value) -> list[str]:
    result, seen = [], set()
    for item in value or []:
        category = str(item or "").strip()
        key = category.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(category)
    return result


def load_api_key() -> str:
    return str(_read_config().get(TOKEN_CONFIG_KEY, "") or "").strip()


def load_marketplace_order() -> list[str]:
    return _normalise_marketplace_order(
        _read_config().get(MARKETPLACE_ORDER_CONFIG_KEY)
    )


def load_category_order() -> list[str]:
    return _normalise_category_order(
        _read_config().get(CATEGORY_ORDER_CONFIG_KEY, [])
    )


def save_trigu_preferences(
    *, marketplace_order=None, category_order=None,
) -> None:
    config = _read_config()
    if marketplace_order is not None:
        config[MARKETPLACE_ORDER_CONFIG_KEY] = (
            _normalise_marketplace_order(marketplace_order)
        )
    if category_order is not None:
        config[CATEGORY_ORDER_CONFIG_KEY] = (
            _normalise_category_order(category_order)
        )
    atomic_write_json(get_data_path("config.json"), config)


def register_categories(values) -> list[str]:
    """Persist newly observed categories after the configured ones."""
    current = load_category_order()
    seen = {item.casefold() for item in current}
    additions_by_key = {}
    for value in values:
        category = str(value or "").strip()
        key = category.casefold()
        if category and key not in seen and key not in additions_by_key:
            additions_by_key[key] = category
    additions = sorted(additions_by_key.values(), key=str.casefold)
    if additions:
        current.extend(additions)
        save_trigu_preferences(category_order=current)
    return current


def save_api_key(value: str) -> None:
    key = value.strip()
    if not key:
        raise TriguApiError("Ключ Trigu не может быть пустым")
    path = get_data_path("config.json")
    config = _read_config()
    config[TOKEN_CONFIG_KEY] = key
    try:
        atomic_write_json(path, config)
    except OSError as error:
        raise TriguApiError(f"Не удалось сохранить ключ Trigu: {error}") from error


@dataclass(frozen=True)
class TriguSupply:
    number: str
    name: str
    shop: str
    marketplace: str
    status: str
    status_label: str
    items_count: int


class TriguClient:
    def __init__(
        self, api_key: str | None = None, *, timeout: int = 25,
        ssl_context: ssl.SSLContext | None = None,
    ):
        self.api_key = (api_key or load_api_key()).strip()
        self.timeout = timeout
        self.ssl_context = ssl_context or _build_ssl_context()

    def _request(self, parameters: dict) -> dict:
        if not self.api_key:
            raise TriguApiError("Ключ Trigu не задан")
        request = Request(
            f"{API_URL}?{urlencode(parameters)}",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        try:
            with urlopen(
                request, timeout=self.timeout, context=self.ssl_context
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                body = json.loads(error.read().decode("utf-8"))
                message = body.get("error", {}).get("message", "")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                message = ""
            if error.code == 429:
                retry_after = error.headers.get("Retry-After", "")
                raise TriguApiError(
                    f"Превышен лимит запросов Trigu. Повторите через {retry_after or 'некоторое время'}"
                ) from error
            raise TriguApiError(message or f"Trigu вернул HTTP {error.code}") from error
        except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TriguApiError(f"Не удалось получить данные Trigu: {error}") from error

    def list_active_supplies(self) -> list[TriguSupply]:
        result = []
        seen_numbers = set()
        for marketplace in ("wb", "ozon"):
            page = 1
            while True:
                response = self._request({
                    "marketplace": marketplace, "page": page, "limit": 200,
                })
                for item in response.get("data", []):
                    number = str(item.get("number", ""))
                    if not number or number in seen_numbers:
                        continue
                    seen_numbers.add(number)
                    result.append(TriguSupply(
                        number=number,
                        name=str(item.get("name", "")),
                        shop=str(item.get("shop", "")),
                        marketplace=(
                            str(item.get("marketplace", marketplace)).upper()
                        ),
                        status=str(item.get("status", "")),
                        status_label=str(item.get("status_label", "")),
                        items_count=int(item.get("items_count", 0) or 0),
                    ))
                if not response.get("pagination", {}).get("has_more"):
                    break
                page += 1
        return result

    def get_supply_items(self, number: str) -> list[dict]:
        result, page = [], 1
        while True:
            response = self._request({"supply": number, "page": page, "limit": 500})
            result.extend(response.get("data", {}).get("items", []))
            if not response.get("pagination", {}).get("has_more"):
                return result
            page += 1

    def check_connection(self) -> None:
        self._request({"page": 1, "limit": 1})
