from decimal import Decimal


def calculate_tax(subtotal: Decimal, rate: Decimal) -> Decimal:
    return (subtotal * rate).quantize(Decimal("0.01"))
