import io
import os
import re
import time
from functools import lru_cache
import fitz
import pdfplumber
from typing import List, Tuple

from ozon_marker import mark_ozon_pdf_in_memory
from supply_detector import is_trigu_ozon_pick_list_lines
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


_TRIGU_PICK_LIST_REQUIRED_HEADERS = (
    "№", "ФОТО", "НАЗВАНИЕ", "ЦВЕТ", "АРТИКУЛ", "СТИКЕР-ЗАКАЗ",
)
_TRIGU_PICK_LIST_OPTIONAL_HEADERS = ("РАЗМЕР",)


def _join_pick_list_cell_lines(value: str) -> str:
    """Join visual wraps while preserving spaces in human-readable articles."""
    lines = [re.sub(r"\s+", " ", line.strip()) for line in value.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    result = lines[0]
    for line in lines[1:]:
        if result.endswith(("_", "-", "/")):
            result += line
        else:
            result += f" {line}"
    return result.strip()


def _trigu_pick_list_header_words(page):
    headers = {}
    supported_headers = set(
        _TRIGU_PICK_LIST_REQUIRED_HEADERS + _TRIGU_PICK_LIST_OPTIONAL_HEADERS
    )
    for word in page.get_text("words"):
        text = str(word[4]).strip().upper()
        if text in supported_headers and text not in headers:
            headers[text] = word
    if not set(_TRIGU_PICK_LIST_REQUIRED_HEADERS).issubset(headers):
        return None
    return headers


def _merge_coordinates(values, tolerance=1.0):
    groups = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def _trigu_pick_list_page_layout(page):
    headers = _trigu_pick_list_header_words(page)
    if not headers:
        return None

    header_y = (
        headers["НАЗВАНИЕ"][1] + headers["НАЗВАНИЕ"][3]
    ) / 2
    horizontal_lines = []
    table_edges = []
    minimum_width = page.rect.width * 0.80
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] == "l":
                first, second = item[1], item[2]
                if (
                    abs(first.y - second.y) <= 1.0
                    and abs(second.x - first.x) >= minimum_width
                ):
                    horizontal_lines.append((first.y + second.y) / 2)
                    table_edges.append((min(first.x, second.x), max(first.x, second.x)))
            elif item[0] == "re":
                rect = item[1]
                if rect.width >= minimum_width:
                    horizontal_lines.extend((rect.y0, rect.y1))
                    table_edges.append((rect.x0, rect.x1))

    boundaries = [
        value for value in _merge_coordinates(horizontal_lines)
        if header_y - 20 <= value <= page.rect.height - 70
    ]
    if len(boundaries) < 3 or not table_edges:
        return None

    table_left = min(edge[0] for edge in table_edges)
    table_right = max(edge[1] for edge in table_edges)
    number_header = headers["№"]
    photo_header = headers["ФОТО"]
    name_header = headers["НАЗВАНИЕ"]
    color_header = headers["ЦВЕТ"]
    article_header = headers["АРТИКУЛ"]
    sticker_header = headers["СТИКЕР-ЗАКАЗ"]
    verticals = [
        table_left,
        (number_header[2] + photo_header[0]) / 2,
        (photo_header[2] + name_header[0]) / 2,
        color_header[0] - 8,
    ]
    size_header = headers.get("РАЗМЕР")
    if size_header:
        verticals.extend([
            size_header[0] - 5,
            (size_header[2] + article_header[0]) / 2,
            sticker_header[0] - 6,
            table_right,
        ])
        size_cell = (verticals[4], verticals[5])
        article_cell = (verticals[5], verticals[6])
    else:
        verticals.extend([
            (color_header[2] + article_header[0]) / 2,
            sticker_header[0] - 6,
            table_right,
        ])
        size_cell = None
        article_cell = (verticals[4], verticals[5])
    if verticals != sorted(verticals):
        return None
    cells = {
        "name": (verticals[2], verticals[3]),
        "size": size_cell,
        "article": article_cell,
    }
    return verticals, boundaries, cells


def _draw_trigu_pick_list_grid(page, verticals, boundaries):
    """Complete the table grid in memory; the source PDF is never saved."""
    shape = page.new_shape()
    for x_value in verticals:
        shape.draw_line(
            fitz.Point(x_value, boundaries[0]),
            fitz.Point(x_value, boundaries[-1]),
        )
    for y_value in boundaries:
        shape.draw_line(
            fitz.Point(verticals[0], y_value),
            fitz.Point(verticals[-1], y_value),
        )
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()


def _parse_trigu_ozon_pick_list(pdf_path: str):
    articles, sizes, locations = [], [], []
    with fitz.open(pdf_path) as document:
        for page in document:
            layout = _trigu_pick_list_page_layout(page)
            if layout is None:
                continue
            verticals, boundaries, cells = layout
            _draw_trigu_pick_list_grid(page, verticals, boundaries)
            for top, bottom in zip(boundaries[1:], boundaries[2:]):
                number = page.get_textbox(
                    fitz.Rect(verticals[0], top, verticals[1], bottom)
                ).strip()
                if not re.fullmatch(r"\d+", number):
                    continue

                product_name = page.get_textbox(
                    fitz.Rect(cells["name"][0], top, cells["name"][1], bottom)
                ).strip()
                size_cell = cells["size"]
                size_text = (
                    page.get_textbox(fitz.Rect(size_cell[0], top, size_cell[1], bottom))
                    if size_cell else ""
                )
                article_cell = cells["article"]
                article_text = page.get_textbox(
                    fitz.Rect(article_cell[0], top, article_cell[1], bottom)
                )
                article = _join_pick_list_cell_lines(article_text)
                if not article:
                    continue
                size_lines = [line.strip() for line in size_text.splitlines() if line.strip()]
                size = normalize_size(size_lines[0]) if size_lines else "0"
                if size != "0":
                    article_without_size = _strip_task_size_suffix(article, size)
                    if article_without_size is not None:
                        article = article_without_size
                articles.append(article)
                sizes.append(size)
                locations.append(extract_print_location(product_name))
    return articles, sizes, locations


def _has_trigu_ozon_pick_list(pdf_path: str) -> bool:
    try:
        with fitz.open(pdf_path) as document:
            if not document.page_count:
                return False
            return is_trigu_ozon_pick_list_lines(
                document[0].get_text().splitlines()
            )
    except (OSError, RuntimeError, ValueError):
        return False


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
        if _has_trigu_ozon_pick_list(pdf_path):
            articles, sizes, locations = _parse_trigu_ozon_pick_list(pdf_path)
        elif _has_new_ozon_format(pdf_path):
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
