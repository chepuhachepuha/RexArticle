import json
import os
from core.json_storage import atomic_write_json

from core.paths import get_data_path

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
    return f"{article}_{size}".strip("_")

def get_override(store_name, supply_type, article, size):
    overrides = _load_to_cache()
    key = _build_key(store_name, supply_type)
    sub_key = _build_sub_key(article, size)
    return overrides.get(key, {}).get(sub_key)

def set_overrides(store_name, supply_type, article_sizes, file_path):
    overrides = _load_to_cache()
    key = _build_key(store_name, supply_type)
    store_overrides = overrides.setdefault(key, {})
    for article, size in article_sizes:
        store_overrides[_build_sub_key(article, size)] = file_path
    save_overrides(overrides)


def set_override(store_name, supply_type, article, size, file_path):
    set_overrides(store_name, supply_type, [(article, size)], file_path)

def remove_override(store_name, supply_type, article, size):
    overrides = _load_to_cache()
    key = _build_key(store_name, supply_type)
    sub_key = _build_sub_key(article, size)
    if key in overrides and sub_key in overrides[key]:
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
