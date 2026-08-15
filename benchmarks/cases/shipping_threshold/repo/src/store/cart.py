from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LineItem:
    unit_price: Decimal
    quantity: int = 1


def merchandise_total(items: list[LineItem]) -> Decimal:
    return sum((item.unit_price * item.quantity for item in items), Decimal("0.00"))
