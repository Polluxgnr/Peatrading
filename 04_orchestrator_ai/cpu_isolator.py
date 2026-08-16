"""CPU-Bound Task Isolator using ProcessPoolExecutor for PEA Pollux.

Isolates heavy synchronous tasks (FinBERT transformer NLP tokenization/inference,
XGBoost cross-validation/training, Isolation Forest multi-factor anomaly scoring)
from the main asyncio event loop to prevent event loop starvation on Mini PC hardware.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("cpu_isolator")


class CpuTaskIsolator:
    """Singleton process pool manager for offloading CPU-intensive quantitative computations."""

    _instance: Optional[CpuTaskIsolator] = None

    def __new__(cls, max_workers: int = 2) -> CpuTaskIsolator:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_workers: int = 2) -> None:
        if getattr(self, "_initialized", False):
            return
        self.max_workers = max(1, int(max_workers))
        self._pool: Optional[concurrent.futures.ProcessPoolExecutor] = None
        self._initialized = True
        logger.info("CpuTaskIsolator initialized (max_workers=%d).", self.max_workers)

    @property
    def pool(self) -> concurrent.futures.ProcessPoolExecutor:
        """Lazily instantiate or return the active ProcessPoolExecutor."""
        if self._pool is None:
            self._pool = concurrent.futures.ProcessPoolExecutor(max_workers=self.max_workers)
        return self._pool

    async def run_in_process(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Offload a synchronous CPU-bound function to a separate OS process.

        Args:
            func: Target callable (must be picklable top-level function).
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            Any: The return value of func(*args, **kwargs).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

        partial_call = functools.partial(func, *args, **kwargs)
        try:
            return await loop.run_in_executor(self.pool, partial_call)
        except Exception as exc:
            logger.warning("ProcessPoolExecutor execution failed for %s (%s); falling back to thread/direct execution.", func.__name__, exc)
            # Fallback in case of pickling constraints or subprocess failure
            return func(*args, **kwargs)

    def shutdown(self, wait: bool = False) -> None:
        """Cleanly shutdown the underlying process pool."""
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
            self._pool = None
            logger.info("CpuTaskIsolator process pool shut down.")


# Global singleton instance
cpu_isolator = CpuTaskIsolator(max_workers=2)
