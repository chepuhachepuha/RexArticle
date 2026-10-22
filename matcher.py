import os
import yaml
import re
from manual_overrides import get_override
from indexer import get_index_for_store
from core.paths import resource_path
from core.yandex_disk import is_yandex_uri
from core.text_normalization import normalize_article
from core.store_manager import KRISTABELL_SIZE_RANGES

CONFIG_PATH = resource_path("config_matcher.yaml")
_config_cache = None


def load_matcher_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if not os.path.exists(CONFIG_PATH):
        default_config = {
            "matcher_map": {},
            "default_matcher": "Simple"
        }
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(default_config, f, ensure_ascii=False, default_flow_style=False)
        _config_cache = default_config
        return _config_cache

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        _config_cache = yaml.safe_load(f)
    return _config_cache


class BaseMatcher:
    """Базовый класс для всех matcher'ов."""

    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None, size_ranges=None):
        raise NotImplementedError("Subclasses must implement find_file_by_article_and_size")

    def find_file_by_article(self, article, store_name):
        index = get_index_for_store(store_name)
        article = normalize_article(article)
        return self._lookup(index, article)

    @staticmethod
    def _lookup(index, key):
        if key in index:
            return index[key]
        for stored_key, paths in index.items():
            if normalize_article(stored_key) == key:
                return paths
        return []

    def _check_manual_override(self, article, size_from_pdf, store_name):
        if "_" in store_name:
            parts = store_name.rsplit("_", 1)
            if len(parts) == 2:
                actual_store, supply_type = parts
                override_path = get_override(actual_store, supply_type, article, size_from_pdf)
                if override_path and (
                    is_yandex_uri(override_path) or os.path.exists(override_path)
                ):
                    return override_path
        return None

    def article_candidates(self, article):
        return [normalize_article(article)]

    def filter_matches(self, matches):
        return matches

    @staticmethod
    def _filter_extensions(matches, allowed_extensions):
        if allowed_extensions is None:
            return matches
        normalised_extensions = {
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in allowed_extensions
        }
        return [
            path for path in matches
            if os.path.splitext(path)[1].lower() in normalised_extensions
        ]

    def _find_range_matches(
        self, article, size_from_pdf, store_name, size_ranges, allowed_extensions
    ):
        index = get_index_for_store(store_name)
        wanted_size = normalize_article(size_from_pdf)
        candidates = self.article_candidates(article)
        for item in size_ranges or []:
            if not isinstance(item, dict):
                continue
            sizes = {normalize_article(value) for value in item.get("sizes", [])}
            pattern = item.get("pattern", "")
            if not pattern or wanted_size not in sizes:
                continue
            for candidate in candidates:
                key = normalize_article(f"{candidate}{pattern}")
                matches = self._filter_extensions(
                    self.filter_matches(self._lookup(index, key)),
                    allowed_extensions,
                )
                if matches:
                    return matches
        return []

    def find_with_override(
        self, article, size_from_pdf, store_name, search_func,
        allowed_extensions=None, size_ranges=None,
    ):
        raw_article = article
        matches = self._find_range_matches(
            raw_article, size_from_pdf, store_name, size_ranges,
            allowed_extensions,
        )
        if not matches:
            article = normalize_article(raw_article)
            matches = search_func(article, size_from_pdf, store_name)
        matches = self._filter_extensions(matches, allowed_extensions)
        if matches:
            return matches

        override_path = self._check_manual_override(raw_article, size_from_pdf, store_name)
        if override_path:
            return [override_path]
        return []


class SimpleMatcher(BaseMatcher):
    """
    Matcher по умолчанию. Ищет по точному совпадению артикула.
    Исключает .jpg файлы — ищет во всех остальных форматах.
    """

    # Расширения, которые исключаем из поиска
    EXCLUDED_EXTENSIONS = {'.jpg'}

    def filter_matches(self, matches):
        return [path for path in matches if not path.lower().endswith(tuple(self.EXCLUDED_EXTENSIONS))]

    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None, size_ranges=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            if self._lookup(index, art):
                # Фильтруем файлы, исключая .jpg
                return self.filter_matches(self._lookup(index, art))
            return []

        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions, size_ranges=size_ranges,
        )


class RozenbergMatcher(BaseMatcher):
    def article_candidates(self, article):
        return [normalize_article(article).split('-', 1)[0]]

    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None, size_ranges=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            clean_article = art.split('-', 1)[0]
            return self._lookup(index, clean_article)

        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions, size_ranges=size_ranges,
        )


class AlekseevMatcher(BaseMatcher):
    def article_candidates(self, article):
        normalised = normalize_article(article)
        stripped = re.sub(r'(black|white)$', '', normalised)
        return list(dict.fromkeys((normalised, stripped)))

    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None, size_ranges=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            matches = self._lookup(index, art)
            if matches:
                return matches

            clean_article = re.sub(r'(black|white)$', '', art)
            return self._lookup(index, clean_article)

        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions, size_ranges=size_ranges,
        )


class PdfOnlyMatcher(BaseMatcher):
    def filter_matches(self, matches):
        return [path for path in matches if path.lower().endswith('.pdf')]

    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None, size_ranges=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            return self.filter_matches(self._lookup(index, art))

        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions, size_ranges=size_ranges,
        )


class KristabellMatcher(BaseMatcher):
    def article_candidates(self, article):
        return [normalize_article(str(article or "").replace("/", " "))]

    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None, size_ranges=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            clean_article = self.article_candidates(art)[0]
            return self._lookup(index, clean_article)

        effective_ranges = KRISTABELL_SIZE_RANGES if size_ranges is None else size_ranges
        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions, size_ranges=effective_ranges,
        )


MATCHER_CLASSES = {
    "Simple": SimpleMatcher,
    "Rozenberg": RozenbergMatcher,
    "Alekseev": AlekseevMatcher,
    "PdfOnly": PdfOnlyMatcher,
    "Kristabell": KristabellMatcher,
}

MATCHER_LABELS = {
    "Simple": "Simple — точное совпадение",
    "Rozenberg": "Rozenberg — артикул до дефиса",
    "Alekseev": "Alekseev — суффиксы Black/White",
    "PdfOnly": "PdfOnly — только PDF",
    "Kristabell": "Kristabell — диапазоны размеров",
}


def get_matcher_options():
    """Return stable matcher names and user-facing descriptions."""
    return [
        (name, MATCHER_LABELS.get(name, name))
        for name in MATCHER_CLASSES
    ]


def resolve_matcher_name(store_name, preferred_name=None):
    """Resolve an explicit per-store matcher with legacy YAML fallback."""
    if preferred_name in MATCHER_CLASSES:
        return preferred_name

    config = load_matcher_config() or {}
    matcher_name = config.get("matcher_map", {}).get(
        store_name,
        config.get("default_matcher", "Simple"),
    )
    return matcher_name if matcher_name in MATCHER_CLASSES else "Simple"


def get_matcher_for_store(store_name, matcher_name=None):
    resolved_name = resolve_matcher_name(store_name, matcher_name)
    return MATCHER_CLASSES[resolved_name]()
