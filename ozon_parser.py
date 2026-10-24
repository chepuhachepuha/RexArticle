import io
import os
import re
import time
from functools import lru_cache
import pdfplumber
from typing import List, Tuple

from ozon_marker import mark_ozon_pdf_in_memory
from core.ozon_sizes import (
    GROUPED_BASE_SIZES, STANDARD_SIZES, load_custom_sizes, normalize_size,
)


def _spaced_token(value: str) -> str:
    return r"\s*".join(re.escape(character) for character in value)


@lru_cache(maxsize=32)
def _trailing_size_patterns(custom_sizes: tuple[str, ...]):
    size_keys = {normalize_size(value) for value in (*STANDARD_SIZES, *custom_sizes)}
    grouped_bases = "|".join(
        _spaced_token(value) for value in sorted(GROUPED_BASE_SIZES, key=len, reverse=True)
    )
    grouped = rf"(?:{grouped_bases})\s*\(\s*(?:{grouped_bases})\s*\)"

    def alternatives(keys):
        exact = "|".join(
            _spaced_token(value) for value in sorted(keys, key=len, reverse=True)
        )
        return rf"(?:{grouped}|{exact})"

    comma_sizes = alternatives(size_keys)
    # A numeric value after a space or hyphen could be the final part of an
    # article. Zero keeps its existing behavior; other numbers need a comma.
    other_sizes = alternatives({value for value in size_keys if value == "0" or not value.isdecimal()})
    return (
        re.compile(rf"^(?P<article>.+?)\s*,\s*(?P<size>{comma_sizes})\s*$", re.IGNORECASE),
        re.compile(rf"^(?P<article>.+?)\s*-\s*(?P<size>{other_sizes})\s*$", re.IGNORECASE),
        re.compile(rf"^(?P<article>.+?)\s+(?P<size>{other_sizes})\s*$", re.IGNORECASE),
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

    name_lower = re.sub(r"\s+", " ", str(name).lower()).strip()
    for location, patterns in LOCATION_PATTERNS.items():
        for pattern in patterns:
            if pattern in name_lower:
                return location
    return ""


def parse_article_and_size(
    variant_size: str, custom_sizes: tuple[str, ...] | None = None
) -> Tuple[str, str]:
    if not variant_size:
        return "", "0"

    normalized = _normalize_variant_size(variant_size)
    if not normalized:
        return "", "0"

    if custom_sizes is None:
        custom_sizes = tuple(load_custom_sizes())
    for pattern in _trailing_size_patterns(tuple(custom_sizes)):
        match = pattern.match(normalized)
        if match:
            article = match.group("article").strip()
            size = normalize_size(match.group("size"))
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


def _new_ozon_format_columns(table):
    """Find the header of Ozon's task-list format and its required columns."""
    required = {"товар", "артикул", "характеристики"}
    for row_index, row in enumerate(table):
        columns = {
            str(value).strip().casefold(): index
            for index, value in enumerate(row)
            if value is not None and str(value).strip()
        }
        if required.issubset(columns):
            return row_index, columns
    return None


def _extract_task_size(characteristics: str) -> str:
    """Extract the textual size before its numeric characteristic value."""
    match = re.search(r"(?:^|\n)\s*размер\s*:\s*([^\n·]+)", characteristics, re.IGNORECASE)
    return normalize_size(match.group(1)) if match else ""


def _strip_task_size_suffix(article: str, size: str) -> str | None:
    """Remove a trailing size only when it exactly matches the characteristic."""
    if not article or not size:
        return None
    size_pattern = _spaced_token(size)
    match = re.match(
        rf"^(?P<article>.+?)\s*(?:,|-|\s)\s*{size_pattern}\s*$",
        article,
        re.IGNORECASE,
    )
    return match.group("article").strip() if match else None


def _parse_new_ozon_format(pdf_path: str, custom_sizes: tuple[str, ...]):
    """Parse Ozon task-list PDFs that already have a complete visible table."""
    articles = []
    sizes = []
    locations = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                header = _new_ozon_format_columns(table)
                if header is None:
                    continue
                header_index, columns = header
                for row in table[header_index + 1:]:
                    if is_empty_row(row):
                        continue
                    max_column = max(
                        columns["товар"], columns["артикул"], columns["характеристики"]
                    )
                    if len(row) <= max_column:
                        continue

                    product_name = str(row[columns["товар"]] or "").strip()
                    source_article = str(row[columns["артикул"]] or "").strip()
                    characteristics = str(row[columns["характеристики"]] or "").strip()
                    if not source_article:
                        continue

                    characteristic_size = _extract_task_size(characteristics)
                    if characteristic_size:
                        article = _strip_task_size_suffix(
                            source_article, characteristic_size
                        )
                        if article is None:
                            article = source_article
                            print(
                                "[OZON] Размер из характеристик не совпал с концом "
                                f"артикула: {source_article!r} / {characteristic_size!r}"
                            )
                        size = characteristic_size
                    else:
                        article, size = parse_article_and_size(
                            source_article, custom_sizes
                        )

                    if not article:
                        continue
                    articles.append(article)
                    sizes.append(size)
                    locations.append(extract_print_location(product_name))

    return articles, sizes, locations


def _has_new_ozon_format(pdf_path: str) -> bool:
    """Inspect the source PDF before deciding whether Ozon markings are needed."""
    with pdfplumber.open(pdf_path) as pdf:
        return any(
            _new_ozon_format_columns(table) is not None
            for page in pdf.pages
            for table in (page.extract_tables() or [])
        )


def _parse_standard_ozon_format(custom_sizes: tuple[str, ...], pdf_path: str):
    """Keep the existing marked-PDF parser isolated from the new table format."""
    articles = []
    sizes = []
    locations = []
    marked_pdf_bytes = mark_ozon_pdf_in_memory(pdf_path)
    pdf_buffer = io.BytesIO(marked_pdf_bytes)

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

                    article, size = parse_article_and_size(variant_size, custom_sizes)
                    if not article:
                        continue

                    articles.append(article)
                    sizes.append(size)
                    locations.append(extract_print_location(product_name))

    return articles, sizes, locations


def parse_ozon_supply(pdf_path: str) -> Tuple[List[str], List[str], List[str]]:
    total_start = time.perf_counter()

    try:
        custom_sizes = tuple(load_custom_sizes())
        if _has_new_ozon_format(pdf_path):
            articles, sizes, locations = _parse_new_ozon_format(
                pdf_path, custom_sizes
            )
        else:
            articles, sizes, locations = _parse_standard_ozon_format(
                custom_sizes, pdf_path
            )

        total_time = time.perf_counter() - total_start
        print(f"[OZON] {os.path.basename(pdf_path)}: {len(articles)} артикулов за {total_time*1000:.1f} мс")

        return articles, sizes, locations

    except Exception as e:
        total_time = time.perf_counter() - total_start
        print(f"[OZON] Ошибка парсинга {os.path.basename(pdf_path)}: {e} ({total_time*1000:.1f} мс)")
        return [], [], []
