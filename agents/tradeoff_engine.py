"""
agents/tradeoff_engine.py
──────────────────────────
Trade-off & Logrolling Engine for Multi-Attribute B2B Negotiations

Scientific Foundation:
─────────────────────────────────────────────────────────────────
- Pruitt (1981): Negotiation Behavior — Logrolling as integrative tactic
- Lax & Sebenius (1986): Creating and Claiming Value — trade-off identification
- Fisher, Ury & Patton (1991): Getting to Yes — interests vs. positions
- Raiffa (1982): The Art and Science of Negotiation — efficient frontier of deals

Core Insight:
─────────────────────────────────────────────────────────────────
Two parties often value different attributes differently. A supplier might
weight delivery_days as low importance (they can flex logistics) while the
retailer values it highly (inventory turnover). A trade-off that gives the
retailer faster delivery in exchange for a better price for the supplier
makes BOTH parties better off simultaneously — this is the essence of
logrolling.

This module:
1. Identifies trade-off opportunities based on estimated preference asymmetry
2. Ranks trade-offs by joint value creation potential
3. Generates concrete trade-off proposals for the LLM to phrase
4. Evaluates whether a proposed package deal is better than current position
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TradeoffAttribute(str, Enum):
    """Negotiation attributes that can be traded off."""
    PRICE          = "price"
    VOLUME         = "volume"
    DELIVERY_DAYS  = "delivery_days"
    PAYMENT_TERMS  = "payment_terms"


@dataclass
class TradeoffProposal:
    """A concrete trade-off proposal."""
    # Attribute we're giving (conceding)
    give_attribute: TradeoffAttribute
    give_current: float | int | str    # Our current position
    give_proposed: float | int | str   # What we offer to give
    give_cost_to_us: float             # 0-1 utility cost to us

    # Attribute we're asking for in return
    ask_attribute: TradeoffAttribute
    ask_current: float | int | str     # Opponent's current position
    ask_proposed: float | int | str    # What we want in return
    ask_benefit_to_us: float           # 0-1 utility benefit to us

    # Estimated opponent value
    estimated_benefit_to_opponent: float   # 0-1 estimated benefit to them
    estimated_cost_to_opponent: float      # 0-1 estimated cost to them

    # Joint value (logrolling potential)
    joint_value_score: float   # Higher = better trade-off for both parties
    feasibility: float         # 0-1: how likely opponent accepts this

    # Human-readable proposal
    proposal_text: str
    rationale: str


@dataclass
class TradeoffAnalysis:
    """Complete trade-off analysis for a negotiation round."""
    proposals: list[TradeoffProposal] = field(default_factory=list)
    best_proposal: Optional[TradeoffProposal] = None
    has_viable_tradeoff: bool = False
    analysis_summary: str = ""


class TradeoffEngine:
    """
    Identifies and evaluates logrolling opportunities in multi-attribute negotiations.

    Key design principle: A trade-off is only worthwhile if:
    1. We value what we're asking for MORE than what we're giving
    2. The opponent values what we're giving MORE than what we're asking
    3. Both sides end up better off on net utility

    This is the mathematical definition of Pareto improvement through logrolling.
    """

    def __init__(self, is_supplier: bool):
        self.is_supplier = is_supplier
        self._trade_history: list[TradeoffProposal] = []

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN ANALYSIS METHOD
    # ═══════════════════════════════════════════════════════════════════════

    def analyze(
        self,
        # Current offer state
        my_price: float,
        my_volume: int,
        my_delivery_days: int,
        my_payment_terms: str,
        # Opponent's current offer
        opponent_price: Optional[float],
        opponent_volume: Optional[int],
        opponent_delivery_days: Optional[int],
        opponent_payment_terms: Optional[str],
        # My limits
        my_min_price: Optional[float],
        my_max_price: Optional[float],
        my_min_volume: Optional[int],
        my_max_volume: Optional[int],
        my_max_delivery_days: Optional[int],
        my_acceptable_payment_terms: list[str],
        # My preference weights (how important is each attribute to ME)
        my_price_weight: float = 0.50,
        my_volume_weight: float = 0.20,
        my_delivery_weight: float = 0.15,
        my_payment_weight: float = 0.15,
        # Opponent estimated weights (from OpponentModel)
        opp_price_weight: float = 0.40,
        opp_volume_weight: float = 0.25,
        opp_delivery_weight: float = 0.20,
        opp_payment_weight: float = 0.15,
    ) -> TradeoffAnalysis:
        """
        Identify trade-off opportunities for this round.

        Returns:
            TradeoffAnalysis with ranked proposals
        """
        proposals: list[TradeoffProposal] = []

        # Build weight maps for easy access
        my_weights = {
            TradeoffAttribute.PRICE:         my_price_weight,
            TradeoffAttribute.VOLUME:        my_volume_weight,
            TradeoffAttribute.DELIVERY_DAYS: my_delivery_weight,
            TradeoffAttribute.PAYMENT_TERMS: my_payment_weight,
        }
        opp_weights = {
            TradeoffAttribute.PRICE:         opp_price_weight,
            TradeoffAttribute.VOLUME:        opp_volume_weight,
            TradeoffAttribute.DELIVERY_DAYS: opp_delivery_weight,
            TradeoffAttribute.PAYMENT_TERMS: opp_payment_weight,
        }

        # Generate all trade-off combinations: give X to get Y
        # The trade-off makes sense when:
        # - my_weight[X] < opp_weight[X]  → I value X less → good to give
        # - my_weight[Y] > opp_weight[Y]  → I value Y more → good to ask for
        for give_attr in TradeoffAttribute:
            for ask_attr in TradeoffAttribute:
                if give_attr == ask_attr:
                    continue  # Can't trade the same attribute

                # Check if this trade-off makes theoretical sense
                our_give_weight = my_weights[give_attr]
                opp_give_weight = opp_weights[give_attr]
                our_ask_weight = my_weights[ask_attr]
                opp_ask_weight = opp_weights[ask_attr]

                # Asymmetry score: how much do we disagree on relative values?
                # Positive = we value ASK more relative to opponent's valuation
                give_asymmetry = opp_give_weight - our_give_weight   # Opponent values X more
                ask_asymmetry = our_ask_weight - opp_ask_weight       # We value Y more

                # Trade-off is viable if both asymmetries are positive
                if give_asymmetry <= 0.05 or ask_asymmetry <= 0.05:
                    continue  # No significant asymmetry = no logrolling benefit

                # Generate specific proposal values
                proposal = self._generate_proposal(
                    give_attr=give_attr,
                    ask_attr=ask_attr,
                    give_asymmetry=give_asymmetry,
                    ask_asymmetry=ask_asymmetry,
                    my_price=my_price,
                    my_volume=my_volume,
                    my_delivery_days=my_delivery_days,
                    my_payment_terms=my_payment_terms,
                    opponent_price=opponent_price,
                    opponent_volume=opponent_volume,
                    opponent_delivery_days=opponent_delivery_days,
                    opponent_payment_terms=opponent_payment_terms,
                    my_min_price=my_min_price,
                    my_max_price=my_max_price,
                    my_min_volume=my_min_volume,
                    my_max_volume=my_max_volume,
                    my_max_delivery_days=my_max_delivery_days,
                    my_acceptable_payment_terms=my_acceptable_payment_terms,
                )

                if proposal is not None:
                    proposals.append(proposal)

        # Sort by joint value score (descending)
        proposals.sort(key=lambda p: p.joint_value_score, reverse=True)

        # Filter to top 3 viable proposals
        viable = [p for p in proposals if p.feasibility >= 0.40 and p.joint_value_score >= 0.20][:3]

        best = viable[0] if viable else None

        # Summary
        if viable:
            summary = (
                f"Found {len(viable)} viable trade-off(s). "
                f"Best: {viable[0].proposal_text} "
                f"(joint value: {viable[0].joint_value_score:.2f})"
            )
        else:
            summary = "No significant trade-off opportunities identified — focus on direct price negotiation."

        analysis = TradeoffAnalysis(
            proposals=viable,
            best_proposal=best,
            has_viable_tradeoff=len(viable) > 0,
            analysis_summary=summary,
        )

        logger.debug(
            f"TradeoffEngine: {len(viable)} viable proposals found. "
            f"Best: {best.proposal_text if best else 'None'}"
        )

        return analysis

    # ═══════════════════════════════════════════════════════════════════════
    # PROPOSAL GENERATION
    # ═══════════════════════════════════════════════════════════════════════

    def _generate_proposal(
        self,
        give_attr: TradeoffAttribute,
        ask_attr: TradeoffAttribute,
        give_asymmetry: float,
        ask_asymmetry: float,
        my_price: float,
        my_volume: int,
        my_delivery_days: int,
        my_payment_terms: str,
        opponent_price: Optional[float],
        opponent_volume: Optional[int],
        opponent_delivery_days: Optional[int],
        opponent_payment_terms: Optional[str],
        my_min_price: Optional[float],
        my_max_price: Optional[float],
        my_min_volume: Optional[int],
        my_max_volume: Optional[int],
        my_max_delivery_days: Optional[int],
        my_acceptable_payment_terms: list[str],
    ) -> Optional[TradeoffProposal]:
        """Generate a concrete proposal for a give/ask attribute pair."""

        # Get current values and generate proposed changes
        give_current, give_proposed = self._propose_give(
            attr=give_attr,
            give_asymmetry=give_asymmetry,
            my_price=my_price,
            my_volume=my_volume,
            my_delivery_days=my_delivery_days,
            my_payment_terms=my_payment_terms,
            my_min_price=my_min_price,
            my_max_price=my_max_price,
            my_min_volume=my_min_volume,
            my_max_volume=my_max_volume,
            my_max_delivery_days=my_max_delivery_days,
            my_acceptable_payment_terms=my_acceptable_payment_terms,
        )

        if give_proposed is None:
            return None  # Can't make this concession

        ask_current, ask_proposed = self._propose_ask(
            attr=ask_attr,
            ask_asymmetry=ask_asymmetry,
            opponent_price=opponent_price,
            opponent_volume=opponent_volume,
            opponent_delivery_days=opponent_delivery_days,
            opponent_payment_terms=opponent_payment_terms,
        )

        if ask_proposed is None:
            return None  # No clear ask available

        # Calculate utility impacts
        give_cost = give_asymmetry * 0.5   # Scaled cost to us
        ask_benefit = ask_asymmetry * 0.6  # Scaled benefit to us

        opp_benefit = give_asymmetry * 0.7   # How much opponent gains from our give
        opp_cost = ask_asymmetry * 0.5       # How much opponent loses from our ask

        # Joint value: sum of net gains for both parties
        our_net = ask_benefit - give_cost
        opp_net = opp_benefit - opp_cost
        joint_value = max(0.0, our_net + opp_net)

        # Feasibility: opponent likely to accept?
        feasibility = max(0.0, min(1.0, opp_benefit - opp_cost + 0.5))

        # Generate proposal text
        proposal_text = self._format_proposal_text(
            give_attr, give_current, give_proposed,
            ask_attr, ask_current, ask_proposed,
        )

        rationale = (
            f"We value {ask_attr.value} ({ask_asymmetry:.2f} asymmetry advantage) more than opponent. "
            f"Opponent values {give_attr.value} ({give_asymmetry:.2f} asymmetry advantage) more than us. "
            f"Logrolling creates joint value of {joint_value:.2f}."
        )

        return TradeoffProposal(
            give_attribute=give_attr,
            give_current=give_current,
            give_proposed=give_proposed,
            give_cost_to_us=round(give_cost, 3),
            ask_attribute=ask_attr,
            ask_current=ask_current,
            ask_proposed=ask_proposed,
            ask_benefit_to_us=round(ask_benefit, 3),
            estimated_benefit_to_opponent=round(opp_benefit, 3),
            estimated_cost_to_opponent=round(opp_cost, 3),
            joint_value_score=round(joint_value, 3),
            feasibility=round(feasibility, 3),
            proposal_text=proposal_text,
            rationale=rationale,
        )

    def _propose_give(
        self,
        attr: TradeoffAttribute,
        give_asymmetry: float,
        my_price: float,
        my_volume: int,
        my_delivery_days: int,
        my_payment_terms: str,
        my_min_price: Optional[float],
        my_max_price: Optional[float],
        my_min_volume: Optional[int],
        my_max_volume: Optional[int],
        my_max_delivery_days: Optional[int],
        my_acceptable_payment_terms: list[str],
    ) -> tuple[float | int | str, Optional[float | int | str]]:
        """Calculate what we can give for an attribute."""

        if attr == TradeoffAttribute.PRICE:
            # Supplier gives by lowering price, retailer gives by raising it
            give_magnitude = min(give_asymmetry * 5.0, 3.0)  # Max €3 price give per trade-off
            if self.is_supplier:
                proposed = my_price - give_magnitude
                if my_min_price and proposed < my_min_price:
                    return my_price, None
            else:
                proposed = my_price + give_magnitude
                if my_max_price and proposed > my_max_price:
                    return my_price, None
            return my_price, round(proposed, 2)

        elif attr == TradeoffAttribute.VOLUME:
            # Give flexibility on volume
            give_magnitude = max(50, int(my_volume * give_asymmetry * 0.3))
            if self.is_supplier:
                # Supplier can offer more volume capacity
                proposed = my_volume + give_magnitude
                if my_max_volume and proposed > my_max_volume:
                    return my_volume, None
            else:
                # Retailer can offer higher order quantity
                proposed = my_volume + give_magnitude
                if my_max_volume and proposed > my_max_volume:
                    return my_volume, None
            return my_volume, proposed

        elif attr == TradeoffAttribute.DELIVERY_DAYS:
            # Give by adjusting delivery commitment
            if self.is_supplier:
                # Supplier gives by offering FASTER delivery
                days_faster = max(2, int(my_delivery_days * give_asymmetry * 0.4))
                proposed = my_delivery_days - days_faster
                proposed = max(1, proposed)  # Never less than 1 day
                return my_delivery_days, proposed
            else:
                # Retailer gives by accepting LONGER delivery
                days_longer = max(2, int(give_asymmetry * 10))
                proposed = my_delivery_days + days_longer
                if my_max_delivery_days and proposed > my_max_delivery_days:
                    return my_delivery_days, None
                return my_delivery_days, proposed

        elif attr == TradeoffAttribute.PAYMENT_TERMS:
            # Give by offering better payment terms
            payment_ladder = ["Prepayment", "Net 7", "Net 14", "Net 30", "Net 45", "Net 60", "Net 90"]
            current_idx = next(
                (i for i, t in enumerate(payment_ladder) if t.lower() in my_payment_terms.lower()),
                3  # Default: Net 30
            )

            if self.is_supplier:
                # Supplier gives by accepting LONGER payment (favorable for retailer)
                new_idx = min(len(payment_ladder) - 1, current_idx + 1)
            else:
                # Retailer gives by offering FASTER payment (favorable for supplier)
                new_idx = max(0, current_idx - 1)

            if new_idx == current_idx:
                return my_payment_terms, None

            new_terms = payment_ladder[new_idx]
            if my_acceptable_payment_terms and new_terms not in my_acceptable_payment_terms:
                return my_payment_terms, None

            return my_payment_terms, new_terms

        return my_price, None

    def _propose_ask(
        self,
        attr: TradeoffAttribute,
        ask_asymmetry: float,
        opponent_price: Optional[float],
        opponent_volume: Optional[int],
        opponent_delivery_days: Optional[int],
        opponent_payment_terms: Optional[str],
    ) -> tuple[float | int | str, Optional[float | int | str]]:
        """Calculate what we should ask for in exchange."""

        if attr == TradeoffAttribute.PRICE:
            if opponent_price is None:
                return 0, None
            ask_magnitude = min(ask_asymmetry * 4.0, 2.5)  # Max €2.50 price improvement per trade-off
            if self.is_supplier:
                proposed = opponent_price + ask_magnitude  # Ask for higher price
            else:
                proposed = opponent_price - ask_magnitude  # Ask for lower price
            return opponent_price, round(proposed, 2)

        elif attr == TradeoffAttribute.VOLUME:
            if opponent_volume is None:
                return 0, None
            ask_magnitude = max(25, int(opponent_volume * ask_asymmetry * 0.2))
            # Ask for commitment: higher volume order
            proposed = opponent_volume + ask_magnitude
            return opponent_volume, proposed

        elif attr == TradeoffAttribute.DELIVERY_DAYS:
            if opponent_delivery_days is None:
                return 14, None
            if self.is_supplier:
                # We ask opponent to accept our delivery as-is (no further pressure)
                # Or we ask for a small extension
                proposed = opponent_delivery_days + max(2, int(ask_asymmetry * 5))
                return opponent_delivery_days, proposed
            else:
                # Retailer asks for FASTER delivery
                days_faster = max(2, int(ask_asymmetry * 7))
                proposed = max(1, opponent_delivery_days - days_faster)
                return opponent_delivery_days, proposed

        elif attr == TradeoffAttribute.PAYMENT_TERMS:
            if opponent_payment_terms is None:
                return "Net 30", None
            payment_ladder = ["Prepayment", "Net 7", "Net 14", "Net 30", "Net 45", "Net 60", "Net 90"]
            current_idx = next(
                (i for i, t in enumerate(payment_ladder) if t.lower() in opponent_payment_terms.lower()),
                3
            )
            if self.is_supplier:
                # Ask for FASTER payment from retailer
                new_idx = max(0, current_idx - 1)
            else:
                # Ask for LONGER payment from supplier
                new_idx = min(len(payment_ladder) - 1, current_idx + 1)

            if new_idx == current_idx:
                return opponent_payment_terms, None

            return opponent_payment_terms, payment_ladder[new_idx]

        return 0, None

    def _format_proposal_text(
        self,
        give_attr: TradeoffAttribute,
        give_current: float | int | str,
        give_proposed: float | int | str,
        ask_attr: TradeoffAttribute,
        ask_current: float | int | str,
        ask_proposed: float | int | str,
    ) -> str:
        """Format a human-readable trade-off proposal."""

        def format_val(attr: TradeoffAttribute, val: float | int | str) -> str:
            if attr == TradeoffAttribute.PRICE:
                return f"€{float(val):.2f}"
            elif attr == TradeoffAttribute.DELIVERY_DAYS:
                return f"{val} days"
            elif attr == TradeoffAttribute.VOLUME:
                return f"{val} units"
            else:
                return str(val)

        give_str = (
            f"{give_attr.value.replace('_', ' ')} "
            f"{format_val(give_attr, give_current)} → {format_val(give_attr, give_proposed)}"
        )
        ask_str = (
            f"{ask_attr.value.replace('_', ' ')} "
            f"{format_val(ask_attr, ask_current)} → {format_val(ask_attr, ask_proposed)}"
        )

        return f"Offer: {give_str} | In exchange: {ask_str}"

    # ═══════════════════════════════════════════════════════════════════════
    # PACKAGE DEAL EVALUATION
    # ═══════════════════════════════════════════════════════════════════════

    def evaluate_package_deal(
        self,
        current_price: float,
        proposed_price: float,
        current_delivery: int,
        proposed_delivery: int,
        current_payment: str,
        proposed_payment: str,
        my_price_weight: float,
        my_delivery_weight: float,
        my_payment_weight: float,
    ) -> dict:
        """
        Evaluate whether a proposed multi-attribute package deal is
        better than the current single-dimensional price negotiation.

        Returns dict with: net_utility_change, is_improvement, breakdown
        """
        # Simplified utility delta calculation
        price_change = (proposed_price - current_price) / max(current_price, 1)
        if self.is_supplier:
            price_utility_delta = price_change * my_price_weight
        else:
            price_utility_delta = -price_change * my_price_weight

        delivery_change = (current_delivery - proposed_delivery) / max(current_delivery, 1)
        delivery_utility_delta = delivery_change * my_delivery_weight  # Fewer days = positive

        # Payment terms: simplified
        payment_utility_delta = 0.0
        payment_ladder = ["Prepayment", "Net 7", "Net 14", "Net 30", "Net 45", "Net 60", "Net 90"]
        curr_idx = next(
            (i for i, t in enumerate(payment_ladder) if t.lower() in current_payment.lower()), 3
        )
        prop_idx = next(
            (i for i, t in enumerate(payment_ladder) if t.lower() in proposed_payment.lower()), 3
        )
        payment_change = (prop_idx - curr_idx) / len(payment_ladder)
        if self.is_supplier:
            payment_utility_delta = -payment_change * my_payment_weight  # Supplier prefers shorter terms
        else:
            payment_utility_delta = payment_change * my_payment_weight   # Retailer prefers longer terms

        net_utility = price_utility_delta + delivery_utility_delta + payment_utility_delta

        return {
            "net_utility_change": round(net_utility, 4),
            "is_improvement": net_utility > 0,
            "price_utility_delta": round(price_utility_delta, 4),
            "delivery_utility_delta": round(delivery_utility_delta, 4),
            "payment_utility_delta": round(payment_utility_delta, 4),
            "recommendation": "Accept package" if net_utility > 0.02 else (
                "Neutral" if abs(net_utility) <= 0.02 else "Reject package"
            ),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # CONTEXT FOR LLM
    # ═══════════════════════════════════════════════════════════════════════

    def to_prompt_context(self, analysis: TradeoffAnalysis) -> str:
        """Generate LLM-readable trade-off analysis summary."""
        if not analysis.has_viable_tradeoff:
            return "Trade-off Analysis: No viable logrolling opportunities — focus on direct negotiation."

        lines = ["Trade-off / Logrolling Opportunities:"]
        for i, proposal in enumerate(analysis.proposals[:2], 1):
            lines.append(
                f"  Option {i}: {proposal.proposal_text} "
                f"(joint value: {proposal.joint_value_score:.2f}, "
                f"feasibility: {proposal.feasibility:.0%})"
            )
            lines.append(f"    Rationale: {proposal.rationale}")

        if analysis.best_proposal:
            lines.append(
                f"\nBEST TRADE-OFF: {analysis.best_proposal.proposal_text}"
            )

        return "\n".join(lines)