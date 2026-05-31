"""Proposal Queue State Machine for Horizon Trading Platform.

Manages the lifecycle of trade proposals:
- PROPOSED: waiting for human approval
- APPROVED: human approved, order submitted
- EXECUTED: order filled
- REJECTED: human rejected
- EXPIRED: time or price drift exceeded
- AUTO_EXECUTED: future Step 3 (not implemented now)
"""

from .models import ProposalStatus, TradeProposal
from .queue import (
    InvalidProposalStateError,
    ProposalExpiredError,
    ProposalNotFoundError,
    ProposalQueue,
)

__all__ = [
    "ProposalStatus",
    "TradeProposal",
    "ProposalQueue",
    "ProposalNotFoundError",
    "InvalidProposalStateError",
    "ProposalExpiredError",
]