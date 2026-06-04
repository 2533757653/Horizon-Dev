"""CoPilotEngine — multi-turn human-initiated trading dialogue."""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .copilot_prompt import COPILOT_SYSTEM_PROMPT, build_context_block

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 100_000
MAX_HISTORY_MESSAGES = 20
DEFAULT_MAX_TOKENS = 2048


@dataclass
class CoPilotSession:
    id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime
    message_count: int


@dataclass
class CoPilotMessage:
    id: str
    session_id: str
    role: str  # user|assistant|system
    content: str
    trade_suggestion: Optional[dict]
    context_summary: Optional[dict]
    token_count_input: Optional[int]
    token_count_output: Optional[int]
    latency_ms: Optional[int]
    created_at: datetime


@dataclass
class CoPilotReply:
    message_id: str
    assistant_text: str
    trade_suggestion: Optional[dict]
    context_summary: dict
    token_count_input: int
    token_count_output: int
    latency_ms: int


class CoPilotEngine:
    def __init__(
        self,
        db: aiosqlite.Connection,
        anthropic_client,
        registry,
        fetcher,
        indicator_calculator,
        portfolio_tracker,
        model_name: str,
    ):
        self._db = db
        self._client = anthropic_client
        self._registry = registry
        self._fetcher = fetcher
        self._indicators = indicator_calculator
        self._portfolio_tracker = portfolio_tracker
        self._model = model_name

    async def create_session(self, title: Optional[str] = None) -> CoPilotSession:
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        await self._db.execute(
            "INSERT INTO copilot_sessions (id, title, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (sid, title, now, now),
        )
        await self._db.commit()
        return CoPilotSession(id=sid, title=title, created_at=now, updated_at=now, message_count=0)

    async def list_sessions(self, limit: int = 20) -> list[CoPilotSession]:
        cursor = await self._db.execute(
            "SELECT id, title, created_at, updated_at, message_count "
            "FROM copilot_sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            CoPilotSession(
                id=r["id"], title=r["title"],
                created_at=r["created_at"], updated_at=r["updated_at"],
                message_count=r["message_count"],
            )
            for r in rows
        ]

    async def delete_session(self, session_id: str) -> None:
        await self._db.execute("DELETE FROM copilot_messages WHERE session_id = ?", (session_id,))
        await self._db.execute("DELETE FROM copilot_sessions WHERE id = ?", (session_id,))
        await self._db.commit()

    async def get_messages(self, session_id: str) -> list[CoPilotMessage]:
        cursor = await self._db.execute(
            "SELECT * FROM copilot_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(r) for r in rows]

    async def send_message(self, session_id: str, user_text: str) -> CoPilotReply:
        # 1. Persist user message
        user_msg_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        await self._db.execute(
            "INSERT INTO copilot_messages (id, session_id, role, content, created_at) "
            "VALUES (?, ?, 'user', ?, ?)",
            (user_msg_id, session_id, user_text, now),
        )

        # 2. Gather context
        prices, indicators, portfolio = await self._gather_context()
        context_block = build_context_block(prices, indicators, portfolio)
        from .copilot_prompt import _portfolio_balances
        context_summary = {
            "prices_fed": [p.get("symbol") for p in prices][:8],
            "indicators_fed": list(indicators.keys())[:6],
            "positions_fed": bool(_portfolio_balances(portfolio)),
        }

        # 3. Load history (this session only)
        history_rows = await self._db.execute(
            "SELECT role, content FROM copilot_messages "
            "WHERE session_id = ? AND id != ? ORDER BY created_at ASC",
            (session_id, user_msg_id),
        )
        history = await history_rows.fetchall()
        history = list(history[-MAX_HISTORY_MESSAGES:])

        # 4. Build messages array
        messages = [{"role": r["role"], "content": r["content"]} for r in history]
        messages.append({
            "role": "user",
            "content": f"{context_block}\n\n### User\n{user_text}",
        })
        context_summary["history_messages"] = len(history)

        # 5. Trim if total prompt exceeds limit
        while sum(len(m["content"]) for m in messages) > MAX_PROMPT_CHARS and len(messages) > 1:
            messages.pop(0)

        # 6. Call LLM
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    temperature=0.4,
                    system=COPILOT_SYSTEM_PROMPT,
                    messages=messages,
                ),
                timeout=60,
            )
        except Exception as e:
            logger.exception("CoPilot LLM call failed: %s", e)
            await self._db.commit()
            raise

        latency_ms = int((time.monotonic() - t0) * 1000)
        # Anthropic may return interleaved ThinkingBlock + TextBlock. Concatenate all
        # text blocks to recover the model reply even when thinking is enabled.
        raw = "".join(
            getattr(block, "text", "")
            for block in result.content
            if getattr(block, "type", "text") == "text"
        )
        in_tok = getattr(result.usage, "input_tokens", 0) if hasattr(result, "usage") else 0
        out_tok = getattr(result.usage, "output_tokens", 0) if hasattr(result, "usage") else 0

        # 7. Parse JSON response
        assistant_text, trade_suggestion = self._parse_reply(raw)

        # 8. Persist assistant message
        asst_id = str(uuid.uuid4())
        await self._db.execute(
            "INSERT INTO copilot_messages "
            "(id, session_id, role, content, trade_suggestion, context_summary, "
            " token_count_input, token_count_output, latency_ms, created_at) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?)",
            (asst_id, session_id, assistant_text,
             json.dumps(trade_suggestion) if trade_suggestion else None,
             json.dumps(context_summary),
             in_tok, out_tok, latency_ms,
             datetime.now(timezone.utc)),
        )
        # Update session count + updated_at
        await self._db.execute(
            "UPDATE copilot_sessions SET message_count = message_count + 2, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc), session_id),
        )
        await self._db.commit()

        return CoPilotReply(
            message_id=asst_id,
            assistant_text=assistant_text,
            trade_suggestion=trade_suggestion,
            context_summary=context_summary,
            token_count_input=in_tok,
            token_count_output=out_tok,
            latency_ms=latency_ms,
        )

    async def _gather_context(self):
        prices: list[dict] = []
        indicators: dict = {}
        portfolio: dict = {}
        try:
            for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
                tks = await self._registry.get_all_tickers(sym)
                for t in tks:
                    prices.append({"symbol": t.symbol, "price": str(t.price), "exchange": t.exchange})
                try:
                    ind = await self._indicators.compute_for_symbol(sym)
                    indicators[sym] = {
                        "rsi_14": ind.rsi_14, "macd_line": ind.macd_line,
                        "macd_signal": ind.macd_signal, "ema_20": ind.ema_20, "ema_50": ind.ema_50,
                    }
                except Exception:
                    pass
        except Exception as e:
            logger.warning("CoPilot context gather (market) failed: %s", e)
        try:
            if hasattr(self._portfolio_tracker, "get_snapshot"):
                portfolio = await self._portfolio_tracker.get_snapshot()
        except Exception as e:
            logger.warning("CoPilot context gather (portfolio) failed: %s", e)
        return prices, indicators, portfolio

    @staticmethod
    def _parse_reply(raw: str) -> tuple[str, Optional[dict]]:
        try:
            data = json.loads(raw.strip())
            text = data.get("assistant_text", "").strip()
            sug = data.get("trade_suggestion")
            if isinstance(sug, dict):
                return text, sug
            return text, None
        except Exception:
            # Tolerate non-JSON: return raw as assistant_text, no suggestion
            return raw.strip(), None

    @staticmethod
    def _row_to_message(r) -> CoPilotMessage:
        return CoPilotMessage(
            id=r["id"], session_id=r["session_id"], role=r["role"],
            content=r["content"],
            trade_suggestion=json.loads(r["trade_suggestion"]) if r["trade_suggestion"] else None,
            context_summary=json.loads(r["context_summary"]) if r["context_summary"] else None,
            token_count_input=r["token_count_input"],
            token_count_output=r["token_count_output"],
            latency_ms=r["latency_ms"],
            created_at=r["created_at"],
        )
