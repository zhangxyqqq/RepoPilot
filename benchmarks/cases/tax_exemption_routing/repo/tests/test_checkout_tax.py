from decimal import Decimal

from commerce.customer import Customer
from commerce.orders import checkout_total


def test_active_exempt_customer_is_not_charged_tax():
    customer = Customer(active=True, tax_exempt=True)
    assert checkout_total(customer, Decimal("100.00"), Decimal("0.25")) == Decimal("100.00")


def test_active_taxable_customer_is_charged_tax():
    customer = Customer(active=True, tax_exempt=False)
    assert checkout_total(customer, Decimal("100.00"), Decimal("0.25")) == Decimal("125.00")
