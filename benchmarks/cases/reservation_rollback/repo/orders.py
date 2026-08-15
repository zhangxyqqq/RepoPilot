from inventory import Inventory


def place_order(inventory: Inventory, requested_skus: list[str]) -> dict[str, object]:
    inventory.reserve_many(requested_skus)
    return {"accepted": True, "items": list(requested_skus)}
