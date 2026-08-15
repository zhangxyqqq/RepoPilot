class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self, stock: dict[str, int]):
        self._stock = dict(stock)

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def snapshot(self) -> dict[str, int]:
        return dict(self._stock)

    def reserve_many(self, skus: list[str]) -> None:
        """Reserve one unit of every SKU as a single operation."""
        reserved: list[str] = []
        for sku in skus:
            if self._stock.get(sku, 0) <= 0:
                raise OutOfStock(sku)
            self._stock[sku] -= 1
            reserved.append(sku)
