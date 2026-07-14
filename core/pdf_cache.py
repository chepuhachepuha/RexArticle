import fitz

_pdf_text_cache = {}

def get_pdf_text(pdf_path):
    if pdf_path in _pdf_text_cache:
        return _pdf_text_cache[pdf_path]
    
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        _pdf_text_cache[pdf_path] = text
        return text
    except Exception as e:
        print(f"DEBUG: Ошибка чтения PDF {pdf_path}: {e}")
        return ""

def get_pdf_text_lines(pdf_path):
    return get_pdf_text(pdf_path).splitlines()

def invalidate_cache(pdf_path=None):
    global _pdf_text_cache
    if pdf_path is None:
        _pdf_text_cache = {}
    else:
        _pdf_text_cache.pop(pdf_path, None)

def get_cache_size():
    return len(_pdf_text_cache)

def clear_all():
    global _pdf_text_cache
    _pdf_text_cache = {}