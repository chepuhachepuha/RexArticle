"""Read-only client for the Trigu supplies API."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.paths import get_data_path


API_URL = "https://wbpar.ru/users/api/v1/supplies.php"
TOKEN_CONFIG_KEY = "trigu_api_key"


class TriguApiError(RuntimeError):
    """A user-safe Trigu API error; never contains the API key."""


def load_api_key() -> str:
    try:
        with open(get_data_path("config.json"), encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, json.JSONDecodeError):
        return ""
    return str(config.get(TOKEN_CONFIG_KEY, "") or "").strip()


def save_api_key(value: str) -> None:
    key = value.strip()
    if not key:
        raise TriguApiError("Ключ Trigu не может быть пустым")
    path = get_data_path("config.json")
    try:
        with open(path, encoding="utf-8") as file:
            config = json.load(file)
            if not isinstance(config, dict):
                config = {}
    except (OSError, json.JSONDecodeError):
        config = {}
    config[TOKEN_CONFIG_KEY] = key
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(config, file, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.remove(temporary)
        except OSError:
            pass
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
    def __init__(self, api_key: str | None = None, *, timeout: int = 25):
        self.api_key = (api_key or load_api_key()).strip()
        self.timeout = timeout

    def _request(self, parameters: dict) -> dict:
        if not self.api_key:
            raise TriguApiError("Ключ Trigu не задан")
        request = Request(
            f"{API_URL}?{urlencode(parameters)}",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
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
        result, page = [], 1
        while True:
            response = self._request({"page": page, "limit": 200})
            for item in response.get("data", []):
                result.append(TriguSupply(
                    number=str(item.get("number", "")), name=str(item.get("name", "")),
                    shop=str(item.get("shop", "")), marketplace=str(item.get("marketplace", "")).upper(),
                    status=str(item.get("status", "")), status_label=str(item.get("status_label", "")),
                    items_count=int(item.get("items_count", 0) or 0),
                ))
            if not response.get("pagination", {}).get("has_more"):
                return result
            page += 1

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
