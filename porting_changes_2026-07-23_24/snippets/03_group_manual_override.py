"""????????? ?????? ????????? ?????????? ?????????

?????????? ?????????, ?? ??????????????? ?????? ??????????.
???????? ??????: manual_overrides.py, gui/main_window.py.
?????? ????? ??????? ????????? ? ???????? unified diff-??????.
"""

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

def _override_dialog_directory(store_path, delivery_folder):
    """Return the first existing physical folder suitable for file selection."""
    candidates = [
        store_path,
        delivery_folder,
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        ),
        os.path.expanduser("~"),
    ]
    for candidate in candidates:
        value = os.path.normpath(
            os.path.expandvars(os.path.expanduser(str(candidate or "").strip()))
        )
        if value and os.path.isdir(value):
            return value
    return os.path.normpath(os.path.expanduser("~"))

def _matching_article_indices(delivery_item, article):
    """Return every row in this delivery with the same trimmed article."""
    target = str(article or "").strip()
    return [
        index
        for index in range(delivery_item.child_count())
        if str(delivery_item.child(index).article or "").strip() == target
    ]

class MainWindowChanges:
    def _remember_override_for_article(
        self, delivery_item, article, file_path, status
    ):
        matching_indices = _matching_article_indices(delivery_item, article)
        article_sizes = [
            (delivery_item.child(index).article, delivery_item.child(index).size)
            for index in matching_indices
        ]
        set_overrides(
            delivery_item.store_name,
            delivery_item.supply_type,
            article_sizes,
            file_path,
        )
        for index in matching_indices:
            self.tree_model.update_article_status(delivery_item, index, status)
        return len(matching_indices)

    def on_override_clicked(self, delivery_item, article_index):
        """
        Обработка клика по кнопке 'Уточнить' для артикула.
        
        Логика:
        - Если copy_on_override = False → запоминаем путь в manual_overrides.json
        - Если copy_on_override = True:
            - Файл НЕ в override_path (и подпапках) → копируем в override_path/overrided/{article}{ext}
            - Файл УЖЕ в override_path (или подпапках) → переименовываем на месте в {article}{ext}
        """
        if article_index >= delivery_item.child_count():
            return
        
        article_item = delivery_item.child(article_index)
        article = article_item.article
        size = article_item.size
        store_name = delivery_item.store_name
        supply_type = delivery_item.supply_type

        # Артикул сразу доступен для вставки в поиск Finder/Проводника.
        QApplication.clipboard().setText(article)
        type_data = get_type_data(store_name, supply_type)
        if type_data.get("source") == "yandex":
            if sys.platform.startswith("win"):
                QMessageBox.information(
                    self, "Яндекс Диск недоступен",
                    "Яндекс Диск временно недоступен в Windows-версии."
                )
                return
            picker = YandexDiskPickerDialog(
                select_file=True,
                initial_path=type_data.get("path", ""),
                parent=self,
            )
            if picker.exec() != QDialog.Accepted:
                return
            linked_count = self._remember_override_for_article(
                delivery_item, article, picker.selected_path,
                "Уточнён (Яндекс Диск)",
            )
            QMessageBox.information(
                self, "Успех",
                f"Облачный файл привязан к {linked_count} поз.",
            )
            return

        initial_directory = _override_dialog_directory(
            type_data.get("path", ""), delivery_item.folder
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Укажите путь артикула {article}", initial_directory,
            "Изображения (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;Все файлы (*.*)"
        )
        if not file_path:
            return

        copy_on_override = type_data.get("copy_on_override", False)
        override_path = type_data.get("override_path", "")
        index_store_name = f"{store_name}_{supply_type}"
        
        if not copy_on_override:
            # === Режим "Только запомнить путь" ===
            linked_count = self._remember_override_for_article(
                delivery_item, article, file_path, "Уточнён (override)"
            )
            QMessageBox.information(
                self, "Успех",
                f"Путь сохранён для {linked_count} поз.:\n{file_path}",
            )
            return
        
        # === Режим "Копировать и переименовывать" ===
        
        # Определяем корневую папку для операций
        target_root = override_path if override_path else delivery_item.folder
        
        if not target_root:
            QMessageBox.warning(
                self, "Ошибка",
                "Не указан путь для копирования. Укажите его в настройках магазина."
            )
            return
        
        # Нормализуем пути для точного сравнения
        file_norm = os.path.normpath(os.path.realpath(file_path))
        root_norm = os.path.normpath(os.path.realpath(target_root))
        
        # Проверяем: находится ли файл внутри target_root (или его подпапок)
        # Используем разделитель для точного совпадения префикса
        is_in_root = (
            file_norm == root_norm or
            file_norm.startswith(root_norm + os.sep)
        )
        
        _, ext = os.path.splitext(file_path)
        new_filename = f"{article}{ext}"
        
        try:
            if is_in_root:
                # === Файл уже в папке магазина → переименовываем на месте ===
                current_dir = os.path.dirname(file_path)
                new_path = os.path.join(current_dir, new_filename)
                new_path_norm = os.path.normpath(new_path)
                
                # Если файл уже назван правильно — ничего не делаем
                if file_norm == new_path_norm:
                    QMessageBox.information(
                        self, "Готово",
                        f"Файл уже имеет правильное имя:\n{file_path}"
                    )
                    self.tree_model.update_article_status(
                        delivery_item, article_index, "Уточнён (уже на месте)"
                    )
                else:
                    # Проверка: нет ли конфликта имен
                    if os.path.exists(new_path):
                        reply = QMessageBox.question(
                            self, "Конфликт имен",
                            f"Файл уже существует:\n{new_path}\n\n"
                            f"Заменить его выбранным файлом?",
                            QMessageBox.Yes | QMessageBox.No
                        )
                        if reply != QMessageBox.Yes:
                            return
                        
                        # Удаляем конфликтующий файл
                        try:
                            os.remove(new_path)
                        except Exception as e:
                            QMessageBox.critical(
                                self, "Ошибка",
                                f"Не удалось удалить конфликтующий файл:\n{e}"
                            )
                            return
                    
                    # Переименовываем
                    os.rename(file_path, new_path)
                    print(f"[OVERRIDE] Переименован: {file_path} → {new_path}")
                    QMessageBox.information(
                        self, "Успех",
                        f"Файл переименован на месте:\n{new_path}"
                    )
                    self.tree_model.update_article_status(
                        delivery_item, article_index, "Уточнён (переименован)"
                    )
            else:
                # === Файл извне → копируем в overrided ===
                overrided_dir = os.path.join(target_root, "overrided")
                os.makedirs(overrided_dir, exist_ok=True)
                
                new_path = os.path.join(overrided_dir, new_filename)
                
                # Проверка: нет ли конфликта имен
                if os.path.exists(new_path):
                    reply = QMessageBox.question(
                        self, "Конфликт имен",
                        f"Файл уже существует:\n{new_path}\n\n"
                        f"Заменить его?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        return
                    
                    try:
                        os.remove(new_path)
                    except Exception as e:
                        QMessageBox.critical(
                            self, "Ошибка",
                            f"Не удалось удалить конфликтующий файл:\n{e}"
                        )
                        return
                
                # Копируем
                shutil.copy2(file_path, new_path)
                print(f"[OVERRIDE] Скопирован: {file_path} → {new_path}")
                QMessageBox.information(
                    self, "Успех",
                    f"Файл скопирован в:\n{new_path}"
                )
                self.tree_model.update_article_status(
                    delivery_item, article_index, "Уточнён (скопирован)"
                )
            
            # После операций обновляем только изменившиеся файлы индекса.
            update_index_for_store(
                index_store_name,
                delivery_item.folder,
                override_path=override_path,
                force=True
            )
        
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось обработать файл:\n{e}"
            )

