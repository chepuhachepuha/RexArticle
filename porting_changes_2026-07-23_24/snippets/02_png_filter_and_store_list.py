"""PNG-?????? ? ?????????? ?????? ?????????

?????????? ?????????, ?? ??????????????? ?????? ??????????.
???????? ??????: matcher.py, core/store_manager.py, gui/settings_window.py.
?????? ????? ??????? ????????? ? ???????? unified diff-??????.
"""

class BaseMatcherChanges:
    def find_with_override(
        self, article, size_from_pdf, store_name, search_func,
        allowed_extensions=None,
    ):
        matches = search_func(article, size_from_pdf, store_name)
        if allowed_extensions is not None:
            normalised_extensions = {
                extension.lower() if extension.startswith(".") else f".{extension.lower()}"
                for extension in allowed_extensions
            }
            matches = [
                path for path in matches
                if os.path.splitext(path)[1].lower() in normalised_extensions
            ]
        if matches:
            return matches
        
        override_path = self._check_manual_override(article, size_from_pdf, store_name)
        if override_path:
            return [override_path]
        return []

class SimpleMatcherChanges:
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None):
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
        
        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions,
        )

class RozenbergMatcherChanges:
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            clean_article = art.split('-', 1)[0]
            if clean_article in index:
                return index[clean_article]
            return []
        
        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions,
        )

class AlekseevMatcherChanges:
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            if art in index:
                return index[art]
            
            clean_article = re.sub(r'(Black|White)$', '', art)
            if clean_article in index:
                return index[clean_article]
            return []
        
        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions,
        )

class PdfOnlyMatcherChanges:
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None):
        def search_func(art, size, store):
            index = get_index_for_store(store)
            if art in index:
                pdf_files = [
                    file_path for file_path in index[art]
                    if file_path.lower().endswith('.pdf')
                ]
                return pdf_files
            return []
        
        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions,
        )

class KristabellMatcherChanges:
    def find_file_by_article_and_size(self, article, size_from_pdf, store_name, allowed_extensions=None):
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
        
        return self.find_with_override(
            article, size_from_pdf, store_name, search_func,
            allowed_extensions=allowed_extensions,
        )

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

class SettingsWindowChanges:
    def refresh_store_buttons(self):
        scroll_value = self.stores_scroll_area.verticalScrollBar().value()

        while self.stores_layout.count():
            item = self.stores_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        self.store_buttons = {}
        for store_name in self.stores.keys():
            button = QPushButton(self._display_store_name(store_name))
            button.setFixedHeight(30)
            button.setProperty("sidebarItem", True)
            button.setProperty("selected", store_name == self.selected_store)
            button.clicked.connect(
                lambda checked=False, name=store_name: self.select_store(name)
            )
            self.store_buttons[store_name] = button
            self.stores_layout.addWidget(button)

        self.stores_layout.addStretch(1)
        QTimer.singleShot(
            0,
            lambda value=scroll_value: self._restore_scroll_value(
                self.stores_scroll_area, value
            ),
        )

    def select_store(self, store_name):
        left_scroll_value = self.stores_scroll_area.verticalScrollBar().value()
        right_scroll_value = self.settings_scroll.verticalScrollBar().value()
        self.selected_store = store_name
        self.store_title.setText(
            f"Настройки: {self._display_store_name(store_name)}"
        )
        self.update_store_index_button.setEnabled(True)

        self.refresh_types_for_store(store_name)
        self.update_overrides_stats()
        self._clear_inline_styles()
        self._update_store_button_selection()
        QTimer.singleShot(
            0,
            lambda value=left_scroll_value: self._restore_scroll_value(
                self.stores_scroll_area, value
            ),
        )
        QTimer.singleShot(
            0,
            lambda value=right_scroll_value: self._restore_scroll_value(
                self.settings_scroll, value
            ),
        )

    def _on_png_only_toggled(self, supply_type, checked):
        store_name = self.selected_store
        if store_name in self.type_vars and supply_type in self.type_vars[store_name]:
            self.type_vars[store_name][supply_type]["png_only"] = bool(checked)
            self._auto_save()

