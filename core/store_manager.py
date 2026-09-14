import json
import os

from core.paths import get_data_path
from core.json_storage import atomic_write_json
STORES_FILE = get_data_path("stores.json")

_stores_cache = None

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

    if needs_migration:
        new_stores = {}
        for store_name, value in stores.items():
            if isinstance(value, str):
                new_stores[store_name] = {
                    "WB": {
                        "path": value,
                        "matcher": "Simple",
                        "copy_on_override": False,
                        "override_path": "",
                        "source": "local",
                        "png_only": False,
                    }
                }
            elif isinstance(value, dict) and "path" in value:
                supply_type = value.get("type", "WB")
                new_stores[store_name] = {
                    supply_type: {
                        "path": value["path"],
                        "matcher": "Simple",
                        "copy_on_override": False,
                        "override_path": "",
                        "source": "local",
                        "png_only": False,
                    }
                }
            else:
                migrated_store = {}
                for supply_type, type_data in value.items():
                    if isinstance(type_data, dict):
                        migrated_store[supply_type] = {
                            "path": type_data.get("path", ""),
                            "matcher": type_data.get("matcher", "Simple"),
                            "copy_on_override": type_data.get("copy_on_override", False),
                            "override_path": type_data.get("override_path", ""),
                            "source": type_data.get("source", "local"),
                            "png_only": bool(type_data.get("png_only", False)),
                        }
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
        "WB": {
            "path": "",
            "matcher": "Simple",
            "copy_on_override": False,
            "override_path": "",
            "source": "local",
            "png_only": False,
        }
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
    stores[store_name][supply_type] = {
        "path": "",
        "matcher": "Simple",
        "copy_on_override": False,
        "override_path": "",
        "source": "local",
        "png_only": False,
    }
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
