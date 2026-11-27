import json
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import trigu_api
from core.trigu_api import TriguSupply
from core.ozon_sizes import strip_matching_article_size_suffix
from gui.dialogs.load_supplies_dialog import (
    FILTER_COMBO_WIDTH, SUPPLY_SEARCH_WIDTH, LoadSuppliesDialog,
)
from gui.dialogs.trigu_report_dialog import (
    ALL_PERIOD_ID, LEGACY_PERIOD_ID, aggregate_supply_reports,
    build_report_periods, filter_supply_reports, format_supply_report,
    supply_report_type,
)
from gui.main_window import MainWindow
from gui.widgets.supply_tree import SupplyTreeModel
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView


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
    def test_request_uses_verified_ssl_context(self):
        context = object()
        response = {"data": [], "pagination": {"has_more": False}}
        with patch.object(trigu_api, "urlopen", return_value=_Response(response)) as request:
            trigu_api.TriguClient("trg_test", ssl_context=context).list_active_supplies()
        self.assertIs(request.call_args.kwargs["context"], context)

    def test_lists_all_pages_of_active_supplies(self):
        responses = [
            {"data": [{"number": "WB-1", "name": "One", "shop": "Shop", "marketplace": "wb", "status": "forming", "status_label": "Формируется", "items_count": 1}], "pagination": {"has_more": True}},
            {"data": [], "pagination": {"has_more": False}},
            {"data": [
                {"number": "OZ-2", "name": "Two", "shop": "Ozon Shop", "marketplace": "ozon", "status": "production", "status_label": "В производстве", "items_count": 2},
                {"number": "WB-1", "name": "Duplicate", "shop": "Shop", "marketplace": "wb", "status": "forming", "status_label": "Формируется", "items_count": 1},
            ], "pagination": {"has_more": False}},
        ]
        with patch.object(trigu_api, "urlopen", side_effect=[_Response(value) for value in responses]) as request:
            supplies = trigu_api.TriguClient("trg_test").list_active_supplies()
        self.assertEqual([s.number for s in supplies], ["WB-1", "OZ-2"])
        self.assertEqual(supplies[1].marketplace, "OZON")
        urls = [call.args[0].full_url for call in request.call_args_list]
        self.assertIn("marketplace=wb", urls[0])
        self.assertIn("page=2", urls[1])
        self.assertIn("marketplace=ozon", urls[2])

    def test_items_follow_pagination(self):
        responses = [
            {"data": {"items": [{"article": "A"}]}, "pagination": {"has_more": True}},
            {"data": {"items": [{"article": "B"}]}, "pagination": {"has_more": False}},
        ]
        with patch.object(trigu_api, "urlopen", side_effect=[_Response(value) for value in responses]):
            items = trigu_api.TriguClient("trg_test").get_supply_items("WB-1")
        self.assertEqual([item["article"] for item in items], ["A", "B"])

    def test_get_supply_loads_legacy_supply_metadata(self):
        response = {
            "data": [{
                "number": "OZ-1", "name": "Поставка", "shop": "Магазин",
                "marketplace": "ozon", "status": "production",
                "status_label": "В производстве", "items_count": 3,
            }],
        }
        with patch.object(trigu_api, "urlopen", return_value=_Response(response)):
            supply = trigu_api.TriguClient("trg_test").get_supply("OZ-1")
        self.assertEqual(supply.shop, "Магазин")
        self.assertEqual(supply.marketplace, "OZON")

    def test_key_is_saved_in_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch.object(trigu_api, "get_data_path", return_value=str(path)):
                trigu_api.save_api_key("trg_secret")
                self.assertEqual(trigu_api.load_api_key(), "trg_secret")

    def test_preferences_are_persisted_without_losing_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"trigu_api_key": "secret"}), encoding="utf-8")
            with patch.object(trigu_api, "get_data_path", return_value=str(path)):
                trigu_api.save_trigu_preferences(
                    marketplace_order=["OZON", "WB"],
                    category_order=["Кружки", " кружки ", "Лонгсливы"],
                )
                self.assertEqual(trigu_api.load_marketplace_order(), ["OZON", "WB"])
                self.assertEqual(trigu_api.load_category_order(), ["Кружки", "Лонгсливы"])
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["trigu_api_key"], "secret")

    def test_new_categories_are_appended_alphabetically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"trigu_category_order": ["Футболки"]}), encoding="utf-8")
            with patch.object(trigu_api, "get_data_path", return_value=str(path)):
                result = trigu_api.register_categories([
                    "Лонгсливы", "кружки", "Кружки", "",
                ])
            self.assertEqual(result, ["Футболки", "кружки", "Лонгсливы"])

    def test_processed_supplies_are_persisted_without_losing_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"trigu_api_key": "secret", "trigu_category_order": ["Кружки"]}),
                encoding="utf-8",
            )
            with patch.object(trigu_api, "get_data_path", return_value=str(path)):
                self.assertEqual(
                    trigu_api.mark_supply_processed("oz-wh-1"), {"OZ-WH-1"}
                )
                self.assertEqual(trigu_api.load_processed_supply_numbers(), {"OZ-WH-1"})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["trigu_api_key"], "secret")
            self.assertEqual(saved["trigu_category_order"], ["Кружки"])

    def test_processed_supply_report_persists_category_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"trigu_api_key": "secret"}), encoding="utf-8")
            with patch.object(trigu_api, "get_data_path", return_value=str(path)):
                reports = trigu_api.save_processed_supply_report(
                    "oz-1", "Никита", "ozon", {"Кружки": 4, "": 1},
                    supply_name="Никита доп", supply_status="dogruzka",
                )
                self.assertEqual(reports["OZ-1"]["categories"], {"Кружки": 4, "Без категории": 1})
                self.assertEqual(reports["OZ-1"]["supply_status"], "dogruzka")
                self.assertEqual(trigu_api.load_processed_supply_numbers(), {"OZ-1"})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["trigu_api_key"], "secret")
            self.assertIn("OZ-1", saved["trigu_processed_supply_reports"])

    def test_search_runs_finishes_and_history_clear_keep_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"trigu_api_key": "secret"}), encoding="utf-8")
            with patch.object(trigu_api, "get_data_path", return_value=str(path)):
                first = trigu_api.record_search_run("2026-11-27T10:00:00+07:00")
                self.assertEqual(first, "2026-11-27T10:00:00+07:00")
                self.assertTrue(trigu_api.mark_work_finished("2026-11-27T13:00:00+07:00"))
                self.assertFalse(trigu_api.mark_work_finished("2026-11-27T14:00:00+07:00"))
                trigu_api.clear_report_history()
                self.assertEqual(trigu_api.load_search_runs(), [])
                self.assertEqual(trigu_api.load_work_finishes(), [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["trigu_api_key"], "secret")


class LoadSuppliesDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _supplies():
        return [
            TriguSupply("WB-1", "Короткое имя", "WB Shop", "WB", "forming", "Формируется", 1),
            TriguSupply("OZ-2", "Очень длинное название поставки Ozon", "Ozon Shop", "OZON", "production", "В производстве", 25),
        ]

    def _dialog(self):
        dialog = LoadSuppliesDialog(auto_load=False)
        dialog._supplies = self._supplies()
        dialog._populate_filters()
        dialog._resize_columns_to_all_contents()
        dialog._apply_filters()
        return dialog

    @staticmethod
    def _row_for(dialog, number):
        return next(
            row for row in range(dialog.table.rowCount())
            if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == number
        )

    def test_statuses_are_fixed_and_production_is_shown_as_in_work(self):
        dialog = self._dialog()
        self.assertEqual(
            [dialog.status_filter.itemText(index) for index in range(3)],
            ["Все", "Формируется", "В работе"],
        )
        ozon_row = self._row_for(dialog, "OZ-2")
        self.assertEqual(dialog.table.item(ozon_row, 5).text(), "В работе")

    def test_checkbox_survives_filter_and_right_list_can_remove_it(self):
        dialog = self._dialog()
        wb_row = self._row_for(dialog, "WB-1")
        dialog.table.item(wb_row, 0).setCheckState(Qt.CheckState.Checked)
        self.app.processEvents()
        self.assertEqual(dialog.selected_list.count(), 1)
        self.assertEqual(dialog.selected_list.item(0).text(), "Короткое имя")
        dialog.status_filter.setCurrentIndex(2)
        self.app.processEvents()
        self.assertIn("WB-1", dialog._selected_numbers)
        dialog._remove_from_selected(dialog.selected_list.item(0))
        self.assertNotIn("WB-1", dialog._selected_numbers)

    def test_header_checkbox_selects_only_visible_supplies(self):
        dialog = self._dialog()
        dialog.marketplace_filter.setCurrentIndex(2)
        dialog._toggle_visible_selection()
        self.assertEqual(dialog._selected_numbers, {"OZ-2"})
        self.assertEqual(
            dialog.table.horizontalHeader()._check_state,
            Qt.CheckState.Checked,
        )
        dialog.marketplace_filter.setCurrentIndex(0)
        self.assertEqual(
            dialog.table.horizontalHeader()._check_state,
            Qt.CheckState.PartiallyChecked,
        )
        dialog._toggle_visible_selection()
        self.assertEqual(dialog._selected_numbers, {"WB-1", "OZ-2"})

    def test_processed_supply_is_highlighted(self):
        dialog = self._dialog()
        dialog._processed_numbers = {"WB-1"}
        dialog._apply_filters()
        row = self._row_for(dialog, "WB-1")
        self.assertEqual(dialog.table.item(row, 2).background().color().name(), "#244d35")
        self.assertEqual(dialog.table.item(row, 2).toolTip(), "Поставка уже была обработана")

    def test_clicking_another_cell_does_not_change_checked_supplies(self):
        dialog = self._dialog()
        wb_row = self._row_for(dialog, "WB-1")
        ozon_row = self._row_for(dialog, "OZ-2")
        dialog.table.item(wb_row, 0).setCheckState(Qt.CheckState.Checked)
        dialog.table.setCurrentCell(ozon_row, 2)
        self.app.processEvents()
        self.assertEqual(dialog._selected_numbers, {"WB-1"})

    def test_columns_are_measured_then_fixed(self):
        dialog = self._dialog()
        header = dialog.table.horizontalHeader()
        self.assertEqual(
            header.sectionResizeMode(2), QHeaderView.ResizeMode.Fixed,
        )
        self.assertGreater(header.sectionSize(2), header.sectionSize(6))

    def test_filter_controls_have_compact_search_and_wider_combos(self):
        dialog = self._dialog()
        self.assertEqual(dialog.name_search.width(), SUPPLY_SEARCH_WIDTH)
        self.assertEqual(dialog.shop_filter.width(), FILTER_COMBO_WIDTH)
        self.assertEqual(dialog.status_filter.width(), FILTER_COMBO_WIDTH)
        self.assertEqual(
            dialog.marketplace_filter.width(), FILTER_COMBO_WIDTH,
        )

    def test_marketplaces_remain_grouped_when_secondary_sort_changes(self):
        dialog = self._dialog()
        dialog._supplies.extend([
            TriguSupply("WB-3", "Я WB", "WB Shop", "WB", "forming", "Формируется", 3),
            TriguSupply("OZ-4", "А Ozon", "Ozon Shop", "OZON", "production", "В производстве", 4),
        ])
        dialog.marketplace_order = ["OZON", "WB"]
        dialog._sort_column = 1
        dialog._sort_order = Qt.SortOrder.DescendingOrder
        dialog._apply_filters()
        marketplaces = [dialog.table.item(row, 4).text() for row in range(4)]
        self.assertEqual(marketplaces, ["OZON", "OZON", "WB", "WB"])

    def test_marketplace_filter_and_name_search_combine(self):
        dialog = self._dialog()
        dialog.marketplace_filter.setCurrentIndex(2)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog._visible_number(0), "OZ-2")
        dialog.name_search.setText("длинное")
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.name_search.setText("wb shop")
        self.assertEqual(dialog.table.rowCount(), 0)

    def test_dialog_loads_supplies_automatically(self):
        with patch(
            "gui.dialogs.load_supplies_dialog.TriguClient.list_active_supplies",
            return_value=self._supplies(),
        ):
            dialog = LoadSuppliesDialog()
            self.app.processEvents()
        self.assertEqual(dialog.table.rowCount(), 2)


class TriguOrderingTests(unittest.TestCase):
    def test_trigu_article_removes_only_its_matching_size_suffix(self):
        self.assertEqual(
            strip_matching_article_size_suffix("ART, XL", "XL"), "ART"
        )
        self.assertIsNone(
            strip_matching_article_size_suffix("ART, L", "XL")
        )
        self.assertIsNone(
            strip_matching_article_size_suffix("ART, XL", "0")
        )

    def test_products_follow_category_order_and_keep_order_inside_group(self):
        items = [
            {"article": "L1", "category": "Лонгсливы"},
            {"article": "M1", "category": "Кружки"},
            {"article": "M2", "category": " кружки "},
            {"article": "X", "category": ""},
        ]
        result = MainWindow._sort_trigu_items(items, ["Кружки", "Лонгсливы"])
        self.assertEqual([item["article"] for item in result], ["M1", "M2", "L1", "X"])

    def test_root_deliveries_follow_marketplace_order_stably(self):
        model = SupplyTreeModel()
        model.add_delivery("wb-1", "S", "WB", "/p")
        model.add_delivery("oz-1", "S", "OZON", "/p")
        model.add_delivery("wb-2", "S", "WB", "/p")
        model.reorder_deliveries(["OZON", "WB"])
        self.assertEqual(
            [item.pdf_path for item in model.root_item.child_items],
            ["oz-1", "wb-1", "wb-2"],
        )

    def test_report_counts_each_supply_once_per_category(self):
        reports = {
            "WB-1": {"shop": "Магазин", "marketplace": "WB", "categories": {"Кружки": 3, "Футболки": 1}},
            "WB-2": {"shop": "Магазин", "marketplace": "WB", "categories": {"Кружки": 5}},
            "OZ-1": {"shop": "Магазин", "marketplace": "OZON", "categories": {"Кружки": 2}},
        }
        stores, totals = aggregate_supply_reports(reports)
        self.assertEqual(stores[("Магазин", "WB")]["Кружки"], [2, 8])
        self.assertEqual(totals["Кружки"], [3, 10])
        self.assertIn("Кружки — 3 поставок, 10 штук", format_supply_report(reports))

    def test_trigu_category_quantities_uses_api_quantities(self):
        result = MainWindow._trigu_category_quantities([
            {"category": "Кружки", "quantity": 3},
            {"category": "", "quantity": 0},
        ])
        self.assertEqual(result, {"Кружки": 3, "Без категории": 1})

    def test_report_periods_split_largest_daily_gap_and_respect_manual_finish(self):
        runs = [
            "2026-11-27T10:00:00+07:00", "2026-11-27T12:00:00+07:00",
            "2026-11-27T14:00:00+07:00", "2026-11-27T17:00:00+07:00",
            "2026-11-27T20:00:00+07:00", "2026-11-28T09:00:00+07:00",
        ]
        periods = build_report_periods(runs, [])
        self.assertEqual([period["label"] for period in periods], [
            "28.11 · 09:00–09:00", "27.11 · 17:00–20:00", "27.11 · 10:00–14:00",
        ])
        manual = build_report_periods(runs[:3], ["2026-11-27T13:00:00+07:00"])
        self.assertEqual([period["label"] for period in manual], [
            "27.11 · 14:00–14:00", "27.11 · 12:00–13:00", "27.11 · 10:00–10:00",
        ])

    def test_report_filters_period_category_store_and_marketplace(self):
        reports = {
            "WB-1": {"shop": "A", "marketplace": "WB", "categories": {"Кружки": 3}, "search_started_at": "2026-11-27T10:00:00+07:00"},
            "OZ-1": {"shop": "B", "marketplace": "OZON", "categories": {"Футболки": 2}},
        }
        periods = build_report_periods(["2026-11-27T10:00:00+07:00"], [])
        self.assertEqual(
            set(filter_supply_reports(reports, periods, period_id=periods[0]["id"], category="Кружки", shop="A", marketplace="WB")),
            {"WB-1"},
        )
        self.assertEqual(
            set(filter_supply_reports(reports, periods, period_id=LEGACY_PERIOD_ID)), {"OZ-1"},
        )
        self.assertEqual(
            set(filter_supply_reports(reports, periods, period_id=ALL_PERIOD_ID)),
            {"WB-1", "OZ-1"},
        )

    def test_sort_and_extra_are_grouped_without_duplication(self):
        reports = {
            "WB-1": {"shop": "Магазин", "marketplace": "WB", "categories": {"Кружки": 1}},
            "WB-2": {"shop": "Магазин", "marketplace": "WB", "categories": {"Кружки": 2}, "supply_status": "sorting"},
            "OZ-1": {"shop": "Магазин", "marketplace": "OZON", "categories": {"Футболки": 3}, "supply_name": "Вечерние допы"},
        }
        self.assertEqual(supply_report_type(reports["WB-1"]), "Основные")
        self.assertEqual(supply_report_type(reports["WB-2"]), "Сорт")
        self.assertEqual(supply_report_type(reports["OZ-1"]), "Доп")
        text = format_supply_report(reports)
        self.assertIn("Основные\n  Кружки — 1 поставок, 1 штук", text)
        self.assertIn("Сорт\n  Кружки — 1 поставок, 2 штук", text)
        self.assertIn("Доп\n  Футболки — 1 поставок, 3 штук", text)

    def test_restarting_one_delivery_does_not_record_a_new_report_run(self):
        self.assertNotIn(
            "_record_report_search_run", inspect.getsource(MainWindow.on_restart_clicked),
        )
