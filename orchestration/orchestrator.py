"""
orchestration/orchestrator.py
──────────────────────────────
Central negotiation orchestrator.

IMPORTANT: This module contains ZERO LLM calls.

The orchestrator is a pure control-flow engine.  It manages the
negotiation loop, alternates agent turns, delegates validation, checks
stop conditions, and accumulates the run-time state.  No decision is
made by an LLM here.

Negotiation flow
----------------
Round 1  : Supplier opens with an initial offer.
Round 2  : Retail responds with a counter-offer.
Round 3  : Supplier counter-offers.
…  (alternating)
Last round: Whichever agent would go triggers the "max rounds" stop.

Stop conditions (checked in order after EACH round)
----------------------------------------------------
1. Agreement reached   – |last_supplier_price − last_retail_price| ≤ threshold
2. Constraint deadlock – supplier floor > retail max acceptable price
                         (detected once before the loop via pre-check)
3. Max rounds reached  – current_round == max_rounds

Logging events
--------------
- Experiment start / end
- Each round: role, offer, validation result
- Constraint violations with full error message
- Agreement check result and price gap
- Final termination reason

Extensibility hooks
-------------------
- ``_on_round_complete()`` : override to add custom round-end logic
                             (e.g., memory module, external logging).
- ``_on_experiment_end()`` : override for post-run teardown.
"""

import logging
from typing import Optional

from agents.base_agent import BaseAgent
from agents.retail_agent import RetailAgent
from agents.supplier_agent import SupplierAgent
from config.settings import NegotiationConfig
from evaluation.kpi_tracker import KPITracker
from models.constraints import ConstraintModel
from models.negotiation_models import AgentRole, NegotiationOffer, NegotiationState, RoundRecord
from validation.validator import ValidationResult, Validator

logger = logging.getLogger(__name__)


class NegotiationOrchestrator:
    """
    Rule-based negotiation orchestrator.

    Coordinates supplier and retail agents over multiple rounds,
    validates every offer, checks stop conditions, logs events,
    and returns the final ``NegotiationState`` with attached KPIs.

    Parameters
    ----------
    supplier_agent : Configured ``SupplierAgent`` instance.
    retail_agent   : Configured ``RetailAgent`` instance.
    constraints    : Immutable ``ConstraintModel`` for this experiment.
    config         : ``NegotiationConfig`` (max_rounds, threshold, …).
    """

    def __init__(
        self,
        supplier_agent: SupplierAgent,
        retail_agent: RetailAgent,
        constraints: ConstraintModel,
        config: NegotiationConfig,
    ) -> None:
        self.supplier      = supplier_agent
        self.retail        = retail_agent
        self.constraints   = constraints
        self.config        = config
        self.validator     = Validator(constraints)
        self.kpi_tracker   = KPITracker()

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def run(self) -> NegotiationState:
        """
        Execute the full negotiation and return the final state.

        Returns
        -------
        NegotiationState
            Complete history, agreement flag, and termination reason.
        """
        self.kpi_tracker.start()
        state = NegotiationState()

        logger.info(
            "═══ Experiment '%s' started ═══ "
            "max_rounds=%d  threshold=±%.2f EUR  constraints=%s",
            self.config.experiment_id,
            self.config.max_rounds,
            self.config.agreement_threshold,
            self.constraints,
        )

        # ── Pre-flight: verify the constraint configuration is feasible ───────
        feasibility = self.validator.validate_feasibility()
        if not feasibility:
            state.termination_reason = feasibility.error_message
            logger.error("PRE-FLIGHT FAILED: %s", feasibility.error_message)
            self.kpi_tracker.stop(state, self.constraints)
            return state

        logger.info(
            "Agreement zone: %s", self.constraints.agreement_zone_str()
        )

        # ── Turn order: Supplier always opens ─────────────────────────────────
        # Round 1 → Supplier
        # Round 2 → Retail
        # Round 3 → Supplier
        # …
        turn_order = [
            (AgentRole.SUPPLIER, self.supplier),
            (AgentRole.RETAIL,   self.retail),
        ]

        for round_num in range(1, self.config.max_rounds + 1):
            state.current_round = round_num
            role, agent = turn_order[(round_num - 1) % 2]

            logger.info(
                "─── Round %d/%d  [%s] ───────────────────────────────",
                round_num,
                self.config.max_rounds,
                role.value.upper(),
            )

            # ── Generate offer ─────────────────────────────────────────────
            try:
                offer = agent.generate_offer(state, self.constraints)
            except Exception as exc:
                termination_msg = (
                    f"Agent '{agent.name}' failed to generate a valid offer "
                    f"in round {round_num}: {exc}"
                )
                logger.error(termination_msg)
                state.termination_reason = termination_msg
                break

            # ── Validate offer ─────────────────────────────────────────────
            validation: ValidationResult = self.validator.validate(offer, role.value)

            self._log_round(round_num, role, offer, validation)

            if not validation:
                self.kpi_tracker.record_violation()

            # ── Record in state ────────────────────────────────────────────
            record = RoundRecord(
                round_number=round_num,
                role=role,
                offer=offer,
                is_valid=validation.is_valid,
                validation_message=validation.error_message,
            )
            state.history.append(record)

            # ── Extensibility hook ─────────────────────────────────────────
            self._on_round_complete(round_num, role, offer, validation, state)

            # ── Stop condition 1: agreement ────────────────────────────────
            # Only check after both sides have made at least one valid offer
            if self._check_agreement(state):
                state.is_agreement = True
                state.termination_reason = "Agreement reached."
                logger.info(
                    "✓ AGREEMENT REACHED at round %d | gap=%.4f EUR ≤ threshold=%.4f EUR",
                    round_num,
                    state.get_price_gap() or 0.0,
                    self.config.agreement_threshold,
                )
                break

            # ── Stop condition 2: runtime deadlock ─────────────────────────
            # Re-check in case constraint violations shifted the effective range
            if self._check_deadlock(state):
                state.termination_reason = (
                    "Constraint deadlock: no feasible agreement zone remains "
                    "given the current offers and constraints."
                )
                logger.warning("✗ DEADLOCK: %s", state.termination_reason)
                break

        else:
            # Loop exhausted without break → max rounds
            if not state.termination_reason:
                state.termination_reason = (
                    f"Maximum rounds ({self.config.max_rounds}) reached "
                    f"without agreement."
                )
                logger.info("✗ MAX ROUNDS: %s", state.termination_reason)

        # ── Finalise ───────────────────────────────────────────────────────────
        self.kpi_tracker.stop(state, self.constraints)
        self._on_experiment_end(state)

        logger.info(
            "═══ Experiment '%s' complete ═══  %s",
            self.config.experiment_id,
            state.to_summary_dict(),
        )
        return state

    # ─────────────────────────────────────────────────────────────────────────
    # STOP CONDITION CHECKS
    # ─────────────────────────────────────────────────────────────────────────

    def _check_agreement(self, state: NegotiationState) -> bool:
        """
        Return True if the price gap between the last valid offers from each
        side is within the configured agreement threshold.

        Both sides must have made at least one valid offer.
        """
        last_supplier = state.get_last_offer_by_role(AgentRole.SUPPLIER)
        last_retail   = state.get_last_offer_by_role(AgentRole.RETAIL)

        if last_supplier is None or last_retail is None:
            return False

        gap = abs(last_supplier.unit_price - last_retail.unit_price)
        threshold = self.config.agreement_threshold

        logger.debug(
            "Agreement check: gap=%.4f EUR  threshold=%.4f EUR  agreed=%s",
            gap,
            threshold,
            gap <= threshold,
        )
        return gap <= threshold

    def _check_deadlock(self, state: NegotiationState) -> bool:
        """
        Return True if the last valid offers from each side are irreconcilable.

        Deadlock = the supplier's last price is above the retailer's last price
        AND the supplier's last price equals the floor price (no room to move).

        This avoids false-positive deadlock detection in early rounds when
        the gap is large but both agents are still converging.
        """
        last_supplier = state.get_last_offer_by_role(AgentRole.SUPPLIER)
        last_retail   = state.get_last_offer_by_role(AgentRole.RETAIL)

        if last_supplier is None or last_retail is None:
            return False

        # Both at their hard limits and still not overlapping?
        at_floor    = last_supplier.unit_price <= self.constraints.min_supplier_price
        above_max   = last_retail.unit_price >= self.constraints.max_acceptable_unit_price()
        still_apart = last_supplier.unit_price > last_retail.unit_price

        return at_floor and above_max and still_apart

    # ─────────────────────────────────────────────────────────────────────────
    # LOGGING HELPER
    # ─────────────────────────────────────────────────────────────────────────

    def _log_round(
        self,
        round_num: int,
        role: AgentRole,
        offer: NegotiationOffer,
        validation: ValidationResult,
    ) -> None:
        """Emit a structured log entry for a single negotiation round."""
        status = "VALID  ✓" if validation.is_valid else f"INVALID ✗ ({validation.error_message})"
        logger.info(
            "  Round %2d | %-8s | price=%7.4f EUR | vol=%5d | "
            "window=%-4s | terms=%-6s | %s",
            round_num,
            role.value,
            offer.unit_price,
            offer.volume,
            offer.delivery_window,
            offer.payment_terms,
            status,
        )
        logger.debug("  Justification: %s", offer.justification)

    # ─────────────────────────────────────────────────────────────────────────
    # EXTENSIBILITY HOOKS
    # ─────────────────────────────────────────────────────────────────────────

    def _on_round_complete(
        self,
        round_num: int,
        role: AgentRole,
        offer: NegotiationOffer,
        validation: ValidationResult,
        state: NegotiationState,
    ) -> None:
        """
        Called at the end of every round.

        Override in a subclass to:
        - Persist round data to a database
        - Feed data to a memory module
        - Publish real-time events
        """
        pass

    def _on_experiment_end(self, state: NegotiationState) -> None:
        """
        Called once when the negotiation terminates.

        Override to handle post-run teardown (file export, DB write, etc.).
        """
        pass
