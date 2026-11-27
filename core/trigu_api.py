"""Read-only client for the Trigu supplies API."""
from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.paths import get_data_path, resource_path
from core.json_storage import atomic_write_json


API_URL = "https://wbpar.ru/users/api/v1/supplies.php"
TOKEN_CONFIG_KEY = "trigu_api_key"
MARKETPLACE_ORDER_CONFIG_KEY = "trigu_marketplace_order"
CATEGORY_ORDER_CONFIG_KEY = "trigu_category_order"
PROCESSED_SUPPLIES_CONFIG_KEY = "trigu_processed_supply_numbers"
PROCESSED_SUPPLY_REPORTS_CONFIG_KEY = "trigu_processed_supply_reports"
SEARCH_RUNS_CONFIG_KEY = "trigu_search_runs"
WORK_FINISHES_CONFIG_KEY = "trigu_work_finishes"
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


def load_processed_supply_numbers() -> set[str]:
    values = _read_config().get(PROCESSED_SUPPLIES_CONFIG_KEY, [])
    if not isinstance(values, list):
        return set()
    return {
        str(value).strip().upper()
        for value in values
        if str(value).strip()
    }


def mark_supply_processed(number: str) -> set[str]:
    """Persist a Trigu supply only after its print search completes."""
    number = str(number or "").strip().upper()
    processed = load_processed_supply_numbers()
    if not number or number in processed:
        return processed
    processed.add(number)
    config = _read_config()
    config[PROCESSED_SUPPLIES_CONFIG_KEY] = sorted(processed)
    atomic_write_json(get_data_path("config.json"), config)
    return processed


def _normalise_timestamp(value) -> str:
    try:
        timestamp = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return ""
    return timestamp.isoformat(timespec="seconds")


def _now_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load_timestamp_list(config_key: str) -> list[str]:
    values = _read_config().get(config_key, [])
    if not isinstance(values, list):
        return []
    return sorted({timestamp for value in values if (timestamp := _normalise_timestamp(value))})


def load_search_runs() -> list[str]:
    """Return the successful search starts used to build work intervals."""
    return _load_timestamp_list(SEARCH_RUNS_CONFIG_KEY)


def load_work_finishes() -> list[str]:
    """Return explicit user markers that close a work interval."""
    return _load_timestamp_list(WORK_FINISHES_CONFIG_KEY)


def record_search_run(timestamp: str | None = None) -> str:
    timestamp = _normalise_timestamp(timestamp) or _now_timestamp()
    config = _read_config()
    runs = load_search_runs()
    if timestamp not in runs:
        runs.append(timestamp)
        config[SEARCH_RUNS_CONFIG_KEY] = sorted(runs)
        atomic_write_json(get_data_path("config.json"), config)
    return timestamp


def mark_work_finished(timestamp: str | None = None) -> bool:
    """Close the current interval, if a search has started since the last close."""
    runs = load_search_runs()
    finishes = load_work_finishes()
    if not runs or runs[-1] <= (finishes[-1] if finishes else ""):
        return False
    timestamp = _normalise_timestamp(timestamp) or _now_timestamp()
    config = _read_config()
    if timestamp not in finishes:
        finishes.append(timestamp)
        config[WORK_FINISHES_CONFIG_KEY] = sorted(finishes)
        atomic_write_json(get_data_path("config.json"), config)
    return True


def clear_report_history() -> None:
    """Remove report-only history without touching API and UI preferences."""
    config = _read_config()
    for key in (
        PROCESSED_SUPPLIES_CONFIG_KEY,
        PROCESSED_SUPPLY_REPORTS_CONFIG_KEY,
        SEARCH_RUNS_CONFIG_KEY,
        WORK_FINISHES_CONFIG_KEY,
    ):
        config.pop(key, None)
    atomic_write_json(get_data_path("config.json"), config)


def load_processed_supply_reports() -> dict[str, dict]:
    """Return saved immutable snapshots for completed Trigu supplies."""
    reports = _read_config().get(PROCESSED_SUPPLY_REPORTS_CONFIG_KEY, {})
    if not isinstance(reports, dict):
        return {}
    result = {}
    for number, value in reports.items():
        normalised_number = str(number or "").strip().upper()
        if not normalised_number or not isinstance(value, dict):
            continue
        categories = value.get("categories", {})
        if not isinstance(categories, dict):
            categories = {}
        normalised_categories = {}
        for category, quantity in categories.items():
            name = str(category or "").strip() or "Без категории"
            try:
                amount = max(0, int(quantity))
            except (TypeError, ValueError):
                continue
            normalised_categories[name] = normalised_categories.get(name, 0) + amount
        result[normalised_number] = {
            "shop": str(value.get("shop", "") or "").strip() or "UNKNOWN",
            "marketplace": str(value.get("marketplace", "") or "").strip().upper() or "UNKNOWN",
            "categories": normalised_categories,
        }
        timestamp = _normalise_timestamp(value.get("search_started_at"))
        if timestamp:
            result[normalised_number]["search_started_at"] = timestamp
        name = str(value.get("supply_name", "") or "").strip()
        status = str(value.get("supply_status", "") or "").strip().casefold()
        if name:
            result[normalised_number]["supply_name"] = name
        if status:
            result[normalised_number]["supply_status"] = status
    return result


def save_processed_supply_report(
    number: str, shop: str, marketplace: str, category_quantities: dict,
    *, search_started_at: str | None = None, supply_name: str = "",
    supply_status: str = "",
) -> dict[str, dict]:
    """Save a supply snapshot only once its processing has completed."""
    number = str(number or "").strip().upper()
    if not number:
        return load_processed_supply_reports()

    categories = {}
    for category, quantity in (category_quantities or {}).items():
        name = str(category or "").strip() or "Без категории"
        try:
            amount = max(0, int(quantity))
        except (TypeError, ValueError):
            continue
        categories[name] = categories.get(name, 0) + amount

    config = _read_config()
    reports = load_processed_supply_reports()
    reports[number] = {
        "shop": str(shop or "").strip() or "UNKNOWN",
        "marketplace": str(marketplace or "").strip().upper() or "UNKNOWN",
        "categories": categories,
    }
    timestamp = _normalise_timestamp(search_started_at)
    if timestamp:
        reports[number]["search_started_at"] = timestamp
    name = str(supply_name or "").strip()
    status = str(supply_status or "").strip().casefold()
    if name:
        reports[number]["supply_name"] = name
    if status:
        reports[number]["supply_status"] = status
    processed = load_processed_supply_numbers()
    processed.add(number)
    config[PROCESSED_SUPPLIES_CONFIG_KEY] = sorted(processed)
    config[PROCESSED_SUPPLY_REPORTS_CONFIG_KEY] = reports
    atomic_write_json(get_data_path("config.json"), config)
    return reports


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
                    result.append(self._supply_from_data(item, marketplace))
                if not response.get("pagination", {}).get("has_more"):
                    break
                page += 1
        return result

    @staticmethod
    def _supply_from_data(item: dict, fallback_marketplace: str = "") -> TriguSupply:
        return TriguSupply(
            number=str(item.get("number", "")),
            name=str(item.get("name", "")),
            shop=str(item.get("shop", "")),
            marketplace=str(item.get("marketplace", fallback_marketplace)).upper(),
            status=str(item.get("status", "")),
            status_label=str(item.get("status_label", "")),
            items_count=int(item.get("items_count", 0) or 0),
        )

    def get_supply(self, number: str) -> TriguSupply | None:
        """Look up a supply by its number for legacy report recovery."""
        response = self._request({"number": str(number or "").strip(), "limit": 1})
        data = response.get("data", [])
        if isinstance(data, dict):
            data = [data]
        if not data:
            return None
        return self._supply_from_data(data[0])

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
