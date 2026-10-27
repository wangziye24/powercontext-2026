"""Regression tests for the behaviour that must survive the change.

Each test names the specification entry it protects, so a failing regression
points straight at the rule that was broken.
"""

from __future__ import annotations

import pytest

from orders.errors import InvalidRangeError, InvalidStatusError
from orders.filter import filter_orders


def test_spec_001_filters_by_status(orders):
    """SPEC-001: filtering by status keeps only the orders in that status."""
    paid = filter_orders(orders, status="paid")
    assert [order.id for order in paid] == ["o-1", "o-3", "o-5"]


def test_spec_001_rejects_unknown_status(orders):
    """SPEC-001: an unknown status is rejected with InvalidStatusError."""
    with pytest.raises(InvalidStatusError):
        filter_orders(orders, status="refunded")


def test_spec_002_amount_range_is_inclusive(orders):
    """SPEC-002: the amount range is closed on both ends."""
    selected = filter_orders(orders, min_amount=45.5, max_amount=120.0)
    assert [order.id for order in selected] == ["o-1", "o-2", "o-5"]


def test_spec_002_rejects_contradictory_range(orders):
    """SPEC-002: a contradictory range is rejected with InvalidRangeError."""
    with pytest.raises(InvalidRangeError):
        filter_orders(orders, min_amount=100.0, max_amount=10.0)


def test_spec_002_single_endpoint_is_unbounded(orders):
    """SPEC-002: a single endpoint leaves the other side unbounded."""
    assert [order.id for order in filter_orders(orders, min_amount=900.0)] == ["o-4"]
    assert [order.id for order in filter_orders(orders, max_amount=46.0)] == ["o-2", "o-5"]


def test_spec_003_keyword_is_case_insensitive(orders):
    """SPEC-003: the keyword is matched case insensitively."""
    selected = filter_orders(orders, keyword="KEYBOARD")
    assert [order.id for order in selected] == ["o-2", "o-5"]


def test_spec_003_keyword_matches_email(orders):
    """SPEC-003: the keyword also matches the customer email."""
    selected = filter_orders(orders, keyword="ada@example.com")
    assert [order.id for order in selected] == ["o-1"]


def test_spec_003_empty_keyword_does_not_filter(orders):
    """SPEC-003: an empty keyword is treated as no keyword at all."""
    assert len(filter_orders(orders, keyword="")) == len(orders)


def test_spec_005_conditions_are_combined_with_and(orders):
    """SPEC-005: several conditions are combined with AND."""
    selected = filter_orders(orders, status="paid", min_amount=50.0)
    assert [order.id for order in selected] == ["o-1"]


def test_spec_005_result_order_is_stable(orders):
    """SPEC-005: the result keeps the order of the input."""
    selected = filter_orders(orders, status="paid")
    assert [order.id for order in selected] == sorted(
        [order.id for order in selected], key=lambda item: [o.id for o in orders].index(item)
    )


def test_spec_006_without_conditions_every_order_is_kept(orders):
    """SPEC-006: without any condition every order is returned."""
    assert [order.id for order in filter_orders(orders)] == [
        "o-1",
        "o-2",
        "o-3",
        "o-4",
        "o-5",
    ]


def test_spec_007_orders_without_amount_are_excluded_by_range(orders):
    """SPEC-007: orders without an amount never survive an amount range."""
    selected = filter_orders(orders, min_amount=0.0)
    assert "o-3" not in [order.id for order in selected]


def test_spec_007_orders_without_amount_survive_without_range(orders):
    """SPEC-007: orders without an amount stay when no range is given."""
    assert "o-3" in [order.id for order in filter_orders(orders)]
