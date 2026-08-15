from decimal import Decimal

from store.cart import LineItem, merchandise_total
from store.shipping import delivery_charge


def checkout_total(items: list[LineItem], free_delivery_at: Decimal) -> Decimal:
    subtotal = merchandise_total(items)
    return subtotal + delivery_charge(subtotal, free_delivery_at)
