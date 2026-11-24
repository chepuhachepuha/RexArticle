import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import trigu_api


class _Response:
    def __init__(self, value):
        self.value = value

    def read(self):
        return json.dumps(self.value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class TriguClientTests(unittest.TestCase):
    def test_lists_all_pages_of_active_supplies(self):
        responses = [
            {"data": [{"number": "WB-1", "name": "One", "shop": "Shop", "marketplace": "wb", "status": "forming", "status_label": "Формируется", "items_count": 1}], "pagination": {"has_more": True}},
            {"data": [{"number": "OZ-2", "name": "Two", "shop": "Shop", "marketplace": "ozon", "status": "production", "status_label": "В производстве", "items_count": 2}], "pagination": {"has_more": False}},
        ]
        with patch.object(trigu_api, "urlopen", side_effect=[_Response(value) for value in responses]):
            supplies = trigu_api.TriguClient("trg_test").list_active_supplies()
        self.assertEqual([s.number for s in supplies], ["WB-1", "OZ-2"])
        self.assertEqual(supplies[1].marketplace, "OZON")

    def test_items_follow_pagination(self):
        responses = [
            {"data": {"items": [{"article": "A"}]}, "pagination": {"has_more": True}},
            {"data": {"items": [{"article": "B"}]}, "pagination": {"has_more": False}},
        ]
        with patch.object(trigu_api, "urlopen", side_effect=[_Response(value) for value in responses]):
            items = trigu_api.TriguClient("trg_test").get_supply_items("WB-1")
        self.assertEqual([item["article"] for item in items], ["A", "B"])

    def test_key_is_saved_in_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch.object(trigu_api, "get_data_path", return_value=str(path)):
                trigu_api.save_api_key("trg_secret")
                self.assertEqual(trigu_api.load_api_key(), "trg_secret")
