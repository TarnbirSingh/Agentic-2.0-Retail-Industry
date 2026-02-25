"""
agents/retail_agent.py
──────────────────────
Retail buyer agent implementation.

Goal: Secure the lowest possible unit price while maintaining at least
``min_margin`` gross margin and staying within ``max_budget``.

Negotiation strategy
--------------------
1. Open aggressively close to ``target_price``.
2. Track the last supplier offer.  If the supplier's price > ``max_acceptable``,
   counter at mid-point between own last offer and the supplier's floor.
3. As agreement approaches (gap < 2× threshold), converge faster.
4. Never propose a price that would violate margin or budget constraints
   (the Validator enforces this, but the agent is prompted to respect it).

The LLM receives full negotiation context and is instructed to output
ONLY a JSON object matching ``NegotiationOffer``.

Extensibility
-------------
* Swap the strategy by subclassing and overriding ``_build_human_prompt``.
* Future: inject a ``StrategyProfile`` (aggressive / cooperative / balanced).
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

_RETAIL_SYSTEM_PROMPT = """\
You are a professional retail procurement buyer negotiating a B2B product purchase.

YOUR ROLE  : Retail buyer representing the company's purchasing department.
OBJECTIVE  : Minimise the unit purchase price while maintaining profitability.

STRICT RULES (non-negotiable):
1. Respond with ONLY a valid JSON object – absolutely no surrounding text.
2. The JSON must match this exact schema:
   {{
     "unit_price":       <float  – your offered purchase price per unit in EUR>,
     "volume":           <int    – proposed order volume>,
     "delivery_window":  <string – must be one of the allowed windows>,
     "payment_terms":    <string – e.g. "Net30", "Net60">,
     "justification":    <string – at least 15 words of business reasoning>
   }}
3. Never propose a unit_price above your maximum acceptable price.
4. Never propose a total spend (unit_price × volume) above your budget.
5. Only use delivery windows explicitly listed in your constraints.
"""

_RETAIL_HUMAN_PROMPT = """\
=== NEGOTIATION CONTEXT ===
Round            : {current_round} / {max_rounds}
Agreement gap    : {price_gap_str}
Agreement threshold : ±{threshold:.2f} EUR

=== YOUR CONSTRAINTS ===
Minimum margin              : {min_margin} (must NOT be violated)
Retail selling price        : {retail_selling_price:.2f} EUR
Maximum acceptable buy price: {max_acceptable:.2f} EUR
Maximum total budget        : {max_budget:.2f} EUR
Allowed delivery windows    : {allowed_windows}

=== LAST SUPPLIER OFFER ===
{last_supplier_offer}

=== NEGOTIATION HISTORY (newest first) ===
{history_str}

=== YOUR STRATEGY GUIDANCE ===
Target purchase price : {target_price:.2f} EUR
Current gap to target : {gap_to_target:.2f} EUR (supplier_price − target)
Recommended action    : {strategy_guidance}

Respond with your counter-offer JSON now:
"""


# ─────────────────────────────────────────────────────────────────────────────
# RETAIL AGENT
# ─────────────────────────────────────────────────────────────────────────────

class RetailAgent(BaseAgent):
    """
    LLM-driven retail buyer agent.

    Parameters
    ----------
    name                : Agent label for logging.
    llm_client          : Configured ``AICoreClient``.
    target_price        : Ideal (aspirational) purchase price in EUR.
    retail_selling_price: Price at which the retailer sells to end customers.
    max_rounds          : Used in prompts to provide round-context.
    agreement_threshold : Price gap threshold; used to guide convergence.
    max_retries         : LLM call retry count on parse failure.
    """

    def __init__(
        self,
        name: str,
        llm_client: AICoreClient,
        target_price: float,
        retail_selling_price: float,
        max_rounds: int = 5,
        agreement_threshold: float = 2.0,
        max_retries: int = 3,
    ) -> None:
        super().__init__(name=name, llm_client=llm_client, max_retries=max_retries)
        self.target_price = target_price
        self.retail_selling_price = retail_selling_price
        self.max_rounds = max_rounds
        self.agreement_threshold = agreement_threshold

    # ── AgentRole ─────────────────────────────────────────────────────────────

    @property
    def role(self) -> AgentRole:
        return AgentRole.RETAIL

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _build_system_prompt(self, constraints: ConstraintModel) -> str:
        """Static retail buyer persona with constraint-aware format rules."""
        return _RETAIL_SYSTEM_PROMPT

    def _build_human_prompt(
        self,
        state: NegotiationState,
        constraints: ConstraintModel,
    ) -> str:
        """Inject current negotiation context into the human turn."""

        # ── Last supplier offer ───────────────────────────────────────────────
        last_supplier = state.get_last_offer_by_role(AgentRole.SUPPLIER)
        if last_supplier:
            last_supplier_str = last_supplier.to_prompt_str()
            gap_to_target = last_supplier.unit_price - self.target_price
        else:
            last_supplier_str = "No supplier offer yet (you open the negotiation)."
            gap_to_target = 0.0

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
        max_acceptable = constraints.max_acceptable_unit_price()
        strategy_guidance = self._compute_strategy_guidance(
            last_supplier=last_supplier,
            max_acceptable=max_acceptable,
            price_gap=price_gap,
        )

        return _RETAIL_HUMAN_PROMPT.format(
            current_round=state.current_round,
            max_rounds=self.max_rounds,
            price_gap_str=price_gap_str,
            threshold=self.agreement_threshold,
            min_margin=f"{constraints.min_margin:.0%}",
            retail_selling_price=self.retail_selling_price,
            max_acceptable=max_acceptable,
            max_budget=constraints.max_budget,
            allowed_windows=constraints.allowed_delivery_windows,
            last_supplier_offer=last_supplier_str,
            history_str=history_str,
            target_price=self.target_price,
            gap_to_target=gap_to_target,
            strategy_guidance=strategy_guidance,
        )

    # ── Strategy guidance helper ──────────────────────────────────────────────

    def _compute_strategy_guidance(
        self,
        last_supplier: NegotiationOffer | None,
        max_acceptable: float,
        price_gap: float | None,
    ) -> str:
        """
        Generate a short natural-language strategy hint injected into the prompt.

        This guides the LLM without overriding its judgment – the LLM still
        decides the exact price and justification.
        """
        if last_supplier is None:
            return (
                f"Open with an aggressive offer near your target "
                f"price of {self.target_price:.2f} EUR to set a strong anchor."
            )

        supplier_price = last_supplier.unit_price

        if price_gap is not None and price_gap <= self.agreement_threshold:
            return (
                f"The gap is within the agreement threshold "
                f"({self.agreement_threshold:.2f} EUR). "
                f"Accept supplier's price of {supplier_price:.2f} EUR if it is "
                f"≤ {max_acceptable:.2f} EUR to close the deal."
            )

        if supplier_price > max_acceptable:
            return (
                f"Supplier's price {supplier_price:.2f} EUR exceeds your maximum "
                f"({max_acceptable:.2f} EUR). Counter firmly below "
                f"{max_acceptable:.2f} EUR while justifying with volume commitment."
            )

        # Supplier is within acceptable range – converge
        midpoint = round((supplier_price + self.target_price) / 2, 2)
        return (
            f"Supplier is within acceptable range. Converge by offering "
            f"~{midpoint:.2f} EUR (midpoint between your target and supplier price). "
            f"Emphasise long-term partnership value."
        )

    # ── Post-process hook ─────────────────────────────────────────────────────

    def _post_process_offer(
        self,
        offer: NegotiationOffer,
        state: NegotiationState,
        constraints: ConstraintModel,
    ) -> NegotiationOffer:
        """
        Clamp retail unit_price to [0, max_acceptable_unit_price].

        This is a *soft guard* that corrects minor LLM rounding issues.
        The Validator will still catch any remaining violations.
        Note: we do NOT enforce min_supplier_price here – that belongs
        to the supplier agent's guard and the Validator.
        """
        max_acceptable = constraints.max_acceptable_unit_price()
        if offer.unit_price > max_acceptable:
            self.logger.warning(
                "[%s] LLM proposed unit_price=%.4f exceeds max_acceptable=%.4f. "
                "Clamping to %.4f.",
                self.name,
                offer.unit_price,
                max_acceptable,
                max_acceptable,
            )
            # Rebuild with clamped price
            data = offer.model_dump()
            data["unit_price"] = max_acceptable
            data["justification"] = (
                data["justification"]
                + f" [auto-clamped to max acceptable {max_acceptable:.2f} EUR]"
            )
            return NegotiationOffer(**data)
        return offer
