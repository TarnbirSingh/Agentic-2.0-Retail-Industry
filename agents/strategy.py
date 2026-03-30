"""
agents/strategy.py
──────────────────
Negotiation strategy module for strategic agent behavior.

Theoretical Foundation (Bachelorarbeit):
────────────────────────────────────────
- Concession-making strategies (Okunev 2022, Monczka 2009)
- Anchoring effects (Kahneman & Tversky)
- Time-based concession patterns (Boulware, Conceder, Linear)
- Strategic vs. Integrative approaches

This module defines how agents make concessions over time, when to use
leverage, and how to balance competitive vs. collaborative tactics.
"""

import logging
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ConcessionPattern(str, Enum):
    """
    How an agent makes concessions over negotiation rounds.
    
    Based on negotiation literature (Raiffa, Monczka):
    - BOULWARE: Hold firm initially, concede late (aggressive)
    - LINEAR: Steady, equal concessions per round (predictable)
    - CONCEDER: Large early concessions to build goodwill (collaborative)
    - ADAPTIVE: Adjust based on counterparty behavior (strategic)
    """
    BOULWARE = "boulware"      # Aggressive: small concessions until late rounds
    LINEAR = "linear"          # Moderate: steady pace
    CONCEDER = "conceder"      # Collaborative: quick convergence
    ADAPTIVE = "adaptive"      # Strategic: mirror counterparty


class NegotiationPosture(str, Enum):
    """
    Overall strategic approach to the negotiation.
    
    - COMPETITIVE: Maximize own utility, zero-sum mindset
    - COLLABORATIVE: Seek integrative solutions, joint value creation
    - BALANCED: Mix of both depending on context
    """
    COMPETITIVE = "competitive"
    COLLABORATIVE = "collaborative"
    BALANCED = "balanced"


class LeverageType(str, Enum):
    """
    Types of negotiation leverage an agent can use.
    
    Examples:
    - VOLUME: "We can increase order size if price is right"
    - TIMING: "We need delivery by Q2, willing to pay premium"
    - RELATIONSHIP: "We've been partners for 3 years, let's find middle ground"
    - ALTERNATIVES: "We have other suppliers at €42" (BATNA)
    - QUALITY: "Your product quality justifies premium pricing"
    - MARKET: "Market price range is €40-50"
    """
    VOLUME = "volume"
    TIMING = "timing"
    RELATIONSHIP = "relationship"
    ALTERNATIVES = "alternatives"
    QUALITY = "quality"
    MARKET = "market"


class NegotiationStrategy(BaseModel):
    """
    Complete strategy profile for an agent's negotiation behavior.
    
    This defines HOW an agent negotiates, not WHAT they want (that's in Preferences).
    """
    # Overall posture
    posture: NegotiationPosture = NegotiationPosture.BALANCED
    
    # Concession behavior
    concession_pattern: ConcessionPattern = ConcessionPattern.LINEAR
    concession_rate: float = Field(
        0.15,
        ge=0.0,
        le=1.0,
        description="How much to concede per round (% of remaining gap)"
    )
    min_concession: float = Field(
        0.50,
        description="Minimum absolute concession (EUR)"
    )
    max_concession: float = Field(
        5.0,
        description="Maximum absolute concession per round (EUR)"
    )
    
    # Anchoring
    initial_anchor_multiplier: float = Field(
        1.15,
        ge=1.0,
        le=2.0,
        description="How far above/below target to start (e.g., 1.15 = 15% premium)"
    )
    
    # Leverage usage
    available_leverage: list[LeverageType] = Field(
        default_factory=lambda: [LeverageType.VOLUME, LeverageType.MARKET]
    )
    leverage_threshold_round: int = Field(
        3,
        description="Use leverage after this round if needed"
    )
    
    # Deadline awareness
    urgency_factor: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="How urgent is deal closure? (0=patient, 1=urgent)"
    )
    accelerate_after_round: int = Field(
        7,
        description="Increase concession rate after this round"
    )
    
    # Trade-off willingness
    logrolling_enabled: bool = Field(
        True,
        description="Willing to trade attributes (e.g., pay more for faster delivery)?"
    )
    priority_attributes: list[str] = Field(
        default_factory=lambda: ["price", "volume"],
        description="Which attributes to prioritize in trade-offs"
    )


class ConcessionCalculator:
    """
    Calculates how much to concede in a given round based on strategy.
    
    Implements various concession patterns from negotiation literature.
    """
    
    @staticmethod
    def calculate_boulware_concession(
        round_number: int,
        max_rounds: int,
        total_gap: float,
        base_rate: float = 0.10,
    ) -> float:
        """
        Boulware pattern: Hold firm early, concede late.
        
        Concession increases exponentially in final rounds.
        """
        if round_number <= max_rounds * 0.6:
            # First 60% of rounds: minimal concessions
            return total_gap * base_rate * 0.3
        elif round_number <= max_rounds * 0.8:
            # Next 20%: moderate concessions
            return total_gap * base_rate * 0.6
        else:
            # Final 20%: larger concessions
            return total_gap * base_rate * 1.5
    
    @staticmethod
    def calculate_linear_concession(
        round_number: int,
        max_rounds: int,
        total_gap: float,
        base_rate: float = 0.15,
    ) -> float:
        """
        Linear pattern: Steady, predictable concessions.
        
        Equal concessions per round.
        """
        return total_gap * base_rate
    
    @staticmethod
    def calculate_conceder_concession(
        round_number: int,
        max_rounds: int,
        total_gap: float,
        base_rate: float = 0.20,
    ) -> float:
        """
        Conceder pattern: Large early concessions.
        
        Front-loads concessions to build goodwill.
        """
        if round_number <= max_rounds * 0.3:
            # First 30%: generous
            return total_gap * base_rate * 1.8
        elif round_number <= max_rounds * 0.6:
            # Middle: moderate
            return total_gap * base_rate * 1.0
        else:
            # Late: minimal (already converged)
            return total_gap * base_rate * 0.4
    
    @staticmethod
    def calculate_adaptive_concession(
        round_number: int,
        max_rounds: int,
        total_gap: float,
        counterparty_last_concession: Optional[float],
        base_rate: float = 0.15,
    ) -> float:
        """
        Adaptive pattern: Mirror counterparty behavior.
        
        If they concede generously, reciprocate. If they hold firm, be cautious.
        """
        if counterparty_last_concession is None or counterparty_last_concession == 0:
            # No data yet, use linear default
            return total_gap * base_rate
        
        # Mirror their concession (with slight reduction to maintain advantage)
        mirrored = counterparty_last_concession * 0.9
        
        # Clamp to reasonable range
        min_concession = total_gap * base_rate * 0.5
        max_concession = total_gap * base_rate * 2.0
        
        return max(min_concession, min(mirrored, max_concession))


def calculate_concession_amount(
    strategy: NegotiationStrategy,
    round_number: int,
    max_rounds: int,
    current_gap: float,
    counterparty_last_concession: Optional[float] = None,
) -> float:
    """
    Calculate how much to concede in this round.
    
    Args:
        strategy: Agent's negotiation strategy
        round_number: Current round (1-indexed)
        max_rounds: Maximum negotiation rounds
        current_gap: Current price gap between parties
        counterparty_last_concession: How much counterparty conceded last round
    
    Returns:
        Concession amount in EUR (absolute value)
    """
    calculator = ConcessionCalculator()
    
    # Calculate base concession based on pattern
    if strategy.concession_pattern == ConcessionPattern.BOULWARE:
        concession = calculator.calculate_boulware_concession(
            round_number, max_rounds, current_gap, strategy.concession_rate
        )
    elif strategy.concession_pattern == ConcessionPattern.CONCEDER:
        concession = calculator.calculate_conceder_concession(
            round_number, max_rounds, current_gap, strategy.concession_rate
        )
    elif strategy.concession_pattern == ConcessionPattern.ADAPTIVE:
        concession = calculator.calculate_adaptive_concession(
            round_number, max_rounds, current_gap, counterparty_last_concession, strategy.concession_rate
        )
    else:  # LINEAR
        concession = calculator.calculate_linear_concession(
            round_number, max_rounds, current_gap, strategy.concession_rate
        )
    
    # Apply urgency modifier
    if strategy.urgency_factor > 0.7 and round_number >= strategy.accelerate_after_round:
        concession *= 1.3  # Accelerate to close deal
    
    # Clamp to min/max bounds
    concession = max(strategy.min_concession, min(concession, strategy.max_concession))
    
    # Ensure we don't concede more than remaining gap
    concession = min(concession, current_gap)
    
    logger.debug(
        f"Concession calc: pattern={strategy.concession_pattern.value}, "
        f"round={round_number}/{max_rounds}, gap={current_gap:.2f} → concession={concession:.2f}"
    )
    
    return concession


def calculate_initial_anchor(
    target_price: float,
    strategy: NegotiationStrategy,
    is_supplier: bool,
) -> float:
    """
    Calculate initial offer price using anchoring strategy.
    
    Supplier: Anchors above target (asks for more)
    Retailer: Anchors below target (offers less)
    
    Args:
        target_price: Desired final price
        strategy: Negotiation strategy
        is_supplier: True for supplier, False for retailer
    
    Returns:
        Initial anchor price
    """
    multiplier = strategy.initial_anchor_multiplier
    
    if is_supplier:
        # Supplier: ask for premium above target
        anchor = target_price * multiplier
    else:
        # Retailer: offer discount below target
        anchor = target_price / multiplier
    
    logger.debug(
        f"Initial anchor: target={target_price:.2f}, multiplier={multiplier:.2f}, "
        f"role={'supplier' if is_supplier else 'retailer'} → anchor={anchor:.2f}"
    )
    
    return anchor


def select_leverage(
    strategy: NegotiationStrategy,
    round_number: int,
    current_situation: dict,
) -> Optional[LeverageType]:
    """
    Select which leverage to use in this round (if any).
    
    Args:
        strategy: Agent's strategy
        round_number: Current round
        current_situation: Dict with keys like 'gap_percentage', 'is_stuck', etc.
    
    Returns:
        LeverageType to use, or None
    """
    if round_number < strategy.leverage_threshold_round:
        return None  # Too early
    
    if not strategy.available_leverage:
        return None  # No leverage available
    
    # Use volume leverage if gap is moderate
    gap_pct = current_situation.get("gap_percentage", 0)
    if gap_pct < 10 and LeverageType.VOLUME in strategy.available_leverage:
        return LeverageType.VOLUME
    
    # Use alternatives (BATNA) if stuck
    if current_situation.get("is_stuck", False) and LeverageType.ALTERNATIVES in strategy.available_leverage:
        return LeverageType.ALTERNATIVES
    
    # Use relationship if negotiation is mature
    if round_number >= max(5, strategy.accelerate_after_round):
        if LeverageType.RELATIONSHIP in strategy.available_leverage:
            return LeverageType.RELATIONSHIP
    
    # Default: use first available
    return strategy.available_leverage[0] if strategy.available_leverage else None


def should_make_tradeoff(
    strategy: NegotiationStrategy,
    round_number: int,
    utility_gap: float,
) -> bool:
    """
    Decide if agent should propose a trade-off (logrolling).
    
    E.g., "I'll pay €2 more if you deliver 3 days faster"
    
    Args:
        strategy: Agent's strategy
        round_number: Current round
        utility_gap: Utility difference between parties
    
    Returns:
        True if should propose trade-off
    """
    if not strategy.logrolling_enabled:
        return False
    
    # Only after several rounds when single-attribute negotiation stalls
    if round_number < 4:
        return False
    
    # If utility gap is high, try multi-attribute approach
    if utility_gap > 0.15:
        return True
    
    return False