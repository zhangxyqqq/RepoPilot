from dataclasses import dataclass


@dataclass(frozen=True)
class Customer:
    active: bool = True
    tax_exempt: bool = False
