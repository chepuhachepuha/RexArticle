import os
import yaml
import re
from manual_overrides import get_override
from indexer import get_index_for_store
from core.paths import resource_path
from core.yandex_disk import is_yandex_uri

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
    
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name):
        raise NotImplementedError("Subclasses must implement find_file_by_article_and_size")
    
    def find_file_by_article(self, article, store_name):
        index = get_index_for_store(store_name)
        if article in index:
            return index[article]
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
    
    def find_with_override(self, article, size_from_pdf, store_name, search_func):
        matches = search_func(article, size_from_pdf, store_name)
        if matches:
            return matches
        
        override_path = self._check_manual_override(article, size_from_pdf, store_name)
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
    
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            if art in index:
                # Фильтруем файлы, исключая .jpg
                filtered_files = [
                    file_path for file_path in index[art]
                    if not file_path.lower().endswith(tuple(self.EXCLUDED_EXTENSIONS))
                ]
                return filtered_files
            return []
        
        return self.find_with_override(article, size_from_pdf, store_name, search_func)


class RozenbergMatcher(BaseMatcher):
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            clean_article = art.split('-', 1)[0]
            if clean_article in index:
                return index[clean_article]
            return []
        
        return self.find_with_override(article, size_from_pdf, store_name, search_func)


class AlekseevMatcher(BaseMatcher):
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            if art in index:
                return index[art]
            
            clean_article = re.sub(r'(Black|White)$', '', art)
            if clean_article in index:
                return index[clean_article]
            return []
        
        return self.find_with_override(article, size_from_pdf, store_name, search_func)


class PdfOnlyMatcher(BaseMatcher):
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            if art in index:
                pdf_files = [
                    file_path for file_path in index[art]
                    if file_path.lower().endswith('.pdf')
                ]
                return pdf_files
            return []
        
        return self.find_with_override(article, size_from_pdf, store_name, search_func)


class KristabellMatcher(BaseMatcher):
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            
            clean_article = art.replace("/", " ")
            
            size_upper = size.upper().strip() if size else ""
            if size_upper in ["XS", "S", "M", "L", "XL"]:
                range_suffix = "(XS_XL)"
            elif size_upper in ["2XL", "3XL", "4XL", "5XL", "6XL"]:
                range_suffix = "(2XL_6XL)"
            else:
                if clean_article in index:
                    return index[clean_article]
                return []
            
            search_key = f"{clean_article} {range_suffix}"
            
            if search_key in index:
                return index[search_key]
            return []
        
        return self.find_with_override(article, size_from_pdf, store_name, search_func)


MATCHER_CLASSES = {
    "Simple": SimpleMatcher,
    "Rozenberg": RozenbergMatcher,
    "Alekseev": AlekseevMatcher,
    "PdfOnly": PdfOnlyMatcher,
    "Kristabell": KristabellMatcher,
}


def get_matcher_for_store(store_name):
    config = load_matcher_config()
    matcher_name = config.get("matcher_map", {}).get(store_name, config.get("default_matcher", "Simple"))
    matcher_class = MATCHER_CLASSES.get(matcher_name)
    
    if matcher_class:
        return matcher_class()
    else:
        return SimpleMatcher()
