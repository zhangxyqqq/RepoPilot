from decimal import Decimal


def display_tax(value: Decimal) -> str:
    """Presentation-only tax formatting; it does not choose who pays tax."""
    return f"Tax: {value:.2f}"
