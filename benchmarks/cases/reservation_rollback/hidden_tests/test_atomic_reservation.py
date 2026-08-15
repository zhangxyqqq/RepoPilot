import pytest

from inventory import Inventory, OutOfStock


def test_failure_on_third_item_rolls_back_every_prior_item():
    inventory = Inventory({"a": 2, "b": 1, "c": 0})
    with pytest.raises(OutOfStock):
        inventory.reserve_many(["a", "b", "c"])
    assert inventory.snapshot() == {"a": 2, "b": 1, "c": 0}


def test_duplicate_sku_is_rolled_back_once_per_reserved_unit():
    inventory = Inventory({"a": 2, "missing": 0})
    with pytest.raises(OutOfStock):
        inventory.reserve_many(["a", "a", "missing"])
    assert inventory.snapshot() == {"a": 2, "missing": 0}


def test_duplicate_sku_success_consumes_both_units():
    inventory = Inventory({"a": 2})
    inventory.reserve_many(["a", "a"])
    assert inventory.available("a") == 0
