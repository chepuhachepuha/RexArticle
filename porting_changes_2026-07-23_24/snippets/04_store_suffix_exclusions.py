"""?????????? ????????? ?? ???????? ?????

?????????? ?????????, ?? ??????????????? ?????? ??????????.
???????? ??????: core/personalization.py, gui/settings_window.py, gui/main_window.py.
?????? ????? ??????? ????????? ? ???????? unified diff-??????.
"""

"""Shared helpers for personalization settings."""


def normalise_store_suffix_exclusions(value):
    """Return valid internal store names as a set."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {
        name
        for name in value
        if isinstance(name, str) and name
    }


def should_append_store_suffix(
    store_name,
    append_enabled,
    excluded_store_names=None,
):
    """Return whether a delivery folder should receive the store suffix."""
    if not append_enabled or not store_name:
        return False
    exclusions = normalise_store_suffix_exclusions(excluded_store_names)
    return store_name not in exclusions
class StoreSuffixExclusionsDialog(QDialog):
    """Select stores that must not be appended to delivery folder names."""

    def __init__(
        self,
        stores,
        display_name,
        selected_store_names=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Не подписывать магазины")
        self.resize(520, 460)
        self._checkboxes = {}
        selected = normalise_store_suffix_exclusions(selected_store_names)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Отметьте магазины, название которых не нужно добавлять "
            "в квадратных скобках к папке поставки."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget(scroll)
        stores_layout = QVBoxLayout(container)

        for store_name in stores:
            visible_name = display_name(store_name)
            label = (
                visible_name
                if visible_name == store_name
                else f"{visible_name} ({store_name})"
            )
            checkbox = QCheckBox(label, container)
            checkbox.setChecked(store_name in selected)
            self._checkboxes[store_name] = checkbox
            stores_layout.addWidget(checkbox)

        if not self._checkboxes:
            empty_label = QLabel("Настроенных магазинов пока нет", container)
            empty_label.setObjectName("settingsSubtitle")
            stores_layout.addWidget(empty_label)

        stores_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_store_names(self):
        return {
            store_name
            for store_name, checkbox in self._checkboxes.items()
            if checkbox.isChecked()
        }

class SettingsWindowChanges:
    def _create_personalization_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel("Персонализация")
        title.setObjectName("settingsTitle")
        layout.addWidget(title)

        hint = QLabel("Общее поведение программы для повседневной работы")
        hint.setObjectName("settingsSubtitle")
        layout.addWidget(hint)

        behavior_group = QGroupBox("Поведение поставок")
        behavior_group.setProperty("settingsCard", True)
        behavior_layout = QVBoxLayout(behavior_group)
        behavior_layout.setSpacing(12)

        self.auto_expand_delivery_checkbox = QCheckBox(
            "Автоматически раскрывать поставку после загрузки"
        )
        self.auto_expand_delivery_checkbox.setChecked(bool(
            self.app_config.get("personalization_auto_expand_delivery", True)
        ))
        behavior_layout.addWidget(self.auto_expand_delivery_checkbox)

        self.open_results_folder_checkbox = QCheckBox(
            "Автоматически открывать папку результатов после завершения"
        )
        self.open_results_folder_checkbox.setChecked(bool(
            self.app_config.get("personalization_open_results_folder", False)
        ))
        behavior_layout.addWidget(self.open_results_folder_checkbox)

        self.append_store_name_checkbox = QCheckBox(
            "Подписывать название магазина в [] в конце названия папки поставки"
        )
        self.append_store_name_checkbox.setChecked(bool(
            self.app_config.get(
                "personalization_append_store_to_delivery_folder", False
            )
        ))
        store_suffix_layout = QHBoxLayout()
        store_suffix_layout.addWidget(self.append_store_name_checkbox, 1)
        self.store_suffix_exclusions_button = QPushButton()
        self.store_suffix_exclusions_button.clicked.connect(
            self._edit_store_suffix_exclusions
        )
        store_suffix_layout.addWidget(self.store_suffix_exclusions_button)
        behavior_layout.addLayout(store_suffix_layout)
        self._update_store_suffix_exclusions_button()

        self.store_names_button = QPushButton("Названия магазинов")
        self.store_names_button.clicked.connect(self._edit_store_display_names)
        behavior_layout.addWidget(self.store_names_button)

        self.logs_button = QPushButton("Логи")
        self.logs_button.clicked.connect(self._open_logs)
        behavior_layout.addWidget(self.logs_button)

        layout.addWidget(behavior_group)
        layout.addStretch()

        for checkbox in (
            self.auto_expand_delivery_checkbox,
            self.open_results_folder_checkbox,
            self.append_store_name_checkbox,
        ):
            checkbox.toggled.connect(self._save_personalization_settings)
        self.append_store_name_checkbox.toggled.connect(
            self._update_store_suffix_exclusions_button
        )
        return tab

    def _update_store_suffix_exclusions_button(self, *_):
        valid_exclusions = self.store_suffix_exclusions.intersection(self.stores)
        count = len(valid_exclusions)
        text = "Кроме…" if count == 0 else f"Кроме… ({count})"
        self.store_suffix_exclusions_button.setText(text)
        self.store_suffix_exclusions_button.setEnabled(
            self.append_store_name_checkbox.isChecked()
        )

    def _edit_store_suffix_exclusions(self):
        dialog = StoreSuffixExclusionsDialog(
            self.stores,
            self._display_store_name,
            self.store_suffix_exclusions,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dialog.selected_store_names()
        config = self._load_app_config()
        config["personalization_store_suffix_exclusions"] = [
            store_name
            for store_name in self.stores
            if store_name in selected
        ]
        try:
            atomic_write_json(self.config_file, config)
        except OSError as error:
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось сохранить исключения магазинов: {error}",
            )
            return

        self.app_config = config
        self.store_suffix_exclusions = set(
            config["personalization_store_suffix_exclusions"]
        )
        self._update_store_suffix_exclusions_button()

    def _save_personalization_settings(self, *_):
        self.app_config = self._load_app_config()
        self.app_config.update({
            "personalization_auto_expand_delivery": (
                self.auto_expand_delivery_checkbox.isChecked()
            ),
            "personalization_open_results_folder": (
                self.open_results_folder_checkbox.isChecked()
            ),
            "personalization_append_store_to_delivery_folder": (
                self.append_store_name_checkbox.isChecked()
            ),
            "personalization_store_suffix_exclusions": [
                store_name
                for store_name in self.stores
                if store_name in self.store_suffix_exclusions
            ],
        })
        try:
            atomic_write_json(self.config_file, self.app_config)
        except OSError as error:
            print(f"[SETTINGS] Не удалось сохранить персонализацию: {error}")

class SearchWorkerChanges:
    def __init__(
        self,
        delivery_items,
        output_folder,
        matcher_cache,
        append_store_name=False,
        cancel_token=None,
        store_suffix_exclusions=None,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self.delivery_items = delivery_items
        self.output_folder = output_folder
        self.matcher_cache = matcher_cache
        self.append_store_name = append_store_name
        self.cancel_token = cancel_token
        self.store_suffix_exclusions = normalise_store_suffix_exclusions(
            store_suffix_exclusions
        )
        self.signals = SearchSignals()

    def _process_delivery(self, delivery_item):
        store_name = delivery_item.store_name
        supply_type = delivery_item.supply_type
        index_store_name = f"{store_name}_{supply_type}"
        
        if index_store_name not in self.matcher_cache:
            self.matcher_cache[index_store_name] = get_matcher_for_store(index_store_name)
        matcher = self.matcher_cache[index_store_name]
        type_data = get_type_data(store_name, supply_type)
        allowed_extensions = {".png"} if type_data.get("png_only", False) else None
        
        base_output_folder = os.path.join(self.output_folder, "Найденные_принты_все")
        os.makedirs(base_output_folder, exist_ok=True)
        
        folder_name = _delivery_output_folder_name(
            delivery_item.pdf_path,
            getattr(delivery_item, "display_store_name", store_name),
            should_append_store_suffix(
                store_name,
                self.append_store_name,
                self.store_suffix_exclusions,
            ),
        )
        output_folder = os.path.join(base_output_folder, folder_name)
        os.makedirs(output_folder, exist_ok=True)
        
        not_found_items = []
        
        for i in range(delivery_item.child_count()):
            if self.cancel_token:
                self.cancel_token.raise_if_cancelled()
            article_item = delivery_item.child(i)
            article = article_item.article
            size = article_item.size
            location = article_item.location
            
            matches = matcher.find_file_by_article_and_size(
                article, size, index_store_name,
                allowed_extensions=allowed_extensions,
            )
            
            if matches:
                file = matches[0]
                self.signals.article_status.emit(delivery_item, i, "Найден")

                try:
                    _, ext = os.path.splitext(os.path.basename(file))
                    manual_override_path = get_override(
                        store_name, supply_type, article, size
                    )
                    base_name = _result_file_base_name(
                        file, article, manual_override_path
                    )
                    if location == "СПИНА":
                        suffix = f"[{size} СПИНА]" if size else "[СПИНА]"
                    else:
                        suffix = f"[{size}]" if size else ""
                    
                    if suffix:
                        numbered_name = f"{i+1}. {base_name} {suffix}{ext}"
                    else:
                        numbered_name = f"{i+1}. {base_name}{ext}"
                    
                    target_path = os.path.abspath(
                        os.path.join(output_folder, numbered_name)
                    )
                    output_root = os.path.abspath(output_folder)
                    if os.path.commonpath([output_root, target_path]) != output_root:
                        raise ValueError("Недопустимое имя итогового файла")
                    copy_resource(file, target_path, cancel_token=self.cancel_token)
                    self.signals.article_copied.emit(delivery_item, i, target_path)
                except OperationCancelled:
                    raise
                except Exception as e:
                    print(f"[ПОИСК] Ошибка копирования {file}: {e}")
            else:
                not_found_items.append((i + 1, article, size, location))
                self.signals.article_status.emit(delivery_item, i, "Не найден")
        
        _sync_not_found_file(
            output_folder, not_found_items, cancel_token=self.cancel_token
        )
        self.signals.delivery_finished.emit(delivery_item)

class MainWindowChanges:
    def load_config(self):
        config = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (OSError, json.JSONDecodeError):
                config = {}
        configured_output_folder = config.get("output_folder", "")
        self.output_folder = _normalise_output_folder(configured_output_folder)
        token_removed = config.pop("yandex_oauth_token", None) is not None
        if token_removed and sys.platform.startswith("win"):
            atomic_write_json(self.config_file, config)
        self.cloud_background_index_enabled = (
            False if sys.platform.startswith("win") else bool(
                config.get("cloud_background_index_enabled", True)
            )
        )
        self.cloud_background_index_time = str(
            config.get("cloud_background_index_time", "03:00")
        )
        self.auto_expand_delivery = bool(
            config.get("personalization_auto_expand_delivery", True)
        )
        self.open_results_folder = bool(
            config.get("personalization_open_results_folder", False)
        )
        self.append_store_to_delivery_folder = bool(config.get(
            "personalization_append_store_to_delivery_folder", False
        ))
        self.store_suffix_exclusions = normalise_store_suffix_exclusions(
            config.get("personalization_store_suffix_exclusions", [])
        )
        self.store_display_names = dict(
            config.get("store_display_names", {}) or {}
        )
        if (
            not os.path.exists(self.config_file)
            or configured_output_folder != self.output_folder
        ):
            self.save_config()

    def save_config(self):
        config = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (OSError, json.JSONDecodeError):
                config = {}
        config.pop("yandex_oauth_token", None)
        config.update({
            "search_folder": "",
            "output_folder": self.output_folder,
            "cloud_background_index_enabled": self.cloud_background_index_enabled,
            "cloud_background_index_time": self.cloud_background_index_time,
            "personalization_auto_expand_delivery": self.auto_expand_delivery,
            "personalization_open_results_folder": self.open_results_folder,
            "personalization_append_store_to_delivery_folder": (
                self.append_store_to_delivery_folder
            ),
            "personalization_store_suffix_exclusions": sorted(
                self.store_suffix_exclusions,
                key=str.casefold,
            ),
            "store_display_names": self.store_display_names,
        })
        atomic_write_json(self.config_file, config)

    def _on_delivery_finished(self, delivery_item):
        base_output_folder = os.path.join(self.output_folder, "Найденные_принты_все")
        folder_name = _delivery_output_folder_name(
            delivery_item.pdf_path,
            getattr(
                delivery_item,
                "display_store_name",
                delivery_item.store_name,
            ),
            should_append_store_suffix(
                delivery_item.store_name,
                self.append_store_to_delivery_folder,
                self.store_suffix_exclusions,
            ),
        )
        output_folder = os.path.join(base_output_folder, folder_name)
        
        delivery_item.output_folder = output_folder
        self.tree_model.update_delivery_status(delivery_item, "Готово")

