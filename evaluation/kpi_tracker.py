"""
evaluation/kpi_tracker.py
──────────────────────────
KPI (Key Performance Indicator) tracker for negotiation experiments.

Tracks all metrics relevant to the bachelor thesis evaluation:
  - Runtime
  - Rounds used
  - Agreement outcome
  - Final margin achieved
  - Final price gap
  - Constraint violations
  - Token usage (when available from LLM response metadata)

The KPI dict returned by ``get_kpis()`` is the primary output consumed
by ``run_experiment()``.  It is designed to be:
  - JSON-serialisable (all native Python types)
  - Flat (one level deep) for easy export to CSV / pandas DataFrame
  - Extensible (add new fields without breaking existing callers)

Usage
-----
>>> tracker = KPITracker()
>>> tracker.start()
>>> # … run experiment …
>>> tracker.stop(final_state, constraints)
>>> kpis = tracker.get_kpis()
>>> print(kpis)
"""

import logging
import time
from typing import Optional

from models.constraints import ConstraintModel
from models.negotiation_models import AgentRole, NegotiationState

logger = logging.getLogger(__name__)


class KPITracker:
    """
    Lightweight KPI accumulator for a single negotiation experiment.

    Thread-safety: not thread-safe by design.  Use one instance per
    experiment run.
    """

    def __init__(self) -> None:
        self._start_time:   Optional[float] = None
        self._end_time:     Optional[float] = None
        self._violations:   int = 0
        self._tokens_used:  int = 0
        self._final_state:  Optional[NegotiationState] = None
        self._constraints:  Optional[ConstraintModel] = None
        self._experiment_id: str = "unknown"

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────

    def start(self, experiment_id: str = "unknown") -> None:
        """
        Start the experiment timer and reset all counters.

        Call this once, immediately before the orchestrator's ``run()``.
        """
        self._start_time    = time.perf_counter()
        self._end_time      = None
        self._violations    = 0
        self._tokens_used   = 0
        self._final_state   = None
        self._constraints   = None
        self._experiment_id = experiment_id
        logger.debug("KPITracker started for experiment '%s'", experiment_id)

    def stop(
        self,
        final_state: NegotiationState,
        constraints: ConstraintModel,
    ) -> None:
        """
        Stop the timer and snapshot the final state.

        Call this once, immediately after the orchestrator finishes.
        """
        self._end_time    = time.perf_counter()
        self._final_state = final_state
        self._constraints = constraints
        logger.debug(
            "KPITracker stopped | runtime=%.3fs | violations=%d",
            self.runtime_seconds,
            self._violations,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # INCREMENTAL COUNTERS (called by orchestrator during the run)
    # ─────────────────────────────────────────────────────────────────────────

    def record_violation(self) -> None:
        """Increment constraint violation counter by one."""
        self._violations += 1

    def record_tokens(self, count: int) -> None:
        """
        Add ``count`` tokens to the running total.

        Call this whenever LLM response metadata is available.
        If the LLM backend does not expose token counts, the total
        will remain 0 (still valid – the field is documented as
        'if available').
        """
        if count > 0:
            self._tokens_used += count

    # ─────────────────────────────────────────────────────────────────────────
    # KPI OUTPUT
    # ─────────────────────────────────────────────────────────────────────────

    def get_kpis(self) -> dict:
        """
        Return all KPIs as a flat, JSON-serialisable dictionary.

        Keys
        ----
        experiment_id             : Identifier from the config.
        total_rounds              : Number of rounds executed.
        agreement_reached         : True / False.
        termination_reason        : Human-readable stop condition.
        final_supplier_price      : Last valid supplier unit price (EUR).
        final_retail_offer        : Last valid retail unit price (EUR).
        final_price_gap           : |supplier_price − retail_offer| (EUR).
        final_margin              : Gross margin at the final supplier price.
        final_margin_pct          : Same, formatted as percentage string.
        constraint_violations_count : Cumulative violations across all rounds.
        total_tokens_used         : Token usage if available, else 0.
        runtime_seconds           : Wall-clock time for the full run.

        Raises
        ------
        RuntimeError
            If ``stop()`` has not been called yet.
        """
        if self._final_state is None or self._constraints is None:
            raise RuntimeError(
                "KPITracker.stop() must be called before get_kpis()."
            )

        state = self._final_state
        constraints = self._constraints

        # ── Price data ────────────────────────────────────────────────────────
        last_supplier = state.get_last_offer_by_role(AgentRole.SUPPLIER)
        last_retail   = state.get_last_offer_by_role(AgentRole.RETAIL)

        final_supplier_price: Optional[float] = (
            last_supplier.unit_price if last_supplier else None
        )
        final_retail_offer: Optional[float] = (
            last_retail.unit_price if last_retail else None
        )

        # ── Price gap ─────────────────────────────────────────────────────────
        final_price_gap: Optional[float] = state.get_price_gap()

        # ── Margin at final supplier price ────────────────────────────────────
        final_margin: Optional[float] = None
        final_margin_pct: Optional[str] = None
        if final_supplier_price is not None:
            final_margin = constraints.calculate_margin(final_supplier_price)
            final_margin_pct = f"{final_margin:.2%}"

        kpis = {
            # Identification
            "experiment_id":               self._experiment_id,
            # Rounds
            "total_rounds":                state.current_round,
            # Outcome
            "agreement_reached":           state.is_agreement,
            "termination_reason":          state.termination_reason,
            # Prices
            "final_supplier_price":        (
                round(final_supplier_price, 4)
                if final_supplier_price is not None else None
            ),
            "final_retail_offer":          (
                round(final_retail_offer, 4)
                if final_retail_offer is not None else None
            ),
            "final_price_gap":             (
                round(final_price_gap, 4)
                if final_price_gap is not None else None
            ),
            # Margin
            "final_margin":                final_margin,
            "final_margin_pct":            final_margin_pct,
            # Constraint compliance
            "constraint_violations_count": self._violations,
            # Token usage
            "total_tokens_used":           self._tokens_used,
            # Runtime
            "runtime_seconds":             round(self.runtime_seconds, 3),
        }

        logger.info("KPIs: %s", kpis)
        return kpis

    # ─────────────────────────────────────────────────────────────────────────
    # PROPERTIES
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def runtime_seconds(self) -> float:
        """Elapsed wall-clock time in seconds.  0.0 if not yet stopped."""
        if self._start_time is None:
            return 0.0
        end = self._end_time if self._end_time is not None else time.perf_counter()
        return end - self._start_time

    @property
    def violations(self) -> int:
        """Current constraint violation count."""
        return self._violations
