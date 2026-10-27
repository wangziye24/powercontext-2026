"""Shared fixtures of the orders test suite."""

from __future__ import annotations

import pytest

from orders.models import Order


@pytest.fixture
def orders() -> list[Order]:
    """Return a fixed sample of orders used by every test."""

    return [
        Order(
            id="o-1",
            status="paid",
            amount=120.0,
            name="Laptop Stand",
            customer_email="Ada@Example.com",
            customer_tier="VIP",
        ),
        Order(
            id="o-2",
            status="pending",
            amount=45.5,
            name="Keyboard",
            customer_email="bob@example.com",
            customer_tier="gold",
        ),
        Order(
            id="o-3",
            status="paid",
            amount=None,
            name="Warranty Extension",
            customer_email="carol@example.com",
            customer_tier=None,
        ),
        Order(
            id="o-4",
            status="cancelled",
            amount=980.0,
            name="Monitor",
            customer_email="dave@corp.example.com",
            customer_tier="SILVER",
        ),
        Order(
            id="o-5",
            status="paid",
            amount=45.5,
            name="Keyboard Cover",
            customer_email="eve@example.com",
            customer_tier="VIP",
        ),
    ]
