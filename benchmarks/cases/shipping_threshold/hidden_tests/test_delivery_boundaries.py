from decimal import Decimal

from store.cart import LineItem
from store.checkout import checkout_total
from store.shipping import delivery_charge


def test_custom_threshold_is_inclusive():
    assert delivery_charge(Decimal("25.00"), Decimal("25.00")) == Decimal("0.00")


def test_above_threshold_remains_free():
    items = [LineItem(Decimal("75.00"))]
    assert checkout_total(items, Decimal("50.00")) == Decimal("75.00")


def test_one_cent_below_threshold_is_not_free():
    assert delivery_charge(Decimal("49.99"), Decimal("50.00")) == Decimal("5.00")
