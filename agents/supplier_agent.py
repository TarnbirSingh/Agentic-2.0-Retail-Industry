"""
agents/supplier_agent.py
────────────────────────
Supplier sales agent implementation.

Goal: Achieve the highest possible unit price while never falling
below ``min_supplier_price`` (the cost/floor price).

Negotiation strategy
--------------------
1. Open at or near the initial (aspirational) price.
2. Track the last retail offer.  Concede gradually if the retailer
   is negotiating in good faith (price is rising toward the floor).
3. As agreement approaches (gap < 2× threshold), converge faster.
4. Hard floor: unit_price is clamped to min_supplier_price after parsing.

Extensibility
-------------
* Swap strategy by subclassing and overriding ``_build_human_prompt``.
* Future: inject a ``StrategyProfile`` (aggressive / cooperative).
"""

import json
import logging

from agents.base_agent import BaseAgent
from llm.ai_core_client import AICoreClient
from models.constraints import ConstraintModel
from models.negotiation_models import AgentRole, NegotiationOffer, NegotiationState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

_SUPPLIER_SYSTEM_PROMPT = """\
You are a professional B2B sales representative for a product supplier.

YOUR ROLE  : Supplier sales agent protecting the company's revenue.
OBJECTIVE  : Secure the highest achievable unit price while closing the deal.

STRICT RULES (non-negotiable):
1. Respond with ONLY a valid JSON object – absolutely no surrounding text.
2. The JSON must match this exact schema:
   {{
     "unit_price":       <float  – your offered selling price per unit in EUR>,
     "volume":           <int    – proposed order volume>,
     "delivery_window":  <string – must be one of the allowed windows>,
     "payment_terms":    <string – e.g. "Net30", "Net60">,
     "justification":    <string – at least 15 words of value-based reasoning>
   }}
3. NEVER propose a unit_price below your minimum floor price.
4. Only use delivery windows explicitly listed in your constraints.
5. Justify your price with production costs, lead time, or quality arguments.
"""

_SUPPLIER_HUMAN_PROMPT = """\
=== NEGOTIATION CONTEXT ===
Round            : {current_round} / {max_rounds}
Agreement gap    : {price_gap_str}
Agreement threshold : ±{threshold:.2f} EUR

=== YOUR CONSTRAINTS ===
Minimum unit price (floor): {min_supplier_price:.2f} EUR  ← NEVER go below this
Allowed delivery windows  : {allowed_windows}

=== LAST RETAIL OFFER ===
{last_retail_offer}

=== NEGOTIATION HISTORY (newest first) ===
{history_str}

=== YOUR STRATEGY GUIDANCE ===
Initial / ideal price     : {initial_price:.2f} EUR
Gap from retail to floor  : {gap_retail_to_floor:.2f} EUR
Recommended action        : {strategy_guidance}

Respond with your counter-offer JSON now:
"""


# ─────────────────────────────────────────────────────────────────────────────
# SUPPLIER AGENT
# ─────────────────────────────────────────────────────────────────────────────

class SupplierAgent(BaseAgent):
    """
    LLM-driven supplier sales agent.

    Parameters
    ----------
    name                : Agent label for logging.
    llm_client          : Configured ``AICoreClient``.
    initial_price       : Starting (ideal) unit price in EUR.
    initial_volume      : Starting (ideal) order volume.
    max_rounds          : Used in prompts to provide round-context.
    agreement_threshold : Price gap threshold; used to guide convergence.
    max_retries         : LLM call retry count on parse failure.
    """

    def __init__(
        self,
        name: str,
        llm_client: AICoreClient,
        initial_price: float,
        initial_volume: int,
        max_rounds: int = 5,
        agreement_threshold: float = 2.0,
        max_retries: int = 3,
    ) -> None:
        super().__init__(name=name, llm_client=llm_client, max_retries=max_retries)
        self.initial_price = initial_price
        self.initial_volume = initial_volume
        self.max_rounds = max_rounds
        self.agreement_threshold = agreement_threshold

    # ── AgentRole ─────────────────────────────────────────────────────────────

    @property
    def role(self) -> AgentRole:
        return AgentRole.SUPPLIER

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _build_system_prompt(self, constraints: ConstraintModel) -> str:
        """Static supplier sales persona with constraint-aware format rules."""
        return _SUPPLIER_SYSTEM_PROMPT

    def _build_human_prompt(
        self,
        state: NegotiationState,
        constraints: ConstraintModel,
    ) -> str:
        """Inject current negotiation context into the human turn."""

        # ── Last retail offer ─────────────────────────────────────────────────
        last_retail = state.get_last_offer_by_role(AgentRole.RETAIL)
        if last_retail:
            last_retail_str = last_retail.to_prompt_str()
            gap_retail_to_floor = max(
                0.0, last_retail.unit_price - constraints.min_supplier_price
            )
        else:
            last_retail_str = "No retail offer yet (you open the negotiation)."
            gap_retail_to_floor = 0.0

        # ── Price gap ─────────────────────────────────────────────────────────
        price_gap = state.get_price_gap()
        price_gap_str = (
            f"{price_gap:.2f} EUR" if price_gap is not None else "N/A (first round)"
        )

        # ── History ───────────────────────────────────────────────────────────
        history = state.get_history_for_prompt()
        history_str = (
            json.dumps(history, indent=2) if history else "No history yet."
        )

        # ── Strategy guidance ─────────────────────────────────────────────────
        strategy_guidance = self._compute_strategy_guidance(
            last_retail=last_retail,
            min_price=constraints.min_supplier_price,
            price_gap=price_gap,
        )

        return _SUPPLIER_HUMAN_PROMPT.format(
            current_round=state.current_round,
            max_rounds=self.max_rounds,
            price_gap_str=price_gap_str,
            threshold=self.agreement_threshold,
            min_supplier_price=constraints.min_supplier_price,
            allowed_windows=constraints.allowed_delivery_windows,
            last_retail_offer=last_retail_str,
            history_str=history_str,
            initial_price=self.initial_price,
            gap_retail_to_floor=gap_retail_to_floor,
            strategy_guidance=strategy_guidance,
        )

    # ── Strategy guidance helper ──────────────────────────────────────────────

    def _compute_strategy_guidance(
        self,
        last_retail: NegotiationOffer | None,
        min_price: float,
        price_gap: float | None,
    ) -> str:
        """
        Generate a short natural-language strategy hint for the LLM.

        Guides convergence behaviour without hard-coding the exact price.
        """
        if last_retail is None:
            return (
                f"Open with your ideal price of {self.initial_price:.2f} EUR "
                f"and justify it with product quality and delivery reliability."
            )

        retail_price = last_retail.unit_price

        if price_gap is not None and price_gap <= self.agreement_threshold:
            return (
                f"The gap is within the agreement threshold "
                f"({self.agreement_threshold:.2f} EUR). "
                f"Consider accepting retail's offer of {retail_price:.2f} EUR "
                f"if it is ≥ {min_price:.2f} EUR to close the deal."
            )

        if retail_price < min_price:
            return (
                f"Retail's offer {retail_price:.2f} EUR is below your floor "
                f"({min_price:.2f} EUR). Firmly counter at {min_price:.2f}–"
                f"{self.initial_price:.2f} EUR. Explain cost structure."
            )

        # Retail is above floor – converge gradually
        midpoint = round((self.initial_price + retail_price) / 2, 2)
        return (
            f"Retail is above your floor. Make a measured concession "
            f"toward ~{midpoint:.2f} EUR. Highlight value-added services "
            f"to justify a premium."
        )

    # ── Post-process hook ─────────────────────────────────────────────────────

    def _post_process_offer(
        self,
        offer: NegotiationOffer,
        state: NegotiationState,
        constraints: ConstraintModel,
    ) -> NegotiationOffer:
        """
        Clamp supplier unit_price to [min_supplier_price, ∞).

        This is a *soft guard* that corrects minor LLM rounding issues.
        The Validator will still catch any remaining violations.
        """
        min_price = constraints.min_supplier_price
        if offer.unit_price < min_price:
            self.logger.warning(
                "[%s] LLM proposed unit_price=%.4f below min_supplier_price=%.4f. "
                "Clamping to %.4f.",
                self.name,
                offer.unit_price,
                min_price,
                min_price,
            )
            data = offer.model_dump()
            data["unit_price"] = min_price
            data["justification"] = (
                data["justification"]
                + f" [auto-clamped to floor price {min_price:.2f} EUR]"
            )
            return NegotiationOffer(**data)
        return offer
