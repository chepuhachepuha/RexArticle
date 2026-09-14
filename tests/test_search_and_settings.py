import os
import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from core.cancellation import CancellationToken, OperationCancelled
from gui.main_window import (
    MainWindow,
    SearchWorker,
    _delivery_output_folder_name,
    _matching_article_indices,
    _override_dialog_directory,
    _sync_not_found_file,
)
from matcher import (
    AlekseevMatcher,
    SimpleMatcher,
    get_matcher_options,
    resolve_matcher_name,
)
from core.personalization import (
    normalise_store_suffix_exclusions,
    should_append_store_suffix,
)


class NotFoundFileTests(unittest.TestCase):
    def test_file_is_atomically_created_and_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            _sync_not_found_file(
                str(output),
                [(1, "A-1", "M", "СПИНА"), (2, "B-2", "", "")],
            )
            result = output / "ненайденные.txt"
            self.assertEqual(
                result.read_text(encoding="utf-8"),
                "1.A-1[M СПИНА]\n2.B-2\n",
            )
            self.assertFalse((output / "ненайденные.txt.part").exists())

            _sync_not_found_file(str(output), [(3, "C-3", "XL", "")])
            self.assertEqual(result.read_text(encoding="utf-8"), "3.C-3[XL]\n")
            self.assertFalse((output / "ненайденные.txt.part").exists())

    def test_empty_result_removes_stale_file_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "ненайденные.txt"
            result.write_text("stale", encoding="utf-8")
            _sync_not_found_file(directory, [])
            _sync_not_found_file(directory, [])
            self.assertFalse(result.exists())

    def test_cancellation_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = output / "ненайденные.txt"
            result.write_text("previous", encoding="utf-8")
            token = CancellationToken()
            token.cancel()
            with self.assertRaises(OperationCancelled):
                _sync_not_found_file(
                    directory, [(1, "NEW", "", "")], cancel_token=token
                )
            self.assertEqual(result.read_text(encoding="utf-8"), "previous")
            self.assertFalse((output / "ненайденные.txt.part").exists())


class OverrideInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_store_folder_and_fallback_order(self):
        import gui.main_window as main_module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            delivery = root / "delivery"
            documents = root / "documents"
            store.mkdir()
            delivery.mkdir()
            documents.mkdir()

            with patch.dict(os.environ, {"REX_STORE_TEST": str(store)}):
                self.assertEqual(
                    _override_dialog_directory("%REX_STORE_TEST%", str(delivery)),
                    os.path.normpath(str(store)),
                )
            self.assertEqual(
                _override_dialog_directory(str(root / "missing"), str(delivery)),
                os.path.normpath(str(delivery)),
            )
            with patch.object(
                main_module.QStandardPaths, "writableLocation", return_value=str(documents)
            ):
                self.assertEqual(
                    _override_dialog_directory("missing", "also-missing"),
                    os.path.normpath(str(documents)),
                )

    def test_home_is_final_fallback(self):
        import gui.main_window as main_module

        home = os.path.normpath(os.path.expanduser("~"))
        with patch.object(
            main_module.QStandardPaths, "writableLocation", return_value=""
        ), patch.object(
            main_module.os.path, "isdir", side_effect=lambda path: path == home
        ):
            self.assertEqual(
                _override_dialog_directory("missing", "also-missing"), home
            )

    def test_matching_article_ignores_size_location_and_edge_spaces(self):
        children = [
            SimpleNamespace(article=" ART-1 ", size="S", location=""),
            SimpleNamespace(article="ART-1", size="XL", location="СПИНА"),
            SimpleNamespace(article="ART-2", size="S", location=""),
        ]
        delivery = SimpleNamespace(
            child_count=lambda: len(children),
            child=lambda index: children[index],
        )
        self.assertEqual(_matching_article_indices(delivery, "ART-1"), [0, 1])

    def test_one_dialog_saves_all_sizes_and_updates_all_duplicate_rows(self):
        import gui.main_window as main_module

        children = [
            SimpleNamespace(article=" ART-1 ", size="S", location=""),
            SimpleNamespace(article="ART-1", size="XL", location="СПИНА"),
            SimpleNamespace(article="ART-1", size="S", location=""),
            SimpleNamespace(article="ART-2", size="S", location=""),
        ]
        delivery = SimpleNamespace(
            store_name="Shop",
            supply_type="WB",
            folder="",
            child_count=lambda: len(children),
            child=lambda index: children[index],
        )
        fake_window = SimpleNamespace(tree_model=SimpleNamespace(
            update_article_status=Mock()
        ))
        fake_window._remember_override_for_article = MethodType(
            MainWindow._remember_override_for_article, fake_window
        )

        with tempfile.TemporaryDirectory() as directory:
            selected = str(Path(directory) / "selected.tif")
            Path(selected).write_bytes(b"selected")
            with patch.object(
                main_module, "get_type_data",
                return_value={
                    "source": "local",
                    "path": directory,
                    "copy_on_override": False,
                },
            ), patch.object(
                main_module.QFileDialog, "getOpenFileName",
                return_value=(selected, ""),
            ) as dialog, patch.object(
                main_module, "set_overrides"
            ) as batch_save, patch.object(
                main_module.QMessageBox, "information"
            ):
                MainWindow.on_override_clicked(fake_window, delivery, 0)

            self.assertEqual(dialog.call_count, 1)
            self.assertEqual(dialog.call_args.args[2], os.path.normpath(directory))
            batch_save.assert_called_once_with(
                "Shop", "WB",
                [(" ART-1 ", "S"), ("ART-1", "XL"), ("ART-1", "S")],
                selected,
            )
            updated_indices = [
                call.args[1]
                for call in fake_window.tree_model.update_article_status.call_args_list
            ]
            self.assertEqual(updated_indices, [0, 1, 2])


class ManualOverrideBatchTests(unittest.TestCase):
    def test_batch_save_writes_all_sizes_once_without_schema_change(self):
        import json
        import manual_overrides as overrides_module

        with tempfile.TemporaryDirectory() as directory:
            overrides_file = str(Path(directory) / "manual_overrides.json")
            overrides_module.invalidate_cache()
            try:
                with patch.object(
                    overrides_module, "OVERRIDES_FILE", overrides_file
                ), patch.object(
                    overrides_module, "atomic_write_json",
                    wraps=overrides_module.atomic_write_json,
                ) as atomic_write:
                    overrides_module.set_overrides(
                        "Shop", "WB",
                        [("ART-1", "S"), ("ART-1", "XL"), ("ART-1", "S")],
                        r"C:\prints\ART-1.tif",
                    )
                    self.assertEqual(atomic_write.call_count, 1)

                data = json.loads(Path(overrides_file).read_text(encoding="utf-8"))
                self.assertEqual(
                    data["Shop_WB"],
                    {
                        "ART-1_S": r"C:\prints\ART-1.tif",
                        "ART-1_XL": r"C:\prints\ART-1.tif",
                    },
                )
            finally:
                overrides_module.invalidate_cache()

class MatcherExtensionFilterTests(unittest.TestCase):
    def test_png_filter_is_case_insensitive_and_filters_before_selection(self):
        index = {"A": [r"C:\prints\A.pdf", r"C:\prints\A.PNG", r"C:\prints\A.png"]}
        with patch("matcher.get_index_for_store", return_value=index), patch(
            "matcher.get_override", return_value=None
        ):
            result = SimpleMatcher().find_file_by_article_and_size(
                "A", "", "Shop_WB", allowed_extensions={".png"}
            )
        self.assertEqual(
            result, [r"C:\prints\A.PNG", r"C:\prints\A.png"]
        )

    def test_non_png_manual_override_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "manual.tif"
            override.write_bytes(b"manual")
            index = {"A": [r"C:\prints\A.pdf"]}
            with patch("matcher.get_index_for_store", return_value=index), patch(
                "matcher.get_override", return_value=str(override)
            ):
                result = SimpleMatcher().find_file_by_article_and_size(
                    "A", "", "Shop_WB", allowed_extensions={"png"}
                )
            self.assertEqual(result, [str(override)])

    def test_omitted_filter_preserves_existing_behavior(self):
        index = {"A": [r"C:\prints\A.pdf", r"C:\prints\A.png"]}
        with patch("matcher.get_index_for_store", return_value=index), patch(
            "matcher.get_override", return_value=None
        ):
            result = SimpleMatcher().find_file_by_article_and_size(
                "A", "", "Shop_WB"
            )
        self.assertEqual(result, index["A"])



class MatcherSelectionTests(unittest.TestCase):
    def test_available_matchers_match_implemented_classes(self):
        names = [name for name, _label in get_matcher_options()]
        self.assertEqual(
            names,
            ["Simple", "Rozenberg", "Alekseev", "PdfOnly", "Kristabell"],
        )

    def test_explicit_matcher_wins_and_legacy_yaml_remains_fallback(self):
        legacy = {
            "matcher_map": {"Legacy_WB": "Alekseev"},
            "default_matcher": "PdfOnly",
        }
        with patch("matcher.load_matcher_config", return_value=legacy):
            self.assertEqual(
                resolve_matcher_name("Legacy_WB", "Kristabell"),
                "Kristabell",
            )
            self.assertEqual(
                resolve_matcher_name("Legacy_WB", None),
                "Alekseev",
            )
            self.assertEqual(
                resolve_matcher_name("Unknown_WB", "Invalid"),
                "PdfOnly",
            )

    def test_explicit_matcher_class_is_used(self):
        from matcher import get_matcher_for_store

        self.assertIsInstance(
            get_matcher_for_store("Any_WB", "Alekseev"),
            AlekseevMatcher,
        )
    def test_search_worker_uses_matcher_saved_for_store_type(self):
        import gui.main_window as main_module

        with tempfile.TemporaryDirectory() as directory:
            delivery = SimpleNamespace(
                store_name="Shop",
                supply_type="WB",
                pdf_path=str(Path(directory) / "Delivery.pdf"),
                display_store_name="Shop",
                child_count=lambda: 0,
            )
            fake_matcher = Mock()
            with patch.object(
                main_module,
                "get_type_data",
                return_value={"matcher": "Alekseev", "png_only": False},
            ), patch.object(
                main_module,
                "get_matcher_for_store",
                return_value=fake_matcher,
            ) as matcher_factory:
                worker = SearchWorker([delivery], directory, {})
                worker._process_delivery(delivery)

            matcher_factory.assert_called_once_with("Shop_WB", "Alekseev")

class SettingsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _stores(self, count=30):
        return {
            f"Store {number:02d}": {
                "WB": {
                    "path": "",
                    "matcher": "Simple",
                    "copy_on_override": False,
                    "override_path": "",
                    "source": "local",
                    "png_only": False,
                },
                "OZON": {
                    "path": "",
                    "matcher": "Simple",
                    "copy_on_override": False,
                    "override_path": "",
                    "source": "local",
                    "png_only": False,
                },
            }
            for number in range(count)
        }

    def test_selecting_store_keeps_sidebar_scroll_and_button_instances(self):
        import gui.settings_window as settings_module

        stores = self._stores()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            settings_module, "load_stores", return_value=stores
        ), patch.object(
            settings_module, "get_data_path",
            side_effect=lambda name: str(Path(directory) / name),
        ), patch.object(
            settings_module, "OVERRIDES_FILE", str(Path(directory) / "overrides.json")
        ), patch.object(settings_module, "save_stores"):
            window = settings_module.SettingsWindow()
            try:
                window.resize(760, 380)
                window.show()
                self.app.processEvents()
                scroll_bar = window.stores_scroll_area.verticalScrollBar()
                self.assertGreater(scroll_bar.maximum(), 0)
                scroll_bar.setValue(scroll_bar.maximum() // 2)
                self.app.processEvents()
                expected_scroll = scroll_bar.value()
                previous_buttons = dict(window.store_buttons)

                window.select_store("Store 25")
                self.app.processEvents()

                self.assertEqual(scroll_bar.value(), expected_scroll)
                self.assertEqual(set(window.store_buttons), set(previous_buttons))
                for name, button in previous_buttons.items():
                    self.assertIs(window.store_buttons[name], button)
            finally:
                window.close()
                self.app.processEvents()

    def test_png_only_is_saved_independently_for_wb_and_ozon(self):
        import gui.settings_window as settings_module

        stores = self._stores(count=1)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            settings_module, "load_stores", return_value=stores
        ), patch.object(
            settings_module, "get_data_path",
            side_effect=lambda name: str(Path(directory) / name),
        ), patch.object(
            settings_module, "OVERRIDES_FILE", str(Path(directory) / "overrides.json")
        ), patch.object(settings_module, "save_stores") as save_mock:
            window = settings_module.SettingsWindow()
            try:
                window._on_png_only_toggled("WB", True)
                self.assertTrue(window.stores["Store 00"]["WB"]["png_only"])
                self.assertFalse(window.stores["Store 00"]["OZON"]["png_only"])
                self.assertTrue(save_mock.called)
            finally:
                window.close()
                self.app.processEvents()


    def test_matcher_is_saved_independently_and_resets_runtime_cache(self):
        import gui.settings_window as settings_module

        stores = self._stores(count=1)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            settings_module, "load_stores", return_value=stores
        ), patch.object(
            settings_module, "get_data_path",
            side_effect=lambda name: str(Path(directory) / name),
        ), patch.object(
            settings_module, "OVERRIDES_FILE", str(Path(directory) / "overrides.json")
        ), patch.object(settings_module, "save_stores") as save_mock:
            window = settings_module.SettingsWindow()
            try:
                window._on_matcher_changed("WB", "PdfOnly")
                self.assertEqual(
                    window.stores["Store 00"]["WB"]["matcher"], "PdfOnly"
                )
                self.assertEqual(
                    window.stores["Store 00"]["OZON"]["matcher"], "Simple"
                )
                self.assertTrue(window.needs_reset_cache)
                self.assertTrue(save_mock.called)
            finally:
                window.close()
                self.app.processEvents()

class RestartSearchTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cleanup_failure_blocks_restart_before_state_changes(self):
        from types import SimpleNamespace
        import gui.main_window as main_module

        window = main_module.MainWindow()
        delivery = SimpleNamespace(
            output_folder=r"C:\locked\delivery",
            pdf_path=r"C:\pdf\delivery.pdf",
        )
        try:
            with patch.object(
                main_module.QMessageBox,
                "question",
                return_value=main_module.QMessageBox.Yes,
            ), patch.object(
                main_module.QMessageBox, "critical"
            ) as critical, patch.object(
                main_module.os.path, "exists", return_value=True
            ), patch.object(
                main_module.shutil, "rmtree", side_effect=PermissionError("locked")
            ):
                window.on_restart_clicked(delivery)

            critical.assert_called_once()
            self.assertFalse(window.is_searching)
            self.assertIsNone(window._pending_restart_item)
        finally:
            window.cloud_auto_timer.stop()
            window.deleteLater()
            self.app.processEvents()

class StoreSuffixPersonalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_and_invalid_config_values_default_to_no_exclusions(self):
        self.assertEqual(normalise_store_suffix_exclusions(None), set())
        self.assertEqual(normalise_store_suffix_exclusions("Store"), set())
        self.assertEqual(normalise_store_suffix_exclusions({"Store": True}), set())
        self.assertEqual(
            normalise_store_suffix_exclusions(["Store", 1, "", "Other"]),
            {"Store", "Other"},
        )

    def test_suffix_uses_internal_store_name_and_display_alias(self):
        self.assertFalse(
            should_append_store_suffix("Internal", True, {"Internal"})
        )
        excluded_name = _delivery_output_folder_name(
            r"C:\input\delivery.pdf",
            "Visible alias",
            should_append_store_suffix("Internal", True, {"Internal"}),
        )
        included_name = _delivery_output_folder_name(
            r"C:\input\delivery.pdf",
            "Visible alias",
            should_append_store_suffix("Internal", True, {"Other"}),
        )
        globally_disabled = _delivery_output_folder_name(
            r"C:\input\delivery.pdf",
            "Visible alias",
            should_append_store_suffix("Internal", False, set()),
        )
        self.assertEqual(excluded_name, "delivery")
        self.assertEqual(globally_disabled, "delivery")
        self.assertEqual(included_name, "delivery [Visible alias]")
        self.assertTrue(
            should_append_store_suffix("Unknown", True, {"Internal"})
        )

    def _open_settings(self, directory, config):
        import gui.settings_window as settings_module

        config_path = Path(directory) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        stores = {
            "Store 00": {"WB": {"path": "", "source": "local"}},
            "Store 01": {"WB": {"path": "", "source": "local"}},
        }
        patches = (
            patch.object(settings_module, "load_stores", return_value=stores),
            patch.object(
                settings_module,
                "get_data_path",
                side_effect=lambda name: str(Path(directory) / name),
            ),
            patch.object(
                settings_module,
                "OVERRIDES_FILE",
                str(Path(directory) / "manual_overrides.json"),
            ),
            patch.object(settings_module, "save_stores"),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        return settings_module, settings_module.SettingsWindow()

    def test_button_count_and_enabled_state_follow_global_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            _, window = self._open_settings(
                directory,
                {
                    "personalization_append_store_to_delivery_folder": True,
                    "personalization_store_suffix_exclusions": [
                        "Store 00", "Deleted Store"
                    ],
                },
            )
            try:
                self.assertTrue(window.store_suffix_exclusions_button.isEnabled())
                self.assertEqual(
                    window.store_suffix_exclusions_button.text(), "Кроме… (1)"
                )
                window.append_store_name_checkbox.setChecked(False)
                self.app.processEvents()
                self.assertFalse(window.store_suffix_exclusions_button.isEnabled())
                self.assertIn("Deleted Store", window.store_suffix_exclusions)
            finally:
                window.close()
                self.app.processEvents()

    def test_cancel_does_not_write_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_module, window = self._open_settings(
                directory,
                {"personalization_append_store_to_delivery_folder": True},
            )
            fake_dialog = Mock()
            fake_dialog.exec.return_value = QDialog.DialogCode.Rejected
            try:
                with patch.object(
                    settings_module,
                    "StoreSuffixExclusionsDialog",
                    return_value=fake_dialog,
                ), patch.object(settings_module, "atomic_write_json") as writer:
                    window._edit_store_suffix_exclusions()
                writer.assert_not_called()
            finally:
                window.close()
                self.app.processEvents()

    def test_accept_saves_once_and_prunes_deleted_stores(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_module, window = self._open_settings(
                directory,
                {
                    "personalization_append_store_to_delivery_folder": True,
                    "personalization_store_suffix_exclusions": ["Deleted Store"],
                },
            )
            fake_dialog = Mock()
            fake_dialog.exec.return_value = QDialog.DialogCode.Accepted
            fake_dialog.selected_store_names.return_value = {
                "Store 01", "Deleted Store"
            }
            try:
                with patch.object(
                    settings_module,
                    "StoreSuffixExclusionsDialog",
                    return_value=fake_dialog,
                ), patch.object(settings_module, "atomic_write_json") as writer:
                    window._edit_store_suffix_exclusions()
                writer.assert_called_once()
                saved_config = writer.call_args.args[1]
                self.assertEqual(
                    saved_config["personalization_store_suffix_exclusions"],
                    ["Store 01"],
                )
                self.assertEqual(window.store_suffix_exclusions, {"Store 01"})
                self.assertEqual(
                    window.store_suffix_exclusions_button.text(), "Кроме… (1)"
                )
            finally:
                window.close()
                self.app.processEvents()

    def test_dialog_shows_alias_and_internal_name(self):
        import gui.settings_window as settings_module

        dialog = settings_module.StoreSuffixExclusionsDialog(
            ["Internal"],
            lambda _: "Visible alias",
            {"Internal"},
        )
        try:
            checkbox = dialog._checkboxes["Internal"]
            self.assertEqual(checkbox.text(), "Visible alias (Internal)")
            self.assertTrue(checkbox.isChecked())
            self.assertEqual(dialog.selected_store_names(), {"Internal"})
        finally:
            dialog.close()
            self.app.processEvents()

if __name__ == "__main__":
    unittest.main()
