"""Persistent user corrections for articles read from supply PDFs."""
from __future__ import annotations

import json

from core.json_storage import atomic_write_json
from core.paths import get_data_path
from core.text_normalization import normalize_article, preserve_article


CORRECTIONS_FILE = get_data_path("article_corrections.json")
_cache = None


def _store_key(store_name, supply_type):
    return f"{store_name}_{supply_type}"


def _load_to_cache():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(CORRECTIONS_FILE, encoding="utf-8") as file:
            loaded = json.load(file)
    except (OSError, json.JSONDecodeError):
        loaded = {}
    _cache = loaded if isinstance(loaded, dict) else {}
    return _cache


def invalidate_cache():
    global _cache
    _cache = None


def get_corrected_article(store_name, supply_type, detected_article):
    """Return the saved factual article, or the original value if absent."""
    original = preserve_article(detected_article)
    corrections = _load_to_cache().get(_store_key(store_name, supply_type), {})
    if not isinstance(corrections, dict):
        return original
    if original in corrections:
        return preserve_article(corrections[original])

    wanted = normalize_article(original)
    for saved_article, factual_article in corrections.items():
        if normalize_article(saved_article) == wanted:
            return preserve_article(factual_article)
    return original


def set_article_correction(store_name, supply_type, detected_article, factual_article):
    """Save or remove a correction and return the resulting factual article."""
    detected = preserve_article(detected_article).strip()
    factual = preserve_article(factual_article).strip()
    if not detected:
        raise ValueError("Исходный артикул не может быть пустым.")
    if not factual:
        raise ValueError("Фактический артикул не может быть пустым.")

    corrections = _load_to_cache()
    key = _store_key(store_name, supply_type)
    store_corrections = corrections.setdefault(key, {})
    if not isinstance(store_corrections, dict):
        store_corrections = corrections[key] = {}

    same_keys = [
        saved for saved in store_corrections
        if normalize_article(saved) == normalize_article(detected)
    ]
    for saved in same_keys:
        del store_corrections[saved]

    if normalize_article(detected) != normalize_article(factual):
        store_corrections[detected] = factual
    if not store_corrections:
        corrections.pop(key, None)

    atomic_write_json(CORRECTIONS_FILE, corrections)
    return factual
