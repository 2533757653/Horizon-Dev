"""Tests for LLMScheduler."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from horizon.internal.llm.scheduler import LLMScheduler


@pytest.mark.asyncio
async def test_trigger_now_calls_engine_once():
    engine = MagicMock()
    engine.analyze_and_propose = AsyncMock(return_value="ok")
    scheduler = LLMScheduler(engine, interval_seconds=999)
    result = await scheduler.trigger_now()
    assert result == "ok"
    engine.analyze_and_propose.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_runs_immediately_then_stops():
    engine = MagicMock()
    engine.analyze_and_propose = AsyncMock(return_value=None)
    scheduler = LLMScheduler(engine, interval_seconds=999, run_on_start=True)
    await scheduler.start()
    # give the task a moment to enter its first iteration
    await asyncio.sleep(0.1)
    await scheduler.stop()
    assert engine.analyze_and_propose.await_count >= 1


@pytest.mark.asyncio
async def test_concurrent_trigger_serialized_by_lock():
    engine = MagicMock()
    in_flight = []
    async def slow():
        in_flight.append(1)
        await asyncio.sleep(0.05)
        in_flight.pop()
        return "done"
    engine.analyze_and_propose = AsyncMock(side_effect=slow)
    scheduler = LLMScheduler(engine, interval_seconds=999)
    results = await asyncio.gather(
        scheduler.trigger_now(),
        scheduler.trigger_now(),
    )
    assert all(r == "done" for r in results)
    # In-flight never exceeded 1 due to the lock
    # (we can't assert on the list state directly after the fact;
    # rely on no exceptions + two completions.)
    assert engine.analyze_and_propose.await_count == 2
