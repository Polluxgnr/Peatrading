"""Revocation Engine for PEA Sniper Terminal V-Prime.

Implements the Anti-Stale logic re-run at each daily pass (09:00, 13:30, 17:10):
a signal is REVOKED if the price drifts too far from the emission price, or
EXPIRED once it outlives its validity window.

Pure logical routing: no LLMs, no APIs. All paths use ``pathlib``.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_PRICE_DRIFT_LIMIT = 0.03  # 3% intraday drift revokes a signal.


class RevocationEngine:
    """Revokes or expires signals that are no longer actionable.

    Attributes:
        validity_hours: Number of hours a signal remains valid after emission.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Load the signal validity window from ``risk_params.yaml``.

        Args:
            config_dir: Path to the ``config`` directory. Defaults to
                ``<project_root>/config``.
        """
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        risk = self._load_yaml(config_path / "risk_params.yaml")
        self.validity_hours: float = float(risk["SIGNAL_VALIDITY_HOURS"])
        logger.debug("RevocationEngine loaded: validity=%.1fh", self.validity_hours)

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file into a dict, raising a clear error if missing."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def evaluate_signal(
        self, signal: Signal, current_price: float, original_price: float
    ) -> Signal:
        """Re-evaluate a signal for price drift and time decay.

        Args:
            signal: The signal to evaluate (mutated in place and returned).
            current_price: Latest market price for the ticker.
            original_price: Price at the moment the signal was emitted.

        Returns:
            Signal: The same signal object, with updated ``status``/``reason``.
        """
        # Rule 1 - Price drift (revocation takes precedence over expiry).
        if original_price and original_price > 0:
            drift = abs(current_price - original_price) / original_price
            if drift > _PRICE_DRIFT_LIMIT:
                signal.status = SignalStatus.REVOKED
                signal.reason = f"{signal.reason} | REVOKED: Price drifted > 3%".strip(" |")
                logger.info(
                    "Signal %s REVOKED: %s drifted %.2f%% (%.2f -> %.2f).",
                    signal.id[:8],
                    signal.ticker,
                    drift * 100,
                    original_price,
                    current_price,
                )
                return signal

        # Rule 2 - Time decay.
        now = datetime.now(timezone.utc)
        created = signal.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (now - created).total_seconds() / 3600.0
        if age_hours > self.validity_hours:
            signal.status = SignalStatus.EXPIRED
            signal.reason = f"{signal.reason} | EXPIRED: Older than validity window".strip(" |")
            logger.info(
                "Signal %s EXPIRED: age %.1fh > %.1fh.",
                signal.id[:8],
                age_hours,
                self.validity_hours,
            )
            return signal

        logger.debug("Signal %s still valid (age %.1fh).", signal.id[:8], age_hours)
        return signal


if __name__ == "__main__":
    from datetime import timedelta

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    engine = RevocationEngine()

    print("--- Rule 1: price drift ---")
    s1 = Signal(ticker="MC.PA", signal_type="BUY", score=80.0,
                reason="Mean-reversion setup")
    s1 = engine.evaluate_signal(s1, current_price=94.0, original_price=100.0)
    print(f"status={s1.status.value} | reason='{s1.reason}'")

    print("\n--- Rule 2: time decay ---")
    s2 = Signal(ticker="AI.PA", signal_type="BUY", score=90.0,
                reason="Mean-reversion setup")
    s2.created_at = datetime.now(timezone.utc) - timedelta(hours=13)
    s2 = engine.evaluate_signal(s2, current_price=100.5, original_price=100.0)
    print(f"status={s2.status.value} | reason='{s2.reason}'")

    print("\n--- Still valid ---")
    s3 = Signal(ticker="OR.PA", signal_type="BUY", score=75.0,
                reason="Mean-reversion setup")
    s3 = engine.evaluate_signal(s3, current_price=100.5, original_price=100.0)
    print(f"status={s3.status.value} | reason='{s3.reason}'")
