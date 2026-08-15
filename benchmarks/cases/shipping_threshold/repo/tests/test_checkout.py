from decimal import Decimal

from store.cart import LineItem
from store.checkout import checkout_total


def test_threshold_total_gets_free_delivery():
    items = [LineItem(Decimal("20.00"), 2), LineItem(Decimal("10.00"))]
    assert checkout_total(items, Decimal("50.00")) == Decimal("50.00")


def test_below_threshold_still_pays_delivery():
    assert checkout_total([LineItem(Decimal("49.99"))], Decimal("50.00")) == Decimal("54.99")
