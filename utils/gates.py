"""Pure gate math — no LLM calls, no I/O. Kept separate from the LangGraph
node functions specifically so this logic can be unit tested in isolation,
without mocking an entire graph run."""

from models.schema import ItemValidationResult, PurchaseOrder


def compute_gate1_fraction(order: PurchaseOrder) -> float:
    """Fraction of required fields present: supplier.name, plus
    product_code/quantity/unit_price for every item. An order with no
    items at all is treated as having only the supplier.name slot,
    since there's nothing else to check."""
    if not order.items:
        return 1.0 if order.supplier.name else 0.0

    total_slots = 1 + 3 * len(order.items)
    filled = 1 if order.supplier.name else 0
    for item in order.items:
        filled += sum(
            [
                item.product_code is not None,
                item.quantity is not None,
                item.unit_price is not None,
            ]
        )
    return filled / total_slots


def compute_gate2_rate(item_validations: list[ItemValidationResult]) -> float:
    """Fraction of items that passed RAG validation. No items validated
    yet means 0.0, not a divide-by-zero — an empty validation set should
    never pass Gate 2 by default."""
    if not item_validations:
        return 0.0
    valid_count = sum(1 for v in item_validations if v.is_valid)
    return valid_count / len(item_validations)