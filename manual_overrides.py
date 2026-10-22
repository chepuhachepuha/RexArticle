import json
import os

from core.paths import get_data_path
from core.text_normalization import normalize_article, preserve_article
from core.json_storage import atomic_write_json

OVERRIDES_FILE = get_data_path("manual_overrides.json")

_cache = None

def _load_to_cache():
    global _cache
    if _cache is not None:
        return _cache
    if not os.path.exists(OVERRIDES_FILE):
        _cache = {}
        return _cache
    try:
        with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                _cache = {}
            else:
                _cache = json.loads(content)
    except (json.JSONDecodeError, FileNotFoundError):
        _cache = {}
    return _cache

def _save_from_cache():
    atomic_write_json(OVERRIDES_FILE, _cache)

def invalidate_cache():
    global _cache
    _cache = None

def load_overrides():
    return _load_to_cache().copy()

def save_overrides(overrides):
    global _cache
    _cache = overrides
    _save_from_cache()

def _build_key(store_name, supply_type):
    return f"{store_name}_{supply_type}"

def _build_sub_key(article, size):
    return f"{preserve_article(article)}_{preserve_article(size)}".strip("_")

def _normalised_pair(article, size):
    return normalize_article(article), normalize_article(size)

def _split_sub_key(sub_key, known_size):
    suffix = f"_{known_size}" if known_size else ""
    if suffix and sub_key.endswith(suffix):
        return sub_key[:-len(suffix)], known_size
    if not known_size:
        return sub_key, ""
    article, separator, size = sub_key.rpartition("_")
    return (article, size) if separator else (sub_key, "")

def _find_sub_key(store_overrides, article, size):
    exact_key = _build_sub_key(article, size)
    if exact_key in store_overrides:
        return exact_key
    wanted = _normalised_pair(article, size)
    raw_size = preserve_article(size)
    for existing_key in store_overrides:
        existing_article, existing_size = _split_sub_key(existing_key, raw_size)
        if _normalised_pair(existing_article, existing_size) == wanted:
            return existing_key
    return None

def get_override(store_name, supply_type, article, size):
    overrides = _load_to_cache()
    key = _build_key(store_name, supply_type)
    store_overrides = overrides.get(key, {})
    found_key = _find_sub_key(store_overrides, article, size)
    return store_overrides.get(found_key) if found_key is not None else None

def set_overrides(store_name, supply_type, article_sizes, file_path):
    """Atomically bind one file to several article/size override keys."""
    pairs = list(article_sizes)
    if not pairs:
        return

    overrides = _load_to_cache()
    key = _build_key(store_name, supply_type)
    store_overrides = overrides.setdefault(key, {})
    for article, size in pairs:
        store_overrides[_build_sub_key(article, size)] = file_path
    save_overrides(overrides)

def set_override(store_name, supply_type, article, size, file_path):
    set_overrides(store_name, supply_type, [(article, size)], file_path)

def remove_override(store_name, supply_type, article, size):
    overrides = _load_to_cache()
    key = _build_key(store_name, supply_type)
    sub_key = _find_sub_key(overrides.get(key, {}), article, size)
    if key in overrides and sub_key is not None:
        del overrides[key][sub_key]
        if not overrides[key]:
            del overrides[key]
        save_overrides(overrides)

def get_all_overrides_for_store(store_name, supply_type):
    overrides = _load_to_cache()
    key = _build_key(store_name, supply_type)
    return overrides.get(key, {})

def clear_overrides_for_store(store_name, supply_type=None):
    overrides = _load_to_cache()
    if supply_type:
        key = _build_key(store_name, supply_type)
        if key in overrides:
            del overrides[key]
    else:
        keys_to_remove = [k for k in overrides if k.startswith(f"{store_name}_")]
        for key in keys_to_remove:
            del overrides[key]
    save_overrides(overrides)


if __name__ == "__main__":
    print("Тест set_override...")
    set_override("Бренд Рекспринт", "WB", "f_i_rats_b_04", "XS", "C:/manual/f_i_rats_b_04.png")

    print("Тест get_override...")
    path = get_override("Бренд Рекспринт", "WB", "f_i_rats_b_04", "XS")
    print(f"Найден путь: {path}")

    print("Тест get_all_overrides_for_store...")
    all_overrides = get_all_overrides_for_store("Бренд Рекспринт", "WB")
    print(f"Все overrides: {all_overrides}")

    print("Тест remove_override...")
    remove_override("Бренд Рекспринт", "WB", "f_i_rats_b_04", "XS")
    path_after = get_override("Бренд Рекспринт", "WB", "f_i_rats_b_04", "XS")
    print(f"После удаления: {path_after}")
