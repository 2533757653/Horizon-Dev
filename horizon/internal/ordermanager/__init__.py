"""Order Manager module for Horizon Trading Platform."""

from .manager import OrderManager, OrderSubmissionError

__all__ = ["OrderManager", "OrderSubmissionError"]