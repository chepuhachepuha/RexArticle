"""
Быстрое определение типа поставки (WB/OZON).
Читает ТОЛЬКО первую страницу PDF для мгновенного результата.
"""
import fitz
import re

# Кэш первых страниц PDF
_first_page_cache = {}


def is_ozon_task_format_lines(lines: list) -> bool:
    """Return whether a page belongs to Ozon's task-list table format."""
    normalized = {str(line).strip().casefold() for line in lines if str(line).strip()}
    return {
        "товар", "артикул", "характеристики",
    }.issubset(normalized) and any("магазин" in line for line in normalized)


def extract_ozon_task_store_name(lines: list) -> str:
    """Read the store name from ``Магазин «…»`` in an Ozon task header."""
    text = " ".join(str(line).strip() for line in lines)
    match = re.search(r'Магазин\s*[«"]\s*([^»"]+?)\s*[»"]', text, re.IGNORECASE)
    return match.group(1).strip() if match else "UNKNOWN"


def get_first_page_lines(pdf_path: str) -> list:
    """
    Читает только первую страницу PDF — быстрое определение типа.
    Кеширует результат для повторных вызовов.
    """
    if pdf_path in _first_page_cache:
        return _first_page_cache[pdf_path]
    
    try:
        doc = fitz.open(pdf_path)
        if not doc.page_count:
            doc.close()
            return []
        
        # ✅ Только первая страница (быстро!)
        first_page = doc[0]
        text = first_page.get_text()
        doc.close()
        
        lines = text.splitlines()
        _first_page_cache[pdf_path] = lines
        return lines
    except Exception as e:
        print(f"[DETECT] Ошибка чтения PDF: {e}")
        return []


def detect_supply_type(pdf_path: str) -> dict:
    """
    Определяет тип поставки (WB или OZON) и название магазина.
    БЫСТРАЯ версия — читает только первую страницу.
    
    Returns:
        dict: {"store_name": str, "supply_type": str}
    """
    try:
        lines = get_first_page_lines(pdf_path)
        
        if not lines:
            return {"store_name": "UNKNOWN", "supply_type": "UNKNOWN"}
        
        # Новый Ozon-формат содержит полноценные заголовки таблицы и магазин
        # в строке задания. Проверяем его до общего правила для Ozon.
        if is_ozon_task_format_lines(lines):
            return {
                "store_name": extract_ozon_task_store_name(lines),
                "supply_type": "OZON",
            }

        # Определяем тип поставки
        has_wb = any("WB" in line.upper() for line in lines[:50])
        
        if has_wb:
            store_name = "UNKNOWN"
            for line in lines[:100]:  # Ищем только в первых 100 строках
                stripped = line.strip()
                if stripped.startswith("Магазин:"):
                    store_name = stripped[len("Магазин:"):].strip()
                    break
            return {"store_name": store_name, "supply_type": "WB"}
        else:
            store_name = "UNKNOWN"
            for line in lines[:100]:
                stripped = line.strip()
                if stripped.startswith("Склад:"):
                    store_name = stripped[len("Склад:"):].strip()
                    break
            return {"store_name": store_name, "supply_type": "OZON"}
    except Exception as e:
        print(f"[DETECT] Ошибка: {e}")
        return {"store_name": "UNKNOWN", "supply_type": "UNKNOWN"}


def clear_first_page_cache():
    """Очищает кэш первых страниц."""
    global _first_page_cache
    _first_page_cache = {}


if __name__ == "__main__":
    import sys
    import time
    
    if len(sys.argv) > 1:
        pdf_file = sys.argv[1]
    else:
        pdf_file = input("Введите путь к PDF: ").strip()
    
    start = time.perf_counter()
    result = detect_supply_type(pdf_file)
    elapsed = time.perf_counter() - start
    
    print(f"\n{'='*60}")
    print(f"Файл: {pdf_file}")
    print(f"📦 Магазин: {result['store_name']}")
    print(f"🏷️  Тип: {result['supply_type']}")
    print(f"⏱️  Время: {elapsed*1000:.1f} мс")
    print(f"{'='*60}\n")
