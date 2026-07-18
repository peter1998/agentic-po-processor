"""Unit tests for the pure gate math in utils/gates.py — no LLM calls,
no I/O, fast and deterministic."""

from models.schema import Item, ItemValidationResult, PurchaseOrder, Supplier
from utils.gates import compute_gate1_fraction, compute_gate2_rate


class TestGate1Fraction:
    def test_complete_single_item_order_scores_1(self):
        order = PurchaseOrder(
            supplier=Supplier(name="Acme Fasteners Ltd"),
            items=[Item(product_code="FAS-M8-100", quantity=500, unit_price=0.18)],
        )
        assert compute_gate1_fraction(order) == 1.0

    def test_one_missing_field_of_four_scores_075(self):
        order = PurchaseOrder(
            supplier=Supplier(name="Precision Sensor Systems GmbH"),
            items=[Item(product_code="SNS-TEMP-4K", quantity=12, unit_price=None)],
        )
        assert compute_gate1_fraction(order) == 0.75

    def test_two_missing_fields_of_four_scores_05_below_default_threshold(self):
        order = PurchaseOrder(
            supplier=Supplier(name="Precision Sensor Systems GmbH"),
            items=[Item(product_code="SNS-TEMP-4K", quantity=None, unit_price=None)],
        )
        assert compute_gate1_fraction(order) == 0.5

    def test_missing_supplier_and_price_scores_05(self):
        order = PurchaseOrder(
            supplier=Supplier(name=None),
            items=[Item(product_code="X", quantity=1, unit_price=None)],
        )
        assert compute_gate1_fraction(order) == 0.5

    def test_multi_item_order_one_item_fully_empty(self):
        order = PurchaseOrder(
            supplier=Supplier(name="Acme Fasteners Ltd"),
            items=[
                Item(product_code="A", quantity=1, unit_price=1.0),
                Item(product_code=None, quantity=None, unit_price=None),
            ],
        )
        # total slots = 1 (supplier) + 3*2 (items) = 7; filled = 1 + 3 + 0 = 4
        assert compute_gate1_fraction(order) == 4 / 7

    def test_zero_items_with_supplier_present_scores_1(self):
        order = PurchaseOrder(supplier=Supplier(name="Acme Fasteners Ltd"), items=[])
        assert compute_gate1_fraction(order) == 1.0

    def test_zero_items_no_supplier_scores_0(self):
        order = PurchaseOrder(supplier=Supplier(name=None), items=[])
        assert compute_gate1_fraction(order) == 0.0


class TestGate2Rate:
    def test_single_invalid_item_scores_0(self):
        validations = [ItemValidationResult(product_code="FAS-M8-100", is_valid=False, reason="price out of range")]
        assert compute_gate2_rate(validations) == 0.0

    def test_single_valid_item_scores_1(self):
        validations = [ItemValidationResult(product_code="FAS-M8-100", is_valid=True, reason="matches catalog")]
        assert compute_gate2_rate(validations) == 1.0

    def test_four_of_five_valid_scores_08(self):
        validations = [
            ItemValidationResult(product_code=f"P{i}", is_valid=(i != 3), reason="x") for i in range(5)
        ]
        assert compute_gate2_rate(validations) == 0.8

    def test_empty_validation_list_scores_0_not_a_silent_pass(self):
        """An empty list must never default to passing the gate — that
        would let an order with zero validated items through silently."""
        assert compute_gate2_rate([]) == 0.0