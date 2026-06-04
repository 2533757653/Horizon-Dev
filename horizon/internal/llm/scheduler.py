"""Periodic asyncio trigger for LLMStrategyEngine.

Replaces APScheduler with a minimal asyncio loop. Supports:
- start() / stop() lifecycle
- trigger_now() for manual "Analyze Now" button
- Lock serializes concurrent invocations
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class LLMScheduler:
    """Runs engine.analyze_and_propose() at a configurable interval."""

    def __init__(
        self,
        engine,
        interval_seconds: int = 8 * 3600,
        run_on_start: bool = False,
    ):
        self._engine = engine
        self._interval = interval_seconds
        self._run_on_start = run_on_start
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="LLMScheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    async def trigger_now(self):
        async with self._lock:
            logger.info("LLMScheduler.trigger_now invoked")
            return await self._engine.analyze_and_propose()

    async def _loop(self) -> None:
        if self._run_on_start:
            await self._run_once_safe()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval,
                )
                # Stop event fired — exit cleanly
                return
            except asyncio.TimeoutError:
                # Interval elapsed — run analysis
                await self._run_once_safe()

    async def _run_once_safe(self) -> None:
        async with self._lock:
            try:
                logger.info("LLMScheduler: periodic analysis starting")
                await self._engine.analyze_and_propose()
                logger.info("LLMScheduler: periodic analysis complete")
            except Exception as e:
                logger.exception("LLMScheduler analysis failed: %s", e)
