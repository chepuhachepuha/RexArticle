"""
Быстрое определение типа поставки (WB/OZON).
Читает ТОЛЬКО первую страницу PDF для мгновенного результата.
"""
import fitz

# Кэш первых страниц PDF
_first_page_cache = {}


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