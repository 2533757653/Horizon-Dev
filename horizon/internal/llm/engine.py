"""LLM Strategy Engine for Horizon Trading Platform.

Coordinates market data gathering, LLM analysis, and proposal generation.
Runs on a configurable schedule via APScheduler.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from ..exchange.adapter import ExchangeAdapter
from ..exchange.registry import ExchangeRegistry
from ..exchange.types import Balance, Ticker
from ..indicators.calculator import TechnicalIndicatorCalculator
from ..marketdata.fetcher import MarketDataFetcher
from ..proposals.queue import ProposalQueue
from .parser import LLMParseError, LLMResponseParser, TradeProposal
from .prompt_builder import MarketContext, PortfolioContext, PromptBuilder

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
DEFAULT_TIMEOUT_SECONDS = 60


@dataclass
class LLMProposalOutput:
    """Output from LLM analysis containing proposals and metadata."""

    proposals: list[TradeProposal]
    market_analysis_summary: str
    confidence_explanation: str


class LLMStrategyEngine:
    """Engine for LLM-driven market analysis and trade proposal generation."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        registry: ExchangeRegistry,
        proposal_queue: ProposalQueue,
        anthropic_client,
        strategy_config: dict,
        indicator_calculator: TechnicalIndicatorCalculator,
        fetcher: MarketDataFetcher,
    ) -> None:
        """Initialize the LLM strategy engine.

        Args:
            db: aiosqlite database connection.
            registry: Exchange registry for accessing market data.
            proposal_queue: Proposal queue for enqueueing generated proposals.
            anthropic_client: Anthropic API client for Claude calls.
            strategy_config: Strategy configuration dictionary.
            indicator_calculator: Technical indicator calculator.
            fetcher: Market data fetcher instance.
        """
        self._db = db
        self._registry = registry
        self._proposal_queue = proposal_queue
        self._anthropic = anthropic_client
        self._strategy_config = strategy_config
        self._indicator_calculator = indicator_calculator
        self._fetcher = fetcher
        self._parser = LLMResponseParser(
            asset_whitelist=self._get_asset_whitelist()
        )

    def _get_asset_whitelist(self) -> list[str]:
        """Extract asset whitelist from strategy config."""
        whitelist = self._strategy_config.get("asset_whitelist", "[]")
        if isinstance(whitelist, str):
            try:
                return json.loads(whitelist)
            except json.JSONDecodeError:
                return []
        return whitelist

    async def analyze_and_propose(self) -> LLMProposalOutput:
        """Execute full LLM analysis pipeline and return proposals.

        This method:
        1. Loads the active strategy config from database
        2. Gathers market context (tickers, orderbooks, trades, indicators)
        3. Gathers portfolio context (snapshot, balances, positions)
        4. Builds a prompt for Claude API
        5. Calls Claude API with timeout
        6. Parses the response
        7. Records analysis history
        8. Enqueues proposals

        Returns:
            LLMProposalOutput with proposals and analysis metadata.
        """
        start_time = time.monotonic()
        raw_prompt = ""
        raw_response = ""
        token_count_input = 0
        token_count_output = 0

        # Step 1: Load active strategy config from database
        strategy_config = await self._load_active_strategy_config()
        if not strategy_config:
            logger.error("No active strategy config found")
            return LLMProposalOutput(
                proposals=[],
                market_analysis_summary="",
                confidence_explanation="",
            )

        asset_whitelist = self._get_asset_whitelist_from_config(strategy_config)

        try:
            # Step 2: Gather market context
            market_context = await self._gather_market_context(asset_whitelist)

            # Step 3: Gather portfolio context
            portfolio_context = await self._gather_portfolio_context()

            # Step 4: Build prompt
            prompt_builder = PromptBuilder(strategy_config)
            raw_prompt = prompt_builder.build(market_context, portfolio_context, strategy_config)

            # Step 5: Call Claude API
            model = strategy_config.get("model") or DEFAULT_MODEL
            system_prompt = strategy_config.get("system_prompt", "")

            try:
                message_result = await asyncio.wait_for(
                    self._anthropic.messages.create(
                        model=model,
                        max_tokens=4096,
                        temperature=0.2,
                        system=system_prompt,
                        messages=[
                            {
                                "role": "user",
                                "content": raw_prompt,
                            }
                        ],
                    ),
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
                raw_response = message_result.content[0].text

                # Extract token usage
                if hasattr(message_result, "usage") and message_result.usage:
                    token_count_input = getattr(message_result.usage, "input_tokens", 0)
                    token_count_output = getattr(message_result.usage, "output_tokens", 0)

            except asyncio.TimeoutError:
                # Record timeout error
                await self._record_analysis_history(
                    strategy_config_id=strategy_config["id"],
                    completion_status="timeout",
                    latency_ms=int((time.monotonic() - start_time) * 1000),
                    token_count_input=token_count_input,
                    token_count_output=token_count_output,
                    raw_prompt=raw_prompt,
                    raw_response="",
                    parsed_proposals_count=0,
                    error_message="Claude API call timed out after 60 seconds",
                )
                logger.error("Claude API call timed out")
                return LLMProposalOutput(
                    proposals=[],
                    market_analysis_summary="",
                    confidence_explanation="",
                )

            except Exception as e:
                # Record API error
                error_type = "timeout" if "timeout" in str(e).lower() else "error"
                await self._record_analysis_history(
                    strategy_config_id=strategy_config["id"],
                    completion_status=error_type,
                    latency_ms=int((time.monotonic() - start_time) * 1000),
                    token_count_input=token_count_input,
                    token_count_output=token_count_output,
                    raw_prompt=raw_prompt,
                    raw_response="",
                    parsed_proposals_count=0,
                    error_message=str(e),
                )
                logger.error("Claude API call failed: %s", e)
                return LLMProposalOutput(
                    proposals=[],
                    market_analysis_summary="",
                    confidence_explanation="",
                )

            # Step 6: Compute latency
            latency_ms = int((time.monotonic() - start_time) * 1000)

            # Step 7: Parse response
            market_snapshot = self._build_market_snapshot(market_context)
            portfolio_snapshot = self._build_portfolio_snapshot(portfolio_context)
            technical_context = self._build_technical_context(market_context)

            try:
                proposals = self._parser.parse(
                    raw_response,
                    market_snapshot,
                    portfolio_snapshot,
                    technical_context,
                )
            except LLMParseError as e:
                # Record parsing error
                await self._record_analysis_history(
                    strategy_config_id=strategy_config["id"],
                    completion_status="error",
                    latency_ms=latency_ms,
                    token_count_input=token_count_input,
                    token_count_output=token_count_output,
                    raw_prompt=raw_prompt,
                    raw_response=raw_response,
                    parsed_proposals_count=0,
                    error_message=f"Parse error: {e}",
                )
                logger.error("Failed to parse LLM response: %s", e)
                return LLMProposalOutput(
                    proposals=[],
                    market_analysis_summary="",
                    confidence_explanation="",
                )

            # Extract summary info from response if available
            analysis_summary = ""
            confidence_explanation = ""
            try:
                response_json = json.loads(raw_response)
                analysis_summary = response_json.get("analysis_summary", "")
                confidence_explanation = response_json.get("confidence_explanation", "")
            except (json.JSONDecodeError, Exception):
                pass

            # Step 8: Record analysis history
            await self._record_analysis_history(
                strategy_config_id=strategy_config["id"],
                completion_status="success",
                latency_ms=latency_ms,
                token_count_input=token_count_input,
                token_count_output=token_count_output,
                raw_prompt=raw_prompt,
                raw_response=raw_response,
                parsed_proposals_count=len(proposals),
                error_message=None,
            )

            # Step 9: Enqueue proposals
            for proposal in proposals:
                await self._proposal_queue.enqueue(proposal)

            logger.info(
                "LLM analysis complete: %d proposals generated",
                len(proposals),
            )

            return LLMProposalOutput(
                proposals=proposals,
                market_analysis_summary=analysis_summary,
                confidence_explanation=confidence_explanation,
            )

        except Exception as e:
            # Catch-all for unexpected errors
            await self._record_analysis_history(
                strategy_config_id=strategy_config.get("id", 0),
                completion_status="error",
                latency_ms=int((time.monotonic() - start_time) * 1000),
                token_count_input=0,
                token_count_output=0,
                raw_prompt=raw_prompt,
                raw_response=raw_response,
                parsed_proposals_count=0,
                error_message=str(e),
            )
            logger.exception("Unexpected error in analyze_and_propose: %s", e)
            return LLMProposalOutput(
                proposals=[],
                market_analysis_summary="",
                confidence_explanation="",
            )

    async def _load_active_strategy_config(self) -> Optional[dict]:
        """Load the active (enabled=1) strategy config from database.

        Returns:
            Strategy config dict or None if not found.
        """
        cursor = await self._db.execute(
            """
            SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

    def _get_asset_whitelist_from_config(self, config: dict) -> list[str]:
        """Extract asset whitelist from a strategy config dict."""
        whitelist = config.get("asset_whitelist", "[]")
        if isinstance(whitelist, str):
            try:
                return json.loads(whitelist)
            except json.JSONDecodeError:
                return []
        return whitelist

    async def _gather_market_context(self, symbols: list[str]) -> MarketContext:
        """Concurrently gather all market data for the given symbols.

        Args:
            symbols: List of trading pair symbols.

        Returns:
            MarketContext with tickers, orderbooks, technical indicators, and trades.
        """
        tickers: list[dict] = []
        orderbooks: dict = {}
        technical_indicators: dict = {}
        recent_trades: dict = {}

        # Gather data for each symbol concurrently
        async def gather_for_symbol(symbol: str):
            # Fetch tickers from all exchanges
            symbol_tickers = await self._registry.get_all_tickers(symbol)
            ticker_dicts = [
                {
                    "symbol": t.symbol,
                    "price": str(t.price),
                    "volume_24h": str(t.volume_24h),
                    "bid": str(float(t.price) * 0.999) if t.price else "N/A",
                    "ask": str(float(t.price) * 1.001) if t.price else "N/A",
                    "exchange": t.exchange,
                }
                for t in symbol_tickers
            ]

            # Orderbook data no longer collected (not needed for this project)
            orderbook_data = None

            # Get recent trades from first enabled adapter
            enabled = self._registry.list_enabled()
            trades_data: list[dict] = []
            if enabled:
                try:
                    trades: list[dict] = await enabled[0].fetch_recent_trades(symbol, limit=20)
                    trades_data = trades
                except Exception as e:
                    logger.warning("Failed to fetch trades for %s: %s", symbol, e)

            # Compute technical indicators
            indicators = await self._indicator_calculator.compute_for_symbol(symbol)
            indicator_dict = {
                "rsi_14": indicators.rsi_14,
                "macd_line": indicators.macd_line,
                "macd_signal": indicators.macd_signal,
                "macd_histogram": indicators.macd_histogram,
                "ema_20": indicators.ema_20,
                "ema_50": indicators.ema_50,
                "bollinger_upper": indicators.bollinger_upper,
                "bollinger_lower": indicators.bollinger_lower,
                "atr_14": indicators.atr_14,
            }

            return symbol, ticker_dicts, orderbook_data, indicator_dict, trades_data

        # Execute gather tasks concurrently
        results = await asyncio.gather(
            *[gather_for_symbol(symbol) for symbol in symbols],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                logger.warning("Failed to gather data for symbol: %s", result)
                continue
            symbol, ticker_dicts, orderbook_data, indicator_dict, trades_data = result
            tickers.extend(ticker_dicts)
            if orderbook_data:
                orderbooks[symbol] = orderbook_data
            technical_indicators[symbol] = indicator_dict
            recent_trades[symbol] = trades_data

        return MarketContext(
            tickers=tickers,
            orderbooks=orderbooks,
            technical_indicators=technical_indicators,
            recent_trades=recent_trades,
        )

    async def _gather_portfolio_context(self) -> PortfolioContext:
        """Query latest portfolio snapshot and format as context.

        Returns:
            PortfolioContext with balances, total USDT value, and open positions.
        """
        # Query most recent portfolio snapshot
        cursor = await self._db.execute(
            """
            SELECT exchange, asset, free_balance, locked_balance, usdt_value, snapshot_time
            FROM portfolio_snapshots
            ORDER BY snapshot_time DESC
            LIMIT 100
            """
        )
        rows = await cursor.fetchall()

        # Group by exchange and asset
        balances_by_exchange: dict[str, list[dict]] = {}
        total_usdt_value = 0.0

        for row in rows:
            exchange = row["exchange"]
            asset = row["asset"]
            free = row["free_balance"]
            locked = row["locked_balance"]
            usdt_val = row["usdt_value"] or 0.0

            if exchange not in balances_by_exchange:
                balances_by_exchange[exchange] = []

            # Only add if not already present for this exchange/asset
            existing = [
                b for b in balances_by_exchange[exchange]
                if b.get("asset") == asset
            ]
            if not existing:
                balances_by_exchange[exchange].append({
                    "asset": asset,
                    "exchange": exchange,
                    "free": str(free),
                    "locked": str(locked),
                })
                total_usdt_value += usdt_val

        # Build balances list
        balances: list[dict] = []
        for exchange_balances in balances_by_exchange.values():
            balances.extend(exchange_balances)

        # Identify open positions (non-zero balances excluding USDT)
        open_positions: dict = {}
        for balance in balances:
            if balance["asset"] != "USDT":
                free = float(balance.get("free", "0"))
                locked = float(balance.get("locked", "0"))
                if free > 0 or locked > 0:
                    entry_price = await self._estimate_entry_price(
                        balance["asset"], balance.get("exchange", "")
                    )
                    open_positions[balance["asset"]] = {
                        "side": "long",
                        "volume": str(free + locked),
                        "entry_price": entry_price,
                    }

        return PortfolioContext(
            balances=balances,
            total_usdt_value=f"{total_usdt_value:.2f}",
            open_positions=open_positions,
        )

    async def _estimate_entry_price(self, asset: str, exchange: str) -> str:
        """Estimate average entry price from the most recent filled orders.

        Looks back at the 5 most recent filled orders for any symbol starting
        with the given asset. Returns the average fill price as a 2-decimal
        string, or "N/A" if no filled orders are found.
        """
        try:
            cursor = await self._db.execute(
                "SELECT AVG(price) FROM orders "
                "WHERE symbol LIKE ? AND exchange = ? AND status = 'filled' "
                "ORDER BY created_at DESC LIMIT 5",
                (f"{asset}%", exchange),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                return f"{row[0]:.2f}"
        except Exception as e:
            logger.warning("Failed to estimate entry price for %s: %s", asset, e)
        return "N/A"

    def _build_market_snapshot(self, market_context: MarketContext) -> dict:
        """Build market snapshot dict from market context."""
        return {
            "tickers": market_context.tickers,
            "orderbooks": market_context.orderbooks,
            "recent_trades": market_context.recent_trades,
        }

    def _build_portfolio_snapshot(self, portfolio_context: PortfolioContext) -> dict:
        """Build portfolio snapshot dict from portfolio context."""
        return {
            "balances": portfolio_context.balances,
            "total_usdt_value": portfolio_context.total_usdt_value,
            "open_positions": portfolio_context.open_positions,
        }

    def _build_technical_context(self, market_context: MarketContext) -> dict:
        """Build technical context dict from market context."""
        return market_context.technical_indicators

    async def _record_analysis_history(
        self,
        strategy_config_id: int,
        completion_status: str,
        latency_ms: int,
        token_count_input: int,
        token_count_output: int,
        raw_prompt: str,
        raw_response: str,
        parsed_proposals_count: int,
        error_message: Optional[str],
    ) -> None:
        """Record LLM analysis in the llm_analysis_history table.

        Args:
            strategy_config_id: ID of the strategy config used.
            completion_status: Status string (success/error/timeout).
            latency_ms: Latency in milliseconds.
            token_count_input: Number of input tokens.
            token_count_output: Number of output tokens.
            raw_prompt: The prompt sent to Claude.
            raw_response: The raw response from Claude.
            parsed_proposals_count: Number of proposals parsed.
            error_message: Error message if failed, None otherwise.
        """
        try:
            await self._db.execute(
                """
                INSERT INTO llm_analysis_history
                (strategy_config_id, completion_status, latency_ms,
                 token_count_input, token_count_output, raw_prompt, raw_response,
                 parsed_proposals_count, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_config_id,
                    completion_status,
                    latency_ms,
                    token_count_input,
                    token_count_output,
                    raw_prompt,
                    raw_response,
                    parsed_proposals_count,
                    error_message,
                ),
            )
            await self._db.commit()
        except Exception as e:
            logger.error("Failed to record analysis history: %s", e)