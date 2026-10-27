import json
import os

from core.paths import get_data_path
from core.json_storage import atomic_write_json
STORES_FILE = get_data_path("stores.json")

_stores_cache = None

KRISTABELL_SIZE_RANGES = [
    {"pattern": "(XS_XL)", "sizes": ["XS", "S", "M", "L", "XL"]},
    {"pattern": "(2XL_6XL)", "sizes": ["2XL", "3XL", "4XL", "5XL", "6XL"]},
    {"pattern": "(XS_6XL)", "sizes": ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL", "6XL"]},
]

def _default_type_data():
    return {
        "path": "", "matcher": "Simple", "copy_on_override": False,
        "override_path": "", "source": "local", "png_only": False,
        "size_ranges": [], "additional_sources": [],
    }


def normalise_additional_sources(values, primary_source=None, primary_path=""):
    def identity(source, path):
        value = str(path or "").strip()
        if source == "yandex":
            value = value.replace("\\", "/")
            if value.lower().startswith("disk:"):
                value = "disk:" + value[5:]
            value = value.rstrip("/") or "disk:/"
        else:
            value = os.path.abspath(os.path.expanduser(value)) if value else ""
            value = os.path.normcase(os.path.normpath(value)) if value else ""
        return source, value.casefold()

    result = []
    seen = set()
    primary_kind = "yandex" if primary_source == "yandex" else "local"
    primary_key = identity(primary_kind, primary_path)
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        source = "yandex" if item.get("source") == "yandex" else "local"
        path = str(item.get("path", "")).strip()
        key = identity(source, path)
        if not path or key == primary_key or key in seen:
            continue
        seen.add(key)
        result.append({"source": source, "path": path})
    return result

def _normalise_type_data(type_data):
    result = _default_type_data()
    result.update(type_data if isinstance(type_data, dict) else {})
    if "size_ranges" not in type_data:
        result["size_ranges"] = (
            [dict(item, sizes=list(item["sizes"])) for item in KRISTABELL_SIZE_RANGES]
            if result.get("matcher") == "Kristabell" else []
        )
    elif not isinstance(result.get("size_ranges"), list):
        result["size_ranges"] = []
    result["additional_sources"] = normalise_additional_sources(
        result.get("additional_sources", []), result.get("source"),
        result.get("path", ""),
    )
    return result

def load_stores():
    global _stores_cache
    if _stores_cache is not None:
        return _stores_cache.copy()

    if not os.path.exists(STORES_FILE):
        _stores_cache = {}
        return {}

    try:
        with open(STORES_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                _stores_cache = {}
                return {}
            stores = json.loads(content)
    except (json.JSONDecodeError, FileNotFoundError):
        _stores_cache = {}
        return {}

    needs_migration = False
    for store_name, value in stores.items():
        if isinstance(value, str):
            needs_migration = True
        elif isinstance(value, dict) and "path" in value:
            needs_migration = True
        elif isinstance(value, dict):
            for type_data in value.values():
                if isinstance(type_data, dict) and "size_ranges" not in type_data:
                    needs_migration = True
                elif isinstance(type_data, dict) and "additional_sources" not in type_data:
                    needs_migration = True

    if needs_migration:
        new_stores = {}
        for store_name, value in stores.items():
            if isinstance(value, str):
                new_stores[store_name] = {
                    "WB": dict(_default_type_data(), path=value)
                }
            elif isinstance(value, dict) and "path" in value:
                supply_type = value.get("type", "WB")
                new_stores[store_name] = {
                    supply_type: dict(_default_type_data(), path=value["path"])
                }
            else:
                migrated_store = {}
                for supply_type, type_data in value.items():
                    if isinstance(type_data, dict):
                        migrated_store[supply_type] = _normalise_type_data(type_data)
                if migrated_store:
                    new_stores[store_name] = migrated_store
                else:
                    new_stores[store_name] = value
        stores = new_stores
        save_stores(stores)
        print(f"DEBUG: Миграция stores.json завершена: {len(stores)} магазинов")

    _stores_cache = stores
    return stores.copy()

def save_stores(stores):
    global _stores_cache
    atomic_write_json(STORES_FILE, stores)
    _stores_cache = stores.copy()


def prune_unused_indexes(stores):
    referenced = []
    for store_data in stores.values():
        if not isinstance(store_data, dict):
            continue
        for type_data in store_data.values():
            if not isinstance(type_data, dict):
                continue
            path = str(type_data.get("path", "")).strip()
            if path:
                referenced.append({
                    "source": type_data.get("source", "local"), "path": path,
                })
            if type_data.get("copy_on_override"):
                override = str(type_data.get("override_path", "")).strip()
                if override:
                    referenced.append({"source": "local", "path": override})
            referenced.extend(type_data.get("additional_sources", []))
    try:
        from indexer import prune_unreferenced_sources
        return prune_unreferenced_sources(referenced)
    except (OSError, ValueError) as error:
        print(f"[INDEX] Не удалось очистить общие источники: {error}")
        return 0

def get_store(store_name):
    stores = load_stores()
    return stores.get(store_name, {})

def get_type_data(store_name, supply_type):
    store_data = get_store(store_name)
    return store_data.get(supply_type, {})

def add_store(store_name):
    stores = load_stores()
    if store_name in stores:
        return False
    stores[store_name] = {
        "WB": _default_type_data()
    }
    save_stores(stores)
    return True

def delete_store(store_name):
    stores = load_stores()
    if store_name not in stores:
        return False
    del stores[store_name]
    save_stores(stores)
    return True

def add_type(store_name, supply_type):
    stores = load_stores()
    if store_name not in stores:
        stores[store_name] = {}
    stores[store_name][supply_type] = _default_type_data()
    save_stores(stores)

def remove_type(store_name, supply_type):
    stores = load_stores()
    if store_name in stores and supply_type in stores[store_name]:
        del stores[store_name][supply_type]
        if not stores[store_name]:
            del stores[store_name]
        save_stores(stores)
        return True
    return False

def update_store(store_name, new_data):
    stores = load_stores()
    stores[store_name] = new_data
    save_stores(stores)

def invalidate_cache():
    global _stores_cache
    _stores_cache = None
