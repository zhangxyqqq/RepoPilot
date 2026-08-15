from decimal import Decimal


def delivery_charge(subtotal: Decimal, threshold: Decimal) -> Decimal:
    if subtotal > threshold:
        return Decimal("0.00")
    return Decimal("5.00")
