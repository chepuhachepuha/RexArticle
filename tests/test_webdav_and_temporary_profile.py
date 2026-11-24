import os
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import indexer
from core import network_paths, storage_backend
from gui.dialogs import new_store_dialog


class NetworkPathTests(unittest.TestCase):
    def test_macos_mount_table_detects_webdav_with_arbitrary_volume_name(self):
        with patch.object(network_paths.sys, "platform", "darwin"), patch.object(
            network_paths, "_read_macos_network_mounts",
            return_value=("/Volumes/Print archive",),
        ):
            self.assertTrue(
                network_paths.is_network_path(
                    "/Volumes/Print archive/Shop/ART.png"
                )
            )


class WebDavCopyTests(unittest.TestCase):
    def test_partial_copy_is_removed_and_retry_succeeds_without_copystat(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = str(Path(directory) / "result.png")
            temporary = Path(destination + ".part")
            attempts = []

            def copy_attempt(_source, target, part, **_kwargs):
                attempts.append(part)
                if len(attempts) == 1:
                    Path(part).write_bytes(b"partial")
                    raise OSError("connection dropped")
                Path(target).write_bytes(b"complete")

            with patch.object(
                storage_backend, "is_network_path", return_value=True,
            ), patch.object(
                storage_backend, "network_operation", return_value=nullcontext(),
            ), patch.object(
                storage_backend, "_copy_local_file", side_effect=copy_attempt,
            ), patch.object(storage_backend.time, "sleep") as sleep_mock, patch.object(
                storage_backend.shutil, "copystat"
            ) as copystat:
                result = storage_backend.copy_resource(
                    "/Volumes/Print archive/ART.png", destination
                )

            self.assertEqual(result, destination)
            self.assertEqual(Path(destination).read_bytes(), b"complete")
            self.assertFalse(temporary.exists())
            self.assertEqual(len(attempts), 2)
            sleep_mock.assert_called_once_with(1)
            copystat.assert_not_called()

    def test_exhausted_retries_remove_part_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = str(Path(directory) / "result.png")
            temporary = Path(destination + ".part")

            def fail(_source, _target, part, **_kwargs):
                Path(part).write_bytes(b"partial")
                raise OSError("offline")

            with patch.object(
                storage_backend, "is_network_path", return_value=True,
            ), patch.object(
                storage_backend, "network_operation", return_value=nullcontext(),
            ), patch.object(
                storage_backend, "_copy_local_file", side_effect=fail,
            ), patch.object(storage_backend.time, "sleep"):
                with self.assertRaises(storage_backend.NetworkReadError):
                    storage_backend.copy_resource(
                        "/Volumes/Print archive/ART.png", destination
                    )
            self.assertFalse(temporary.exists())

    def test_two_webdav_copies_are_serialized(self):
        active = 0
        maximum = 0
        state_lock = threading.Lock()

        def slow_copy(_source, _target, _part, **_kwargs):
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1

        with patch.object(
            storage_backend, "is_network_path", return_value=True,
        ), patch.object(
            network_paths, "is_network_path", return_value=True,
        ), patch.object(
            storage_backend, "_copy_local_file", side_effect=slow_copy,
        ):
            threads = [
                threading.Thread(
                    target=storage_backend.copy_resource,
                    args=(f"/Volumes/cloud/{number}.png", f"/tmp/{number}.png"),
                )
                for number in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(maximum, 1)


class WebDavIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "prints"
        self.source.mkdir()
        (self.source / "ART.png").write_bytes(b"print")
        self.index_patch = patch.object(
            indexer, "INDEX_DIR", str(Path(self.temp.name) / "index")
        )
        self.index_patch.start()
        indexer.INDEX_CACHE.clear()
        indexer.SOURCE_INDEX_CACHE.clear()

    def tearDown(self):
        self.index_patch.stop()
        indexer.INDEX_CACHE.clear()
        indexer.SOURCE_INDEX_CACHE.clear()
        self.temp.cleanup()

    def test_existing_webdav_index_is_not_scanned_automatically(self):
        indexer.update_source_index("local", str(self.source), force_full=True)
        with patch.object(indexer, "_is_network_path", return_value=True), patch.object(
            indexer, "_scan_local_source_full"
        ) as scanner:
            changed = indexer.update_source_index("local", str(self.source))
        self.assertFalse(changed)
        scanner.assert_not_called()

    def test_transient_directory_error_is_retried(self):
        original_scandir = os.scandir
        calls = 0

        def flaky(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary disconnect")
            return original_scandir(path)

        with patch.object(indexer, "_is_network_path", return_value=True), patch.object(
            indexer.os, "scandir", side_effect=flaky,
        ), patch.object(indexer.time, "sleep") as sleep_mock:
            indexer.update_source_index("local", str(self.source), force_full=True)
        self.assertGreaterEqual(calls, 2)
        sleep_mock.assert_called_once_with(1)

    def test_failed_full_scan_preserves_previous_index(self):
        indexer.update_source_index("local", str(self.source), force_full=True)
        before = indexer.get_index_for_source("local", str(self.source))
        with patch.object(indexer, "_is_network_path", return_value=True), patch.object(
            indexer.os, "scandir", side_effect=OSError("offline"),
        ), patch.object(indexer.time, "sleep"):
            with self.assertRaises(indexer.NetworkScanError):
                indexer.update_source_index(
                    "local", str(self.source), force_full=True
                )
        indexer.SOURCE_INDEX_CACHE.clear()
        self.assertEqual(
            indexer.get_index_for_source("local", str(self.source)), before
        )


class TemporaryProfileDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def stores():
        return {
            "Profile": {
                "WB": {"path": "/prints/wb", "source": "local"},
                "OZON": {"path": "disk:/prints/ozon", "source": "yandex"},
            }
        }

    def test_unknown_defaults_to_temporary_profile_and_does_not_save(self):
        with patch.object(
            new_store_dialog, "get_pdf_text", return_value="",
        ), patch.object(
            new_store_dialog, "detect_supply_type",
            return_value={"store_name": "UNKNOWN", "supply_type": "WB"},
        ), patch.object(
            new_store_dialog, "load_stores", return_value=self.stores(),
        ), patch.object(new_store_dialog, "save_stores") as save_mock:
            dialog = new_store_dialog.NewStoreDialog(None, "unknown.pdf")
            try:
                self.assertTrue(dialog.profile_mode.isChecked())
                dialog.profile_store_combo.setCurrentText("Profile")
                dialog.profile_type_combo.setCurrentText("OZON")
                dialog.save()
                self.assertEqual(dialog.result, {
                    "store_name": "Profile",
                    "supply_type": "OZON",
                    "folder": "disk:/prints/ozon",
                    "source": "yandex",
                    "temporary_profile": True,
                })
                save_mock.assert_not_called()
            finally:
                dialog.close()

    def test_each_unknown_pdf_requires_a_new_selection(self):
        with patch.object(
            new_store_dialog, "get_pdf_text", return_value="",
        ), patch.object(
            new_store_dialog, "detect_supply_type",
            return_value={"store_name": "UNKNOWN", "supply_type": "WB"},
        ), patch.object(
            new_store_dialog, "load_stores", return_value=self.stores(),
        ):
            first = new_store_dialog.NewStoreDialog(None, "first.pdf")
            second = new_store_dialog.NewStoreDialog(None, "second.pdf")
            try:
                first.profile_type_combo.setCurrentText("OZON")
                self.assertEqual(second.profile_type_combo.currentText(), "WB")
            finally:
                first.close()
                second.close()

    def test_detected_new_store_can_copy_current_type_from_template(self):
        stores = {
            "Profile": {
                "WB": {
                    "path": "/prints/wb",
                    "source": "local",
                    "matcher": "Kristabell",
                    "size_ranges": [{"pattern": "S", "sizes": ["S"]}],
                    "additional_sources": [
                        {"source": "local", "path": "/shared/prints"},
                    ],
                },
                "OZON": {"path": "disk:/prints/ozon", "source": "yandex"},
            }
        }
        with patch.object(
            new_store_dialog, "get_pdf_text", return_value="",
        ), patch.object(
            new_store_dialog, "detect_supply_type",
            return_value={"store_name": "Detected shop", "supply_type": "WB"},
        ), patch.object(
            new_store_dialog, "load_stores", return_value=stores,
        ), patch.object(new_store_dialog, "save_stores") as save_mock:
            dialog = new_store_dialog.NewStoreDialog(None, "detected.pdf")
            try:
                self.assertTrue(dialog.create_mode.isChecked())
                self.assertFalse(dialog.template_container.isHidden())
                self.assertEqual(
                    [dialog.template_store_combo.itemText(index)
                     for index in range(dialog.template_store_combo.count())],
                    ["Не использовать", "Profile"],
                )
                dialog.template_store_combo.setCurrentText("Profile")
                self.assertTrue(dialog.folder_input.isHidden())
                dialog.save()

                saved_stores = save_mock.call_args.args[0]
                copied = saved_stores["Detected shop"]["WB"]
                self.assertEqual(copied, stores["Profile"]["WB"])
                self.assertIsNot(copied, stores["Profile"]["WB"])
                self.assertIsNot(
                    copied["additional_sources"],
                    stores["Profile"]["WB"]["additional_sources"],
                )
                self.assertNotIn("OZON", saved_stores["Detected shop"])
                self.assertEqual(dialog.result, {
                    "store_name": "Detected shop",
                    "supply_type": "WB",
                    "folder": "/prints/wb",
                    "source": "local",
                })
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
