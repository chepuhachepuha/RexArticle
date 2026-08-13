"""????????? ????? ? ????????????? ???????????.txt

?????????? ?????????, ?? ??????????????? ?????? ??????????.
???????? ??????: gui/main_window.py.
?????? ????? ??????? ????????? ? ???????? unified diff-??????.
"""

def _sync_not_found_file(output_folder, not_found_items, cancel_token=None):
    """Atomically make ненайденные.txt reflect one completed search."""
    not_found_path = os.path.join(output_folder, "ненайденные.txt")
    temporary_path = not_found_path + ".part"

    if cancel_token:
        cancel_token.raise_if_cancelled()

    if not not_found_items:
        try:
            os.remove(not_found_path)
        except FileNotFoundError:
            pass
        return

    try:
        with open(temporary_path, "w", encoding="utf-8", newline="") as file:
            for position, article, size, location in not_found_items:
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                if size and location:
                    file.write(f"{position}.{article}[{size} {location}]\n")
                elif size:
                    file.write(f"{position}.{article}[{size}]\n")
                else:
                    file.write(f"{position}.{article}\n")
            file.flush()
            os.fsync(file.fileno())
        if cancel_token:
            cancel_token.raise_if_cancelled()
        os.replace(temporary_path, not_found_path)
    except Exception:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise

class SearchWorkerChanges:
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
            self.append_store_name,
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
    def on_restart_clicked(self, delivery_item):
        if self.is_searching:
            QMessageBox.information(self, "Информация", "Дождитесь завершения текущего поиска")
            return
        
        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Перезапустить поиск для поставки:\n{os.path.basename(delivery_item.pdf_path)}?\n\n"
            f"Все скопированные файлы будут удалены.",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        if delivery_item.output_folder and os.path.exists(delivery_item.output_folder):
            try:
                shutil.rmtree(delivery_item.output_folder)
            except Exception as e:
                QMessageBox.critical(
                    self, "Ошибка повторного поиска",
                    "Не удалось очистить папку предыдущего результата.\n\n"
                    f"{delivery_item.output_folder}\n\n{e}",
                )
                return
        
        for i in range(delivery_item.child_count()):
            self.tree_model.update_article_status(delivery_item, i, "Ожидание")
            delivery_item.child(i).copied_file = None
        self.tree_model.update_delivery_status(delivery_item, "Ожидание")
        
        self.is_searching = True
        self._set_actions_enabled(False)
        
        store_name = delivery_item.store_name
        supply_type = delivery_item.supply_type
        key = f"{store_name}_{supply_type}"
        
        type_data = get_type_data(store_name, supply_type)
        override_path = type_data.get("override_path") if type_data.get("copy_on_override") else None
        
        unique_stores = {
            key: {
                "store_name": store_name,
                "supply_type": supply_type,
                "folder": delivery_item.folder,
                "override_path": override_path,
                "force": type_data.get("source", "local") != "yandex",
                "source": type_data.get("source", "local"),
            }
        }
        
        self._pending_restart_item = delivery_item

        if type_data.get("source", "local") == "yandex":
            if sys.platform.startswith("win"):
                self._pending_restart_item = None
                self.is_searching = False
                self._set_actions_enabled(True)
                QMessageBox.information(
                    self, "Яндекс Диск недоступен",
                    "Для этого магазина выберите локальную или сетевую папку."
                )
                return
            cloud_path = type_data.get("path", "").strip()
            if not has_yandex_index_for_store(key, cloud_path):
                self._pending_restart_item = None
                self.is_searching = False
                self._set_actions_enabled(True)
                QMessageBox.warning(
                    self, "Требуется индекс",
                    "Для этого облачного магазина ещё нет готового индекса.\n\n"
                    "Откройте настройки и выполните ручную полную сверку."
                )
                return
            print(
                f"[INDEX] {key}: повторный облачный поиск без индексации, "
                "используется SQLite"
            )
            self._on_restart_index_finished()
            return
        
        worker = IndexRebuildWorker(unique_stores, self.shutdown_token)
        worker.signals.finished.connect(self._on_restart_index_finished)
        worker.signals.error.connect(self._on_index_error)
        self.thread_pool.start(worker)
