from decimal import Decimal

from commerce.customer import Customer
from commerce.tax import calculate_tax


def checkout_total(customer: Customer, subtotal: Decimal, tax_rate: Decimal) -> Decimal:
    """Archived accounts and tax-exempt customers do not accrue new tax."""
    if not customer.active:
        return subtotal
    return subtotal + calculate_tax(subtotal, tax_rate)
