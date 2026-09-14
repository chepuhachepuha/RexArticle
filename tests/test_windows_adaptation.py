import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cancellation import CancellationToken, OperationCancelled
from core.json_storage import atomic_write_json
from core.storage_backend import copy_resource
import core.paths as paths


class CancellationTests(unittest.TestCase):
    def test_token_is_idempotent(self):
        token = CancellationToken()
        self.assertFalse(token.is_cancelled())
        token.cancel()
        token.cancel()
        self.assertTrue(token.is_cancelled())
        with self.assertRaises(OperationCancelled):
            token.raise_if_cancelled()


class AtomicStorageTests(unittest.TestCase):
    def test_atomic_json_and_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            atomic_write_json(str(config), {"name": "RexArticles"})
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["name"], "RexArticles")

            source = root / "source.bin"
            destination = root / "destination.bin"
            source.write_bytes(b"rex" * 1024)
            copy_resource(str(source), str(destination))
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertFalse(Path(str(destination) + ".part").exists())

    def test_cancelled_copy_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            destination = root / "destination.bin"
            source.write_bytes(b"data")
            token = CancellationToken()
            token.cancel()
            with self.assertRaises(OperationCancelled):
                copy_resource(str(source), str(destination), cancel_token=token)
            self.assertFalse(destination.exists())
            self.assertFalse(Path(str(destination) + ".part").exists())


class OutputFolderTests(unittest.TestCase):
    def test_existing_output_folder_is_preserved(self):
        from gui.main_window import _normalise_output_folder

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                _normalise_output_folder(directory), os.path.normpath(directory)
            )

    def test_stale_macos_path_uses_windows_desktop(self):
        from gui.main_window import QStandardPaths, _normalise_output_folder

        redirected = r"C:\Users\User\OneDrive\Desktop"
        with patch.object(QStandardPaths, "writableLocation", return_value=redirected):
            self.assertEqual(
                _normalise_output_folder("/Users/old/Desktop"),
                os.path.normpath(redirected),
            )

    def test_virtual_desktop_name_uses_physical_windows_desktop(self):
        from gui.main_window import QStandardPaths, _normalise_output_folder

        physical_desktop = r"C:\Users\User\Desktop"
        with patch.object(QStandardPaths, "writableLocation", return_value=physical_desktop):
            self.assertEqual(
                _normalise_output_folder("Рабочий стол"),
                os.path.normpath(physical_desktop),
            )

    def test_windows_folder_dialog_avoids_virtual_shell_objects(self):
        import gui.main_window as main_window
        from PySide6.QtWidgets import QFileDialog

        with patch.object(main_window.sys, "platform", "win32"):
            options = main_window._output_directory_dialog_options()
        self.assertTrue(options & QFileDialog.Option.ShowDirsOnly)
        self.assertTrue(options & QFileDialog.Option.DontUseNativeDialog)

class GeneratedNameTests(unittest.TestCase):
    def test_manual_override_article_cannot_create_subfolder(self):
        from gui.main_window import _result_file_base_name

        override = r"C:\prints\manual.tif"
        result = _result_file_base_name(
            override,
            "ДетскийКостюмG3000/Roblox- 99Nigths3Heroes",
            override,
        )
        self.assertEqual(
            result, "ДетскийКостюмG3000_Roblox- 99Nigths3Heroes"
        )
        self.assertNotIn("/", result)
        self.assertNotIn("\\", result)

    def test_all_windows_invalid_characters_are_removed(self):
        from gui.main_window import _safe_filename_component

        result = _safe_filename_component('a<b>c:d"e/f\\g|h?i*j')
        for character in '<>:"/\\|?*':
            self.assertNotIn(character, result)

    def test_reserved_windows_name_is_prefixed(self):
        from gui.main_window import _safe_filename_component

        self.assertEqual(_safe_filename_component("CON"), "_CON")

class SettingsMigrationTests(unittest.TestCase):
    def test_windows_migration_strips_token_and_keeps_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            legacy_dir = root / "legacy"
            app_dir.mkdir()
            legacy_dir.mkdir()
            legacy_config = legacy_dir / "config.json"
            legacy_config.write_text(
                json.dumps({"output_folder": "C:/Prints", "yandex_oauth_token": "secret"}),
                encoding="utf-8",
            )
            frozen_was_present = hasattr(sys, "frozen")
            frozen_value = getattr(sys, "frozen", None)
            sys.frozen = True
            try:
                with patch.object(paths, "get_app_dir", return_value=str(app_dir)), patch.object(
                    paths, "get_runtime_data_dir", return_value=str(legacy_dir)
                ), patch.object(paths.sys, "platform", "win32"):
                    migrated = Path(paths.get_data_path("config.json"))
            finally:
                if frozen_was_present:
                    sys.frozen = frozen_value
                else:
                    del sys.frozen
            data = json.loads(migrated.read_text(encoding="utf-8"))
            self.assertEqual(data["output_folder"], "C:/Prints")
            self.assertNotIn("yandex_oauth_token", data)
            self.assertTrue(legacy_config.exists())

    def test_existing_sidecar_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            legacy_dir = root / "legacy"
            app_dir.mkdir()
            legacy_dir.mkdir()
            sidecar = app_dir / "stores.json"
            sidecar.write_text('{"local": {}}', encoding="utf-8")
            (legacy_dir / "stores.json").write_text('{"legacy": {}}', encoding="utf-8")
            with patch.object(paths, "get_settings_dir", return_value=str(app_dir)), patch.object(
                paths, "get_runtime_data_dir", return_value=str(legacy_dir)
            ):
                result = paths.get_data_path("stores.json")
            self.assertEqual(Path(result), sidecar)
            self.assertIn("local", sidecar.read_text(encoding="utf-8"))



class FilenameCaseTests(unittest.TestCase):
    def test_indexer_preserves_original_path_and_filename_case(self):
        from indexer import _index_from_files, _normalise_path, _roots, _scan_files

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "MiXeD Print.PNG"
            source.write_bytes(b"png")
            normalised = _normalise_path(str(source))
            self.assertEqual(Path(normalised).name, "MiXeD Print.PNG")

            files = _scan_files(_roots(directory))
            stored_path = next(iter(files))
            self.assertEqual(Path(stored_path).name, "MiXeD Print.PNG")
            index = _index_from_files(files)
            self.assertIn("MiXeD Print", index)
            self.assertEqual(Path(index["MiXeD Print"][0]).name, "MiXeD Print.PNG")

    def test_result_filename_keeps_source_case(self):
        from gui.main_window import _result_file_base_name

        result = _result_file_base_name(
            r"C:\Prints\MiXeD Article.PNG",
            "ignored",
            None,
        )
        self.assertEqual(result, "MiXeD Article")

if __name__ == "__main__":
    unittest.main()