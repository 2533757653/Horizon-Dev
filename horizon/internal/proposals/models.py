"""Proposal models for Horizon Trading Platform.

Provides ProposalStatus enum and extends TradeProposal with database-friendly methods.
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

import aiosqlite

from horizon.internal.llm.parser import TradeProposal as BaseTradeProposal


class ProposalStatus(Enum):
    """Status values for trade proposals."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    AUTO_EXECUTED = "auto_executed"


def _parse_datetime(value) -> Optional[datetime]:
    """Parse a datetime value from database or string.

    Args:
        value: datetime object, string, or None.

    Returns:
        datetime object or None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Handle ISO format with timezone
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _extend_trade_proposal():
    """Extend TradeProposal with additional methods."""

    def is_expired(self, current_price: Decimal) -> bool:
        """Check if the proposal has expired based on time or price drift.

        Args:
            current_price: Current market price for the symbol.

        Returns:
            True if status is PROPOSED and either:
            - datetime.now() > expires_at (time expired)
            - abs(current_price - proposed_price) / proposed_price > price_drift_threshold_pct / 100
        """
        if self.status != ProposalStatus.PROPOSED.value:
            return False

        # Check time expiry
        if datetime.now(timezone.utc) > self.expires_at:
            return True

        # Check price drift
        if self.proposed_price > 0:
            price_drift = abs(float(current_price) - self.proposed_price) / self.proposed_price
            if price_drift > self.price_drift_threshold_pct / 100:
                return True

        return False

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "TradeProposal":
        """Create a TradeProposal instance from a database row.

        Args:
            row: Database row from aiosqlite.

        Returns:
            TradeProposal instance.
        """
        return cls(
            id=row["id"],
            status=row["status"],
            exchange=row["exchange"],
            symbol=row["symbol"],
            side=row["side"],
            order_type=row["order_type"],
            price=row["price"],
            volume=row["volume"],
            confidence_score=row["confidence_score"],
            risk_tier=row["risk_tier"],
            llm_rationale=row["llm_rationale"],
            llm_raw_response=row["llm_raw_response"],
            technical_context=row["technical_context"],
            market_snapshot=row["market_snapshot"],
            portfolio_snapshot=row["portfolio_snapshot"],
            guardrail_result=row["guardrail_result"],
            approved_by=row["approved_by"],
            approved_at=_parse_datetime(row["approved_at"]),
            executed_order_id=row["executed_order_id"],
            expires_at=_parse_datetime(row["expires_at"]),
            price_drift_threshold_pct=row["price_drift_threshold_pct"],
            proposed_price=row["proposed_price"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def to_dict(self) -> dict:
        """Serialize all fields to a dict, converting Decimal to str and datetime to ISO format.

        Returns:
            Dictionary representation of the proposal.
        """
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, Decimal):
                result[key] = str(value)
            elif isinstance(value, datetime):
                result[key] = value.isoformat() if value else None
            else:
                result[key] = value
        return result

    # Attach methods to the class
    BaseTradeProposal.is_expired = is_expired
    BaseTradeProposal.from_row = classmethod(from_row) if not isinstance(from_row, classmethod) else from_row
    BaseTradeProposal.to_dict = to_dict


_extend_trade_proposal()

# Re-export for public API
TradeProposal = BaseTradeProposal

__all__ = ["ProposalStatus", "TradeProposal"]