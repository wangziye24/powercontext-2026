"""Order filtering rules.

The behaviour of this module is defined by ``specs/filtering.md``. Every
specification entry that applies to this function is referenced in the
docstring so a development assistant can connect a change to its rule.
"""

from __future__ import annotations

from collections.abc import Iterable

from .errors import InvalidRangeError, InvalidStatusError, InvalidTierError
from .models import Order

VALID_STATUSES = ("paid", "pending", "cancelled")
VALID_TIERS = ("VIP", "GOLD", "SILVER")


def filter_orders(
    orders: Iterable[Order],
    *,
    status: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    keyword: str | None = None,
    tier: str | None = None,
) -> list[Order]:
    """Return the orders that satisfy every provided condition.

    Implements SPEC-001 (status), SPEC-002 (amount range), SPEC-003 (keyword),
    SPEC-004 (customer tier), SPEC-005 (AND combination), SPEC-006 (no
    condition) and SPEC-007 (orders without an amount).
    """

    if status is not None and status not in VALID_STATUSES:
        raise InvalidStatusError(f"unknown order status: {status!r}")
    if tier is not None:
        normalized_tier = tier.upper() if isinstance(tier, str) else ""
        if normalized_tier not in VALID_TIERS and normalized_tier != "NONE":
            raise InvalidTierError(f"unknown customer tier: {tier!r}")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise InvalidRangeError(
            f"min_amount {min_amount} is greater than max_amount {max_amount}"
        )

    selected = list(orders)

    if status is not None:
        selected = [order for order in selected if order.status == status]

    if min_amount is not None or max_amount is not None:
        selected = [
            order
            for order in selected
            if order.amount is not None
            and (min_amount is None or order.amount >= min_amount)
            and (max_amount is None or order.amount <= max_amount)
        ]

    if keyword:
        needle = keyword.lower()
        selected = [
            order
            for order in selected
            if needle in order.name.lower() or needle in order.customer_email.lower()
        ]

    if tier is not None:
        if normalized_tier == "NONE":
            selected = [order for order in selected if order.customer_tier is None]
        else:
            selected = [
                order
                for order in selected
                if order.customer_tier is not None
                and order.customer_tier.upper() == normalized_tier
            ]

    return selected
