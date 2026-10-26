"""Target tests of the customer tier filter (SPEC-004).

Before the change these tests fail because ``filter_orders`` does not accept a
``tier`` argument at all. After the change every test must pass.
"""

from __future__ import annotations

import pytest

from orders.errors import InvalidTierError
from orders.filter import filter_orders


def test_spec_004_filters_by_tier(orders):
    """SPEC-004: filtering by tier keeps only the orders in that tier."""
    selected = filter_orders(orders, tier="VIP")
    assert [order.id for order in selected] == ["o-1", "o-5"]


def test_spec_004_tier_comparison_is_case_insensitive(orders):
    """SPEC-004: the tier comparison ignores the case."""
    selected = filter_orders(orders, tier="vip")
    assert [order.id for order in selected] == ["o-1", "o-5"]


def test_spec_004_gold_tier_matches(orders):
    """SPEC-004: every allowed tier can be selected."""
    selected = filter_orders(orders, tier="GOLD")
    assert [order.id for order in selected] == ["o-2"]


def test_spec_004_silver_tier_matches(orders):
    """SPEC-004: every allowed tier can be selected."""
    selected = filter_orders(orders, tier="silver")
    assert [order.id for order in selected] == ["o-4"]


def test_spec_004_rejects_unknown_tier(orders):
    """SPEC-004: an unknown tier is rejected with InvalidTierError."""
    with pytest.raises(InvalidTierError):
        filter_orders(orders, tier="diamond")


def test_spec_004_none_tier_matches_orders_without_tier(orders):
    """SPEC-004: only tier NONE selects the orders without a tier."""
    selected = filter_orders(orders, tier="NONE")
    assert [order.id for order in selected] == ["o-3"]


def test_spec_004_tier_none_does_not_filter(orders):
    """SPEC-004: tier None means no tier filtering at all."""
    assert len(filter_orders(orders, tier=None)) == len(orders)


def test_spec_004_combines_with_other_conditions(orders):
    """SPEC-004: the tier filter combines with the other conditions."""
    selected = filter_orders(orders, tier="VIP", status="paid")
    assert [order.id for order in selected] == ["o-1", "o-5"]
