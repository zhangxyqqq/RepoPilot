from inventory import Inventory


def all_available(inventory: Inventory, skus: list[str]) -> bool:
    """Read-only availability helper; it does not perform reservations."""
    return all(inventory.available(sku) > 0 for sku in skus)
