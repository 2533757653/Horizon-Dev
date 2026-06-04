"""LLM Prompt Builder for Horizon Trading Platform.

Constructs structured prompts for LLM market analysis based on current market data,
portfolio state, and strategy configuration.
"""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MarketContext:
    """Market context data for prompt construction.

    Attributes:
        tickers: List of ticker dictionaries with symbol, price, volume_24h, bid, ask.
        orderbooks: Dictionary of symbol -> orderbook data.
        technical_indicators: Dictionary of symbol -> technical indicator values.
        recent_trades: Dictionary of symbol -> recent trade data.
    """

    tickers: list[dict] = field(default_factory=list)
    orderbooks: dict = field(default_factory=dict)
    technical_indicators: dict = field(default_factory=dict)
    recent_trades: dict = field(default_factory=dict)


@dataclass
class PortfolioContext:
    """Portfolio context data for prompt construction.

    Attributes:
        balances: List of balance dictionaries with asset, free, locked, exchange.
        total_usdt_value: Total portfolio value in USDT as string.
        open_positions: Dictionary of symbol -> position data.
    """

    balances: list[dict] = field(default_factory=list)
    total_usdt_value: str = "0"
    open_positions: dict = field(default_factory=dict)


@dataclass
class PromptBuilder:
    """Builds structured prompts for LLM market analysis.

    The PromptBuilder constructs deterministic prompts containing market data,
    portfolio state, and strategy parameters for generating trade recommendations.
    """

    def __init__(self, strategy_config: dict):
        """Initialize PromptBuilder with strategy configuration.

        Args:
            strategy_config: A row from the strategy_configs table containing
                parameters like asset_whitelist, max_position_pct, max_daily_loss_pct,
                min_confidence_threshold, max_risk_tier, etc.
        """
        self.strategy_config = strategy_config

    def build(
        self,
        market_context: MarketContext,
        portfolio_context: PortfolioContext,
        strategy_config: dict,
    ) -> str:
        """Build a complete prompt for LLM market analysis.

        Args:
            market_context: Current market data including tickers, orderbooks,
                technical indicators, and recent trades.
            portfolio_context: Current portfolio state including balances,
                total USDT value, and open positions.
            strategy_config: Strategy configuration row (same as constructor).

        Returns:
            A complete prompt string with all required sections.
        """
        sections = []

        # Section 1: System persona
        sections.append(self._build_system_persona())

        # Section 2: Current market snapshot
        sections.append(self._build_market_snapshot(market_context))

        # Section 3: Portfolio state
        sections.append(self._build_portfolio_state(portfolio_context))

        # Section 4: Strategy parameters
        sections.append(self._build_strategy_parameters(strategy_config))

        # Section 5: Instructions
        sections.append(self._build_instructions())

        # Section 6: Response format
        sections.append(self._build_response_format())

        # Section 7: Constraints reminder
        sections.append(self._build_constraints(strategy_config))

        return "\n\n".join(sections)

    def _build_system_persona(self) -> str:
        """Build system persona section."""
        return """You are Horizon, a quantitative trading analyst specializing in long-term crypto asset positioning. Your analysis horizon is 8 hours. You make disciplined, data-driven trade recommendations."""

    def _build_market_snapshot(self, market_context: MarketContext) -> str:
        """Build current market snapshot section with tickers and technical indicators."""
        lines = ["## Current Market Snapshot\n"]

        # Ticker information
        if market_context.tickers:
            lines.append("### Tickers\n")
            lines.append("| Symbol | Price | 24h Volume | Bid-Ask Spread |")
            lines.append("|--------|-------|-----------|----------------|")
            for ticker in market_context.tickers:
                symbol = ticker.get("symbol", "N/A")
                price = ticker.get("price", "N/A")
                volume = ticker.get("volume_24h", ticker.get("volume", "N/A"))
                bid = ticker.get("bid", "N/A")
                ask = ticker.get("ask", "N/A")
                if bid != "N/A" and ask != "N/A":
                    try:
                        spread = float(ask) - float(bid)
                        spread_str = f"{spread:.2f}"
                    except (ValueError, TypeError):
                        spread_str = "N/A"
                else:
                    spread_str = "N/A"
                lines.append(f"| {symbol} | {price} | {volume} | {spread_str} |")
            lines.append("")

        # Technical indicators
        if market_context.technical_indicators:
            lines.append("### Technical Indicators\n")
            lines.append("| Symbol | RSI(14) | MACD Line | MACD Signal | MACD Histogram | EMA(20) | EMA(50) | Bollinger Upper | Bollinger Lower | ATR(14) |")
            lines.append("|--------|---------|-----------|-------------|--------------|---------|---------|----------------|---------------|--------|")
            for symbol, indicators in market_context.technical_indicators.items():
                rsi = self._format_value(indicators.get("rsi_14"))
                macd_line = self._format_value(indicators.get("macd_line"))
                macd_signal = self._format_value(indicators.get("macd_signal"))
                macd_hist = self._format_value(indicators.get("macd_histogram"))
                ema_20 = self._format_value(indicators.get("ema_20"))
                ema_50 = self._format_value(indicators.get("ema_50"))
                bb_upper = self._format_value(indicators.get("bollinger_upper"))
                bb_lower = self._format_value(indicators.get("bollinger_lower"))
                atr = self._format_value(indicators.get("atr_14"))
                lines.append(
                    f"| {symbol} | {rsi} | {macd_line} | {macd_signal} | {macd_hist} | {ema_20} | {ema_50} | {bb_upper} | {bb_lower} | {atr} |"
                )
            lines.append("")

        return "\n".join(lines)

    def _build_portfolio_state(self, portfolio_context: PortfolioContext) -> str:
        """Build portfolio state section."""
        lines = ["## Portfolio State\n"]

        # Total USDT value
        lines.append(f"**Total USDT Value:** {portfolio_context.total_usdt_value}\n")

        # Holdings
        if portfolio_context.balances:
            lines.append("### Holdings\n")
            lines.append("| Asset | Exchange | Free | Locked |")
            lines.append("|-------|----------|------|--------|")
            for balance in portfolio_context.balances:
                asset = balance.get("asset", "N/A")
                exchange = balance.get("exchange", "N/A")
                free = balance.get("free", "0")
                locked = balance.get("locked", "0")
                lines.append(f"| {asset} | {exchange} | {free} | {locked} |")
            lines.append("")

        # Open positions
        if portfolio_context.open_positions:
            lines.append("### Open Positions\n")
            lines.append("| Symbol | Side | Volume | Entry Price |")
            lines.append("|--------|------|--------|-------------|")
            for symbol, position in portfolio_context.open_positions.items():
                side = position.get("side", "N/A")
                volume = position.get("volume", position.get("size", "N/A"))
                entry_price = position.get("entry_price", "N/A")
                lines.append(f"| {symbol} | {side} | {volume} | {entry_price} |")
            lines.append("")

        return "\n".join(lines)

    def _build_strategy_parameters(self, strategy_config: dict) -> str:
        """Build strategy parameters section."""
        lines = ["## Strategy Parameters\n"]

        # Asset whitelist
        whitelist = strategy_config.get("asset_whitelist", "[]")
        if isinstance(whitelist, str):
            try:
                whitelist = json.loads(whitelist)
            except json.JSONDecodeError:
                whitelist = []
        lines.append(f"- **Asset Whitelist:** {', '.join(whitelist) if whitelist else 'None'}")

        # Max position percentage
        max_position_pct = strategy_config.get("max_position_pct", 20.0)
        lines.append(f"- **Max Position %:** {max_position_pct}%")

        # Max daily loss percentage
        max_daily_loss = strategy_config.get("max_daily_loss_pct", 5.0)
        lines.append(f"- **Max Daily Loss %:** {max_daily_loss}%")

        # Confidence threshold (noted as not active in Step 2)
        confidence_threshold = strategy_config.get("min_confidence_threshold", 75)
        lines.append(f"- **Confidence Threshold:** {confidence_threshold}% (noted but not active in Step 2)")

        # Max risk tier allowed
        max_risk_tier = strategy_config.get("max_risk_tier", "low")
        lines.append(f"- **Max Risk Tier Allowed:** {max_risk_tier}")

        lines.append("")
        return "\n".join(lines)

    def _build_instructions(self) -> str:
        """Build instructions section."""
        return """## Instructions

- Analyze the provided data and decide if any trades are recommended.
- You may recommend zero trades if market conditions do not favor entry.
- For each recommended trade, provide: exchange, symbol, side, order_type, price (for limit), volume, confidence_score (0-100), risk_tier (low/medium/high), action_type (open|close|reduce), and a detailed rationale.
- Consider portfolio concentration — do not recommend assets already at max position size.
- Consider technical indicator confluence — trades with multiple confirming indicators should have higher confidence.
- You may also recommend modifying existing positions:
  - action_type = "close": fully exit a position you currently hold
  - action_type = "reduce": partially exit a position (volume = amount to exit)
  - action_type = "open": new entry (default if omitted)
  For close/reduce, the `side` field is the closing side (opposite of held direction).
  Only propose close/reduce for symbols listed under "Current open positions"."""

    def _build_response_format(self) -> str:
        """Build response format section with exact JSON schema."""
        return """## Response Format (MANDATORY)

You MUST respond with valid JSON and NOTHING ELSE. No markdown, no explanations outside the JSON.

Schema:
{
  "analysis_summary": "string - 2-3 sentence market overview",
  "confidence_explanation": "string - explain your overall confidence in current market conditions",
  "proposals": [
    {
      "exchange": "binance|htx|hyperliquid|bitget",
      "symbol": "BTCUSDT",
      "side": "buy|sell",
      "order_type": "market|limit",
      "price": "12345.67",
      "volume": "0.5",
      "action_type": "open|close|reduce",
      "confidence_score": 85,
      "risk_tier": "low|medium|high",
      "rationale": "Detailed explanation of the trade thesis"
    }
  ]
}"""

    def _build_constraints(self, strategy_config: dict) -> str:
        """Build constraints reminder section."""
        whitelist = strategy_config.get("asset_whitelist", "[]")
        if isinstance(whitelist, str):
            try:
                whitelist = json.loads(whitelist)
            except json.JSONDecodeError:
                whitelist = []
        whitelist_str = ", ".join(whitelist) if whitelist else "none"

        return f"""## Constraints Reminder

- Only trade symbols in the whitelist: {whitelist_str}
- Confidence score must be honest — do not inflate scores.
- Risk tier must reflect actual risk, not desired risk."""

    def _format_value(self, value: Any) -> str:
        """Format a numeric value for display in tables.

        Args:
            value: The value to format.

        Returns:
            String representation of the value, or "N/A" if None or invalid.
        """
        if value is None:
            return "N/A"
        try:
            num = float(value)
            if abs(num) >= 10000:
                return f"{num:,.2f}"
            elif abs(num) >= 1:
                return f"{num:.2f}"
            else:
                return f"{num:.4f}"
        except (ValueError, TypeError):
            return "N/A"