"""
Профессиональный парсер PDF-поставок WB.
Поддерживает:
- Переносы строк в артикулах, размерах, наименованиях
- Извлечение расположения принта (СПИНА, ГРУДЬ+СПИНА, и т.д.)
- Табличные поставки через pdfplumber (без промежуточного Excel)
- Fallback через PyMuPDF для нетабличных поставок
"""
import re
import os
import time
from typing import List, Tuple, Dict, Any


class SupplyParser:
    COLOR_MARKERS = {"черный", "белый"}
    SIZE_PATTERN = re.compile(r'\b(XS|S|M|L|XL|2XL|3XL|4XL|5XL|6XL)\b', re.IGNORECASE)
    LOCATION_PATTERNS = {
        "ДВОЙНОЙ": ["грудь+спина", "двусторонняя", "грудь и спина", "с двух сторон"],
        "СПИНА": ["на спине", "спина", "сзади", "принт на спине"],
    }
    STORE_REGEX = re.compile(r'Магазин:\s*(.+?)\s*Дата:', re.IGNORECASE)

    def __init__(self):
        pass

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        from core.pdf_cache import get_pdf_text
        return get_pdf_text(pdf_path)

    def extract_tables_from_pdf(self, pdf_path: str) -> List[List[List[str]]]:
        import pdfplumber
        all_tables = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            clean_table = []
                            for row in table:
                                clean_row = [
                                    str(cell).strip().replace('\n', ' ') if cell else ""
                                    for cell in row
                                ]
                                clean_table.append(clean_row)
                            all_tables.append(clean_table)
        except Exception:
            pass
        return all_tables

    def clean_lines(self, lines: List[str]) -> List[str]:
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned

    def is_color_line(self, line: str) -> bool:
        return line.lower() in self.COLOR_MARKERS

    def is_size_line(self, line: str) -> bool:
        if not self.SIZE_PATTERN.search(line):
            return False
        return True

    def is_wb_marker(self, line: str) -> bool:
        return line.strip().upper() in ["WB", "BW"]

    def extract_size_value(self, size_line: str) -> str:
        match = self.SIZE_PATTERN.search(size_line)
        if match:
            return match.group(1).upper()
        return size_line.split('(')[0].strip().upper()

    def extract_print_location(self, name_lines) -> str:
        if isinstance(name_lines, list):
            full_name = " ".join(name_lines).lower()
        else:
            full_name = str(name_lines).lower()
        for location, patterns in self.LOCATION_PATTERNS.items():
            for pattern in patterns:
                if pattern in full_name:
                    return location
        return ""

    def parse_products(self, lines: List[str]) -> Tuple[List[str], List[str], List[str], List[str]]:
        articles = []
        sizes = []
        locations = []
        names = []
        clean_lines_list = self.clean_lines(lines)
        i = 0
        while i < len(clean_lines_list):
            line = clean_lines_list[i]
            if self.is_wb_marker(line):
                if i < 1:
                    i += 1
                    continue
                article_line_1 = clean_lines_list[i - 1]
                if i < 2:
                    i += 1
                    continue
                line_above_article = clean_lines_list[i - 2]
                if self.is_color_line(line_above_article):
                    article = article_line_1
                    color_index = i - 2
                else:
                    article_line_2 = clean_lines_list[i - 2]
                    article = article_line_2 + article_line_1
                    color_index = i - 3
                if color_index < 1:
                    i += 1
                    continue
                size_line_1 = clean_lines_list[color_index - 1]
                has_open_bracket = '(' in size_line_1
                has_close_bracket = ')' in size_line_1
                if has_close_bracket and not has_open_bracket:
                    if color_index < 2:
                        i += 1
                        continue
                    size_line_2 = clean_lines_list[color_index - 2]
                    full_size = size_line_2 + " " + size_line_1
                    size_index = color_index - 2
                else:
                    full_size = size_line_1
                    size_index = color_index - 1
                size_value = self.extract_size_value(full_size)
                name_lines = []
                j = size_index - 1
                while j >= 0:
                    check_line = clean_lines_list[j]
                    if check_line.startswith("Склад:"):
                        break
                    if self.is_size_line(check_line):
                        break
                    name_lines.insert(0, check_line)
                    j -= 1
                full_name = " ".join(name_lines)
                location = self.extract_print_location(name_lines)
                if article and size_value:
                    articles.append(article)
                    sizes.append(size_value)
                    locations.append(location)
                    names.append(full_name)
                i += 1
            else:
                i += 1
        return articles, sizes, locations, names

    def parse_tables_supply(self, tables: List[List[List[str]]]) -> Tuple[str, List[str], List[str], List[str]]:
        store_name = "UNKNOWN"
        articles = []
        sizes = []
        locations = []
        for table in tables:
            col_article = None
            col_name = None
            col_size = None
            for row in table:
                row_text = " ".join(row)
                store_match = self.STORE_REGEX.search(row_text)
                if store_match and store_name == "UNKNOWN":
                    store_name = store_match.group(1).strip()
                for idx, cell_value in enumerate(row):
                    cell_lower = cell_value.lower()
                    if "артикул" in cell_lower:
                        col_article = idx
                    if "наименование" in cell_lower:
                        col_name = idx
                    if cell_lower == "размер":
                        col_size = idx
                if col_article is not None and col_size is not None:
                    break
            if col_article is None or col_size is None:
                continue
            header_found = False
            for row in table:
                if not header_found:
                    for idx, cell_value in enumerate(row):
                        cell_lower = cell_value.lower()
                        if "артикул" in cell_lower:
                            header_found = True
                            break
                    continue
                if len(row) <= max(col_article, col_size, col_name if col_name is not None else 0):
                    continue
                article = row[col_article]
                size_raw = row[col_size] if col_size < len(row) else ""
                name_raw = row[col_name] if col_name is not None and col_name < len(row) else ""
                if not article or article == "None" or article.strip() == "":
                    continue
                if article.lower() in ["артикул", "сорт", "печать", "стикер", "скан", "киз"]:
                    continue
                size_value = self.extract_size_value(size_raw) if size_raw else ""
                if not size_value:
                    continue
                location = self.extract_print_location([name_raw]) if name_raw else ""
                articles.append(article)
                sizes.append(size_value)
                locations.append(location)
        return store_name, articles, sizes, locations

    def parse_wb_supply(self, pdf_path: str) -> Dict[str, Any]:
        result = {
            "store": "UNKNOWN",
            "articles": [],
            "sizes": [],
            "locations": [],
            "source": ""
        }
        try:
            tables = self.extract_tables_from_pdf(pdf_path)
            if tables:
                store, articles, sizes, locations = self.parse_tables_supply(tables)
                if articles:
                    result["store"] = store
                    result["articles"] = articles
                    result["sizes"] = sizes
                    result["locations"] = locations
                    result["source"] = "tables"
                    return result
        except Exception:
            pass
        try:
            text = self.extract_text_from_pdf(pdf_path)
            lines = text.splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("Магазин:"):
                    result["store"] = stripped[len("Магазин:"):].strip()
                    break
                store_match = self.STORE_REGEX.search(stripped)
                if store_match:
                    result["store"] = store_match.group(1).strip()
                    break
            articles, sizes, locations, _ = self.parse_products(lines)
            result["articles"] = articles
            result["sizes"] = sizes
            result["locations"] = locations
            result["source"] = "pymupdf"
            return result
        except Exception:
            return result


def parse_wb_supply(pdf_path: str) -> Dict[str, Any]:
    """Точка входа — добавлено логирование одной строкой."""
    total_start = time.perf_counter()
    
    try:
        parser = SupplyParser()
        result = parser.parse_wb_supply(pdf_path)
        
        total_time = time.perf_counter() - total_start
        count = len(result.get("articles", []))
        print(f"[WB] {os.path.basename(pdf_path)}: {count} артикулов за {total_time*1000:.1f} мс")
        
        return result
    except Exception as e:
        total_time = time.perf_counter() - total_start
        print(f"[WB] Ошибка парсинга {os.path.basename(pdf_path)}: {e} ({total_time*1000:.1f} мс)")
        return {"store": "UNKNOWN", "articles": [], "sizes": [], "locations": [], "source": ""}