"""Deterministic CSV -> PurchaseOrder conversion. Only used when the CSV
headers match the expected set exactly (ADR-006). Anything else falls back
to the LLM extraction path in services/llm.py."""

import csv

from models.schema import Item, PurchaseOrder, Supplier

EXPECTED_HEADERS = {"supplier_name", "product_code", "quantity", "unit_price"}


def try_deterministic_csv_parse(file_path: str) -> PurchaseOrder | None:
    """Returns a PurchaseOrder if the CSV headers match exactly, else None
    (caller should fall back to LLM extraction on raw text)."""
    with open(file_path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return None
        if set(reader.fieldnames) != EXPECTED_HEADERS:
            return None

        rows = list(reader)
        if not rows:
            return None

        supplier_name = rows[0]["supplier_name"]
        items = [
            Item(
                product_code=row["product_code"],
                quantity=float(row["quantity"]),
                unit_price=float(row["unit_price"]),
            )
            for row in rows
        ]
        return PurchaseOrder(supplier=Supplier(name=supplier_name), items=items)