import pytest

from inventory import Inventory, OutOfStock
from orders import place_order


def test_failed_order_restores_items_reserved_before_failure():
    inventory = Inventory({"book": 2, "pen": 0})

    with pytest.raises(OutOfStock):
        place_order(inventory, ["book", "pen"])

    assert inventory.snapshot() == {"book": 2, "pen": 0}


def test_successful_order_consumes_stock():
    inventory = Inventory({"book": 2, "pen": 1})
    place_order(inventory, ["book", "pen"])
    assert inventory.snapshot() == {"book": 1, "pen": 0}
