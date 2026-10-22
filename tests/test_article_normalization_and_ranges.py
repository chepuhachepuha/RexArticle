import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.store_manager as store_manager
import manual_overrides
from matcher import AlekseevMatcher, PdfOnlyMatcher, RozenbergMatcher


class StoreRangeMigrationTests(unittest.TestCase):
    def tearDown(self):
        store_manager.invalidate_cache()

    def test_kristabell_missing_ranges_is_migrated_but_empty_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stores.json"
            path.write_text(json.dumps({
                "Migrated": {"WB": {"matcher": "Kristabell", "path": "/a"}},
                "Empty": {"OZON": {"matcher": "Kristabell", "path": "/b", "size_ranges": []}},
            }), encoding="utf-8")
            with patch.object(store_manager, "STORES_FILE", str(path)):
                store_manager.invalidate_cache()
                stores = store_manager.load_stores()
            self.assertEqual(
                [item["pattern"] for item in stores["Migrated"]["WB"]["size_ranges"]],
                ["(XS_XL)", "(2XL_6XL)", "(XS_6XL)"],
            )
            self.assertEqual(stores["Empty"]["OZON"]["size_ranges"], [])


class ManualOverrideCompatibilityTests(unittest.TestCase):
    def tearDown(self):
        manual_overrides.invalidate_cache()

    def test_old_readable_key_matches_normalised_article(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual_overrides.json"
            path.write_text(json.dumps({
                "Shop_WB": {"АРТ 123_XL": "/prints/manual.png"}
            }), encoding="utf-8")
            with patch.object(manual_overrides, "OVERRIDES_FILE", str(path)):
                manual_overrides.invalidate_cache()
                result = manual_overrides.get_override("Shop", "WB", "арт123", "xl")
                stored = manual_overrides.load_overrides()
            self.assertEqual(result, "/prints/manual.png")
            self.assertIn("АРТ 123_XL", stored["Shop_WB"])


class RangeMatcherCompatibilityTests(unittest.TestCase):
    def _find(self, matcher, article, index, allowed=None):
        ranges = [{"pattern": "(S_L)", "sizes": ["S", "M", "L"]}]
        with patch("matcher.get_index_for_store", return_value=index), patch(
            "matcher.get_override", return_value=None
        ):
            return matcher.find_file_by_article_and_size(
                article, "M", "Shop_WB", allowed_extensions=allowed,
                size_ranges=ranges,
            )

    def test_rozenberg_transform_applies_to_range(self):
        self.assertEqual(
            self._find(RozenbergMatcher(), "ART-extra", {"art(s_l)": ["/p/a.png"]}),
            ["/p/a.png"],
        )

    def test_alekseev_transform_applies_to_range(self):
        self.assertEqual(
            self._find(AlekseevMatcher(), "ARTBlack", {"art(s_l)": ["/p/a.png"]}),
            ["/p/a.png"],
        )

    def test_pdf_filter_applies_to_range(self):
        result = self._find(
            PdfOnlyMatcher(), "ART",
            {"art(s_l)": ["/p/a.png", "/p/a.PDF"]},
            allowed={".pdf"},
        )
        self.assertEqual(result, ["/p/a.PDF"])


if __name__ == "__main__":
    unittest.main()
