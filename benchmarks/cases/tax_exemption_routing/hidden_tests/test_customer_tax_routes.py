from decimal import Decimal

from commerce.customer import Customer
from commerce.orders import checkout_total


def test_archived_customer_remains_untaxed():
    customer = Customer(active=False, tax_exempt=False)
    assert checkout_total(customer, Decimal("80.00"), Decimal("0.20")) == Decimal("80.00")


def test_exempt_customer_with_fractional_total_is_unchanged():
    customer = Customer(active=True, tax_exempt=True)
    assert checkout_total(customer, Decimal("19.99"), Decimal("0.20")) == Decimal("19.99")


def test_taxable_customer_still_uses_tax_calculation():
    customer = Customer(active=True, tax_exempt=False)
    assert checkout_total(customer, Decimal("19.99"), Decimal("0.20")) == Decimal("23.99")
