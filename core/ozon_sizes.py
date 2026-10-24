"""Built-in and user-defined Ozon sizes shared by parsing and settings."""
from __future__ import annotations

import json
import re
import unicodedata

from core.paths import get_data_path


STANDARD_SIZES = (
    "0", "1", "XXS", "XS", "S", "M", "L", "XL", "XXL",
    *(f"{number}XL" for number in range(2, 11)),
)
# These five forms occur in real supplies. Other pairs of legacy sizes are
# also accepted by the parser, as they were before this setting existed.
COMMON_GROUPED_SIZES = ("XS(M)", "S(L)", "M(XL)", "L(2XL)", "XL(3XL)")
GROUPED_BASE_SIZES = tuple(size for size in STANDARD_SIZES if size not in {"1", "XXL"})
CONFIG_KEY = "ozon_custom_sizes"


def normalize_size(value: str) -> str:
    """Canonical comparison key, retaining punctuation such as parentheses."""
    text = unicodedata.normalize("NFC", str(value or ""))
    return re.sub(r"\s+", "", text).upper()


def is_builtin_size(value: str) -> bool:
    key = normalize_size(value)
    if key in STANDARD_SIZES or key in COMMON_GROUPED_SIZES:
        return True
    match = re.fullmatch(r"(.+)\((.+)\)", key)
    return bool(match and all(part in GROUPED_BASE_SIZES for part in match.groups()))


def validate_custom_size(value: str, existing=(), skip=None) -> str:
    """Validate an editable entry and return the text shown in settings."""
    display = unicodedata.normalize("NFC", str(value or "")).strip()
    key = normalize_size(display)
    if not key or "," in display or "\n" in display or "\r" in display:
        raise ValueError("Введите размер без запятых и переносов строк.")
    if is_builtin_size(key) or any(
        index != skip and normalize_size(item) == key
        for index, item in enumerate(existing)
    ):
        raise ValueError("Такой размер уже есть в словаре.")
    return display


def clean_custom_sizes(values) -> list[str]:
    """Ignore malformed or duplicate saved values without changing the file."""
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            result.append(validate_custom_size(value, result))
        except ValueError:
            continue
    return result


def load_custom_sizes() -> list[str]:
    """Read the external config each time a supply starts parsing."""
    try:
        with open(get_data_path("config.json"), encoding="utf-8") as file:
            values = json.load(file).get(CONFIG_KEY, [])
    except (OSError, ValueError, AttributeError):
        return []
    return clean_custom_sizes(values)
