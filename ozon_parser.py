import io
import os
import re
import time
import pdfplumber
from typing import List, Tuple

from ozon_marker import mark_ozon_pdf_in_memory


KNOWN_SIZES = {
    "0",
    "XXS",
    "XS",
    "S",
    "M",
    "L",
    "XL",
    *(f"{number}XL" for number in range(2, 11)),
}
_SIZE_PATTERN = "|".join(
    re.escape(size) for size in sorted(KNOWN_SIZES, key=len, reverse=True)
)
_GROUPED_SIZE_PATTERN = rf"(?:{_SIZE_PATTERN})\s*\(\s*(?:{_SIZE_PATTERN})\s*\)"
_SIZE_VALUE_PATTERN = rf"(?:{_GROUPED_SIZE_PATTERN}|{_SIZE_PATTERN})"
_TRAILING_SIZE_PATTERNS = (
    re.compile(rf"^(?P<article>.+?)\s*,\s*(?P<size>{_SIZE_VALUE_PATTERN})\s*$", re.IGNORECASE),
    re.compile(rf"^(?P<article>.+?)\s*-\s*(?P<size>{_SIZE_VALUE_PATTERN})\s*$", re.IGNORECASE),
    re.compile(rf"^(?P<article>.+?)\s+(?P<size>{_SIZE_VALUE_PATTERN})\s*$", re.IGNORECASE),
)


def _normalize_variant_size(value: str) -> str:
    """Нормализует пробелы, сохраняя переносы внутри кодовых артикулов."""
    lines = [re.sub(r"\s+", " ", line.strip()) for line in value.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    normalized = lines[0]
    for line in lines[1:]:
        last_token = normalized.rsplit(" ", 1)[-1]
        starts_with_numeric_tail = re.match(r"^\d+\s*[,\-]", line) is not None
        code_was_wrapped = (
            " " not in last_token
            and ("_" in last_token or "-" in last_token)
            and starts_with_numeric_tail
        )
        normalized += line if code_was_wrapped else f" {line}"

    return normalized.strip()


def extract_print_location(name: str) -> str:
    LOCATION_PATTERNS = {
        "ДВОЙНОЙ": [
            "грудь+спина ",
            "двусторонняя ",
            "грудь и спина ",
            "с двух сторон "
        ],
        "СПИНА": [
            "на спине",
            "спина",
            "сзади",
            "принт на спине"
        ],
    }

    name_lower = name.lower()
    for location, patterns in LOCATION_PATTERNS.items():
        for pattern in patterns:
            if pattern in name_lower:
                return location
    return ""


def parse_article_and_size(variant_size: str) -> Tuple[str, str]:
    if not variant_size:
        return "", "0"

    normalized = _normalize_variant_size(variant_size)
    if not normalized:
        return "", "0"

    for pattern in _TRAILING_SIZE_PATTERNS:
        match = pattern.match(normalized)
        if match:
            article = match.group("article").strip()
            size = re.sub(r"\s+", "", match.group("size")).upper()
            if article:
                return article, size

    return normalized, "0"


def is_header_row(row) -> bool:
    if not row or len(row) < 2:
        return False

    row_text = " ".join([str(cell).lower() if cell else "" for cell in row])
    header_keywords = ["фото", "товар", "артикул", "кол-во", "этикетка", "номер отправления"]
    for keyword in header_keywords:
        if keyword in row_text:
            return True
    return False


def is_empty_row(row) -> bool:
    if not row:
        return True
    return all(not cell or str(cell).strip() == "" for cell in row)


def parse_ozon_supply(pdf_path: str) -> Tuple[List[str], List[str], List[str]]:
    total_start = time.perf_counter()

    try:
        marked_pdf_bytes = mark_ozon_pdf_in_memory(pdf_path)
        pdf_buffer = io.BytesIO(marked_pdf_bytes)

        articles = []
        sizes = []
        locations = []

        with pdfplumber.open(pdf_buffer) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue

                for table in tables:
                    for row in table:
                        if is_empty_row(row):
                            continue
                        if is_header_row(row):
                            continue
                        if len(row) < 2:
                            continue

                        product_name = str(row[0]).strip().replace('\n', ' ') if row[0] else ""
                        variant_size = str(row[1]).strip() if row[1] else ""

                        if not variant_size:
                            continue

                        article, size = parse_article_and_size(variant_size)
                        if not article:
                            continue

                        location = extract_print_location(product_name)

                        articles.append(article)
                        sizes.append(size)
                        locations.append(location)

        total_time = time.perf_counter() - total_start
        print(f"[OZON] {os.path.basename(pdf_path)}: {len(articles)} артикулов за {total_time*1000:.1f} мс")

        return articles, sizes, locations

    except Exception as e:
        total_time = time.perf_counter() - total_start
        print(f"[OZON] Ошибка парсинга {os.path.basename(pdf_path)}: {e} ({total_time*1000:.1f} мс)")
        return [], [], []
