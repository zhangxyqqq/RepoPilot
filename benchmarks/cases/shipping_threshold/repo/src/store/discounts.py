from decimal import Decimal


def loyalty_discount(subtotal: Decimal, minimum: Decimal) -> Decimal:
    """A separate threshold rule that intentionally remains strictly greater."""
    return Decimal("2.00") if subtotal > minimum else Decimal("0.00")
