"""Errors raised by the orders filter."""

from __future__ import annotations


class OrderFilterError(ValueError):
    """Base class for every invalid filter argument."""


class InvalidStatusError(OrderFilterError):
    """Raised when an unknown order status is requested."""


class InvalidRangeError(OrderFilterError):
    """Raised when the requested amount range is contradictory."""


class InvalidTierError(OrderFilterError):
    """Raised when an unknown customer tier is requested."""
