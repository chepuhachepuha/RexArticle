import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import indexer
from core.store_manager import normalise_additional_sources
from gui.main_window import SearchWorker


class SharedSourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_dir = str(Path(self.temp.name) / "db")
        self.source = Path(self.temp.name) / "prints"
        self.source.mkdir()
        indexer.INDEX_CACHE.clear()
        indexer.SOURCE_INDEX_CACHE.clear()
        self.index_patch = patch.object(indexer, "INDEX_DIR", self.database_dir)
        self.index_patch.start()

    def tearDown(self):
        self.index_patch.stop()
        indexer.INDEX_CACHE.clear()
        indexer.SOURCE_INDEX_CACHE.clear()
        self.temp.cleanup()

    def test_same_root_has_one_shared_source_for_two_stores(self):
        (self.source / "ART.png").write_bytes(b"print")
        self.assertTrue(indexer.update_source_index("local", str(self.source)))
        shared = set()
        indexer.update_index_for_store(
            "First_WB", str(self.source), updated_source_keys=shared
        )
        indexer.update_index_for_store(
            "Second_OZON", str(self.source), updated_source_keys=shared
        )
        with indexer._open_connection() as connection:
            sources = connection.execute("SELECT COUNT(*) FROM source_state").fetchone()[0]
            files = connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
        self.assertEqual((sources, files), (1, 1))
        self.assertEqual(
            indexer.get_index_for_store("First_WB"),
            indexer.get_index_for_store("Second_OZON"),
        )

    def test_v3_duplicate_store_indexes_migrate_once_with_backup(self):
        database = Path(self.database_dir) / indexer.DB_FILENAME
        database.parent.mkdir(parents=True)
        file_path = str(self.source / "ART.png")
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE store_state(
                    store TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
                    last_scan_ns INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE roots(
                    store TEXT NOT NULL, priority INTEGER NOT NULL, path TEXT NOT NULL,
                    PRIMARY KEY(store, priority)
                );
                CREATE TABLE files(
                    store TEXT NOT NULL, path TEXT NOT NULL, article TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,
                    priority INTEGER NOT NULL, PRIMARY KEY(store, path)
                );
                """
            )
            for store in ("First_WB", "Second_OZON"):
                connection.execute(
                    "INSERT INTO store_state VALUES (?, 3, 0)", (store,)
                )
                connection.execute(
                    "INSERT INTO roots VALUES (?, 1, ?)",
                    (store, str(self.source)),
                )
                connection.execute(
                    "INSERT INTO files VALUES (?, ?, 'art', 0, 0, 1)",
                    (store, file_path),
                )
        with indexer._open_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM source_state").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],
                1,
            )
        self.assertTrue(Path(str(database) + ".v3-backup").exists())

    def test_unchanged_refresh_does_not_enumerate_files(self):
        (self.source / "ART.png").write_bytes(b"print")
        indexer.update_source_index("local", str(self.source), force_full=True)
        with patch.object(indexer.os, "scandir", wraps=os.scandir) as scandir:
            changed = indexer.update_source_index("local", str(self.source))
        self.assertFalse(changed)
        scandir.assert_not_called()

    def test_changed_nested_directory_updates_only_changed_content(self):
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "OLD.png").write_bytes(b"old")
        indexer.update_source_index("local", str(self.source), force_full=True)
        (nested / "OLD.png").rename(nested / "NEW.png")
        stat = nested.stat()
        os.utime(nested, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        self.assertTrue(indexer.update_source_index("local", str(self.source)))
        result = indexer.get_index_for_source("local", str(self.source))
        self.assertNotIn("old", result)
        self.assertIn("new", result)


class AdditionalSourceSearchTests(unittest.TestCase):
    @staticmethod
    def _delivery(directory):
        child = SimpleNamespace(article="ART", size="M", location="")
        return SimpleNamespace(
            store_name="Shop", supply_type="WB",
            pdf_path=str(Path(directory) / "Delivery.pdf"),
            display_store_name="Shop", child_count=lambda: 1,
            child=lambda _index: child,
        )

    def test_additional_sources_follow_order_and_precede_manual_override(self):
        import gui.main_window as main_module

        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.png"
            manual = Path(directory) / "manual.png"
            for path in (first, second, manual):
                path.write_bytes(path.name.encode())
            sources = [
                {"source": "local", "path": str(Path(directory) / "one")},
                {"source": "local", "path": str(Path(directory) / "two")},
            ]
            for source in sources:
                Path(source["path"]).mkdir()
            matcher = Mock()

            def find(*_args, **kwargs):
                supplied = kwargs.get("index")
                if supplied is None:
                    return []
                if supplied == {"first": []}:
                    return [str(first)]
                if supplied == {"second": []}:
                    return [str(second)]
                return [str(manual)] if kwargs.get("include_override") else []

            matcher.find_file_by_article_and_size.side_effect = find
            delivery = self._delivery(directory)
            worker = SearchWorker([delivery], directory, {})
            with patch.object(
                main_module, "get_type_data", return_value={
                    "matcher": "Simple", "png_only": False,
                    "additional_sources": sources,
                },
            ), patch.object(
                main_module, "get_matcher_for_store", return_value=matcher,
            ), patch.object(
                main_module, "has_source_index", return_value=True,
            ), patch.object(
                main_module, "get_index_for_source",
                side_effect=({"first": []}, {"second": []}),
            ):
                worker._process_delivery(delivery)
            copied = next(
                path for path in Path(directory).rglob("*.png")
                if path.name == "1. first [M].png"
            )
            self.assertEqual(copied.read_bytes(), b"first.png")

    def test_primary_copy_error_falls_back_to_additional_source(self):
        import gui.main_window as main_module

        with tempfile.TemporaryDirectory() as directory:
            fallback_dir = Path(directory) / "fallback"
            fallback_dir.mkdir()
            fallback = fallback_dir / "ART.png"
            fallback.write_bytes(b"fallback")
            matcher = Mock()
            matcher.find_file_by_article_and_size.side_effect = (
                lambda *_args, **kwargs: (
                    [str(Path(directory) / "missing.png")]
                    if kwargs.get("index") is None
                    else [str(fallback)] if kwargs.get("index") else []
                )
            )
            delivery = self._delivery(directory)
            worker = SearchWorker([delivery], directory, {})
            with patch.object(
                main_module, "get_type_data", return_value={
                    "matcher": "Simple", "png_only": False,
                    "additional_sources": [
                        {"source": "local", "path": str(fallback_dir)}
                    ],
                },
            ), patch.object(
                main_module, "get_matcher_for_store", return_value=matcher,
            ), patch.object(
                main_module, "has_source_index", return_value=True,
            ), patch.object(
                main_module, "get_index_for_source", return_value={"fallback": []},
            ), patch.object(main_module, "remove_missing_path_from_index"):
                worker._process_delivery(delivery)
            copied = next(
                path for path in Path(directory).rglob("*.png")
                if path.name == "1. ART [M].png"
            )
            self.assertEqual(copied.read_bytes(), b"fallback")


class AdditionalSourceConfigTests(unittest.TestCase):
    def test_duplicates_and_primary_path_are_removed_preserving_order(self):
        values = [
            {"source": "local", "path": "/main"},
            {"source": "local", "path": "/main/../main/"},
            {"source": "local", "path": "/extra"},
            {"source": "local", "path": "/EXTRA"},
            {"source": "yandex", "path": "disk:/extra"},
            {"source": "yandex", "path": "DISK:/extra/"},
        ]
        self.assertEqual(
            normalise_additional_sources(values, "local", "/main"),
            [
                {"source": "local", "path": "/extra"},
                {"source": "yandex", "path": "disk:/extra"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
