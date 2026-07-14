import io
import os
import time
import pdfplumber
from typing import List, Tuple

from ozon_marker import mark_ozon_pdf_in_memory


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
    if not variant_size or ',' not in variant_size:
        return variant_size.strip() if variant_size else "", ""
    
    parts = variant_size.split(',', 1)
    article = parts[0].strip()
    size = parts[1].strip()
    return article, size


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
                        variant_size = str(row[1]).strip().replace('\n', ' ') if row[1] else ""
                        
                        if not product_name or not variant_size:
                            continue
                        
                        article, size = parse_article_and_size(variant_size)
                        if not article or not size:
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