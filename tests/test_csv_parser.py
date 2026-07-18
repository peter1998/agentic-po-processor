"""Unit tests for the deterministic CSV parsing path (ADR-006) —
no LLM involved, so this should always be fast and predictable."""

import pytest

from utils.csv_parser import try_deterministic_csv_parse


class TestDeterministicCsvParse:
    def test_real_valid_order_csv_parses_correctly(self):
        """Against the actual demo file, not a synthetic example."""
        order = try_deterministic_csv_parse("data/demo_files/valid_order.csv")
        assert order is not None
        assert order.supplier.name == "Acme Fasteners Ltd"
        assert order.items[0].product_code == "FAS-M8-100"
        assert order.items[0].quantity == 500.0
        assert order.items[0].unit_price == 0.18

    def test_mismatched_headers_returns_none(self, tmp_path):
        csv_file = tmp_path / "weird_headers.csv"
        csv_file.write_text("vendor,sku,qty,price\nAcme,X,1,1.0\n")
        assert try_deterministic_csv_parse(str(csv_file)) is None

    def test_empty_csv_headers_only_returns_none(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("supplier_name,product_code,quantity,unit_price\n")
        assert try_deterministic_csv_parse(str(csv_file)) is None

    def test_multi_row_csv_produces_multiple_items(self, tmp_path):
        csv_file = tmp_path / "multi.csv"
        csv_file.write_text(
            "supplier_name,product_code,quantity,unit_price\n"
            "Acme Fasteners Ltd,FAS-M8-100,100,0.15\n"
            "Acme Fasteners Ltd,FAS-M8-100,50,0.20\n"
        )
        order = try_deterministic_csv_parse(str(csv_file))
        assert order is not None
        assert len(order.items) == 2