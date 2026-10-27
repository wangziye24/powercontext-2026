"""Data model of the orders package."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    """One order that can be filtered."""

    id: str
    status: str
    amount: float | None
    name: str
    customer_email: str
    customer_tier: str | None = None
