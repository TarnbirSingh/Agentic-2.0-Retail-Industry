"""
agents/aspiration_manager.py
─────────────────────────────
Aspiration-Driven Negotiation Manager

Scientific Foundation:
─────────────────────────────────────
- Pruitt & Carnevale (1993): Aspiration level as the strongest predictor of negotiation outcome
- Galinsky, Mussweiler & Medvec (2002): Anchoring and aspiration in negotiation
- Oesch & Galinsky (2007): Competitive arousal and aspiration-driven negotiation
- Siegel & Fouraker (1960): Level of Aspiration and Decision Making

Core Insight:
─────────────────────────────────────
A negotiator with a STRONG ASPIRATION (target they believe is reachable) achieves
significantly better outcomes than one who merely tries to avoid going below their
reservation point. The aspiration level drives ambition, persistence, and the
willingness to hold firm under pressure.

This module implements:
1. Dynamic aspiration tracking (starts at target, decays contextually)
2. Aspiration-driven concession sizing (concede less when aspiration is realistic)
3. Aspiration recalibration (update based on opponent behavior)
4. Justification for aspiration levels (fed to LLM for reasoning)
"""

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class AspirationConfidence(str, Enum):
    """How confident the agent is that their aspiration is achievable."""
    HIGH = "high"       # Opponent has lots of room, trend favorable
    MEDIUM = "medium"   # Uncertain, keep pushing but monitor
    LOW = "low"         # Near opponent's limit, aspiration may need to drop


@dataclass
class AspirationState:
    """Current state of the aspiration tracker."""
    current_aspiration: float          # Current target price
    original_target: float             # Initial aspiration (never changes)
    resistance_price: float            # Absolute walk-away point
    confidence: AspirationConfidence   # How achievable is current aspiration
    
    # History
    aspiration_history: list[float] = field(default_factory=list)
    
    # Gap metrics
    aspiration_gap: float = 0.0        # Distance from current best offer to aspiration
    gap_pct: float = 0.0               # Gap as % of original range
    
    # Flags
    is_near_aspiration: bool = False   # Within 2% of aspiration
    is_near_resistance: bool = False   # Within 5% of resistance


class AspirationManager:
    """
    Manages the agent's dynamic aspiration level throughout the negotiation.
    
    The aspiration level is the price the agent WANTS to achieve (not just survive).
    It starts at the target price and adapts based on:
    - Opponent behavior (conceding? stubborn?)
    - Time pressure (rounds remaining)
    - Own concession history (have I given too much already?)
    - Risk signals (is opponent close to walk-away?)
    
    Key Design Principles:
    1. Aspiration NEVER drops below resistance_point + minimum_buffer
    2. Aspiration decays CONTEXTUALLY, not linearly by round
    3. Aspiration can INCREASE if opponent makes a large unexpected concession
    4. Aspiration decay is FASTER when opponent shows credible resistance
    """

    def __init__(
        self,
        is_supplier: bool,
        target_price: float,
        resistance_price: float,
        minimum_buffer_pct: float = 0.08,  # Never within 8% of resistance (increased ambition)
    ):
        """
        Args:
            is_supplier: True = supplier (wants higher price), False = retailer (wants lower)
            target_price: The price the agent ideally wants to achieve
            resistance_price: Absolute walk-away limit (supplier min / retailer max)
            minimum_buffer_pct: % buffer above resistance to keep as minimum aspiration
        """
        self.is_supplier = is_supplier
        self.target_price = target_price
        self.resistance_price = resistance_price
        self.minimum_buffer_pct = minimum_buffer_pct
        
        # Calculate minimum aspiration (cannot go below resistance + buffer)
        if is_supplier:
            self.minimum_aspiration = resistance_price * (1.0 + minimum_buffer_pct)
        else:
            self.minimum_aspiration = resistance_price * (1.0 - minimum_buffer_pct)
        
        # Start at target
        self.current_aspiration = target_price
        
        # Track aspiration history
        self._aspiration_history: list[float] = [target_price]
        
        # Context tracking
        self._rounds_at_current_aspiration: int = 0
        self._last_opponent_price: Optional[float] = None
        self._total_own_concessions: float = 0.0
        self._total_rounds: int = 0
        
        logger.debug(
            f"AspirationManager init: role={'supplier' if is_supplier else 'retailer'}, "
            f"target={target_price:.2f}, resistance={resistance_price:.2f}, "
            f"min_aspiration={self.minimum_aspiration:.2f}"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN UPDATE METHOD
    # ═══════════════════════════════════════════════════════════════════════

    def update(
        self,
        current_round: int,
        my_last_price: Optional[float],
        opponent_last_price: Optional[float],
        opponent_concession_this_round: float,  # Positive = opponent moved toward agreement
        opponent_stubbornness: float,            # 0-1 from OpponentModel
        opponent_cooperation: float,             # 0-1 from OpponentModel  
        opponent_type: str,                      # "boulware" / "linear" / "conceder" / "unknown"
        rounds_remaining: int,
        max_rounds: int,
    ) -> AspirationState:
        """
        Update aspiration based on current negotiation situation.
        
        Returns:
            AspirationState with current aspiration and metadata
        """
        self._total_rounds = current_round
        
        # Calculate time pressure (0 = no pressure, 1 = extreme pressure)
        time_pressure = self._calculate_time_pressure(current_round, rounds_remaining, max_rounds)
        
        # Calculate opponent pressure (how much room does opponent have left?)
        opponent_pressure = self._calculate_opponent_pressure(
            opponent_last_price, opponent_stubbornness, opponent_type
        )
        
        # Determine aspiration adjustment
        new_aspiration = self._calculate_new_aspiration(
            current_aspiration=self.current_aspiration,
            opponent_concession=opponent_concession_this_round,
            opponent_pressure=opponent_pressure,
            time_pressure=time_pressure,
            opponent_cooperation=opponent_cooperation,
            rounds_remaining=rounds_remaining,
        )
        
        # Apply floor: never below minimum aspiration
        new_aspiration = self._clamp_aspiration(new_aspiration)
        
        # Track change
        if abs(new_aspiration - self.current_aspiration) > 0.01:
            logger.debug(
                f"Aspiration adjusted: {self.current_aspiration:.2f} → {new_aspiration:.2f} "
                f"(time_pressure={time_pressure:.2f}, opponent_pressure={opponent_pressure:.2f})"
            )
        
        self.current_aspiration = new_aspiration
        self._aspiration_history.append(new_aspiration)
        self._last_opponent_price = opponent_last_price
        
        # Calculate gap metrics
        if my_last_price and opponent_last_price:
            current_best = self._get_best_current_offer(my_last_price, opponent_last_price)
        elif opponent_last_price:
            current_best = opponent_last_price
        elif my_last_price:
            current_best = my_last_price
        else:
            current_best = self.target_price  # No offers yet
        
        aspiration_gap = self._calculate_gap(current_best, new_aspiration)
        total_range = abs(self.target_price - self.resistance_price)
        gap_pct = aspiration_gap / total_range if total_range > 0 else 0.0
        
        # Confidence assessment
        confidence = self._assess_confidence(
            aspiration_gap=aspiration_gap,
            opponent_stubbornness=opponent_stubbornness,
            opponent_type=opponent_type,
            time_pressure=time_pressure,
        )
        
        # Proximity flags
        is_near_aspiration = gap_pct <= 0.02
        near_resistance_distance = abs(new_aspiration - self.minimum_aspiration)
        total_range_for_resistance = abs(self.target_price - self.minimum_aspiration)
        is_near_resistance = (
            near_resistance_distance / total_range_for_resistance <= 0.05
            if total_range_for_resistance > 0 else True
        )
        
        return AspirationState(
            current_aspiration=round(new_aspiration, 2),
            original_target=round(self.target_price, 2),
            resistance_price=round(self.resistance_price, 2),
            confidence=confidence,
            aspiration_history=list(self._aspiration_history),
            aspiration_gap=round(aspiration_gap, 2),
            gap_pct=round(gap_pct, 3),
            is_near_aspiration=is_near_aspiration,
            is_near_resistance=is_near_resistance,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CONCESSION SIZING
    # ═══════════════════════════════════════════════════════════════════════

    def calculate_concession_size(
        self,
        aspiration_state: AspirationState,
        opponent_stubbornness: float,
        opponent_cooperation: float,
        time_pressure: float,
        risk_reward_ratio: float,
        my_current_price: float,
    ) -> float:
        """
        Calculate the optimal concession size for this round.
        
        Core principle: Concede proportionally to what you expect to gain.
        A large concession when opponent is already near their limit is wasteful.
        A small concession when opponent has lots of room is strategic.
        
        Returns:
            Concession amount in EUR (always positive, direction determined by role)
        """
        # Base concession: percentage of remaining negotiation range
        remaining_range = abs(my_current_price - self.minimum_aspiration)
        
        if remaining_range < 0.01:
            return 0.0  # Nothing left to give
        
        # Base rate: 5-15% of remaining range per round
        base_concession_rate = 0.08
        
        # Adjust for opponent stubbornness
        # Stubborn opponent → smaller concession (don't reward stubbornness)
        # Cooperative opponent → moderate concession (reciprocate)
        if opponent_stubbornness > 0.7:
            stubbornness_modifier = 0.5   # Half the concession
        elif opponent_stubbornness > 0.4:
            stubbornness_modifier = 0.8
        else:
            stubbornness_modifier = 1.2   # Generous when opponent is flexible
        
        # Adjust for time pressure
        # High time pressure → slightly larger concession (deal needs to close)
        # But not too much — panic concessions are exploitable
        time_modifier = 1.0 + (time_pressure * 0.4)
        
        # Adjust for aspiration confidence
        if aspiration_state.confidence == AspirationConfidence.HIGH:
            confidence_modifier = 0.7     # Hold firm, aspiration is achievable
        elif aspiration_state.confidence == AspirationConfidence.MEDIUM:
            confidence_modifier = 1.0
        else:
            confidence_modifier = 1.3     # Near limit, make more meaningful moves
        
        # Adjust for risk-reward
        # Poor risk-reward ratio → larger concession to close the deal
        # Strong risk-reward ratio → small concession, push more
        if risk_reward_ratio > 3.0:
            risk_modifier = 0.6
        elif risk_reward_ratio > 1.5:
            risk_modifier = 0.9
        elif risk_reward_ratio > 0.5:
            risk_modifier = 1.1
        else:
            risk_modifier = 1.4   # Not worth fighting anymore
        
        concession_rate = (
            base_concession_rate
            * stubbornness_modifier
            * time_modifier
            * confidence_modifier
            * risk_modifier
        )
        
        # Clamp to reasonable bounds (min 0.5%, max 20% of remaining range)
        concession_rate = max(0.005, min(0.20, concession_rate))
        
        concession_amount = remaining_range * concession_rate
        
        # Ensure minimum meaningful concession if any concession is made
        # (no "symbolic" €0.02 concessions that signal dishonesty)
        if concession_amount > 0 and concession_amount < 0.50:
            concession_amount = 0.50
        
        return round(concession_amount, 2)

    # ═══════════════════════════════════════════════════════════════════════
    # ACCEPTANCE EVALUATION  
    # ═══════════════════════════════════════════════════════════════════════

    def should_accept(
        self,
        opponent_offer_price: float,
        aspiration_state: AspirationState,
        risk_reward_ratio: float,
        current_round: int,
        min_rounds_before_accept: int = 4,
    ) -> tuple[bool, str]:
        """
        Determine if the opponent's offer should be accepted.
        
        NOT a simple threshold — evaluates:
        1. Is the offer above resistance? (Hard constraint)
        2. Is the aspiration gap small enough to justify accepting?
        3. Does the risk-reward justify continuing to push?
        4. Minimum rounds check (no deal in round 1-3 = always push back)
        
        Returns:
            (should_accept: bool, reasoning: str)
        """
        # Hard constraint: never accept below resistance
        if self.is_supplier:
            if opponent_offer_price < self.resistance_price:
                return False, f"Below resistance point (€{self.resistance_price:.2f})"
        else:
            if opponent_offer_price > self.resistance_price:
                return False, f"Above resistance point (€{self.resistance_price:.2f})"
        
        # Minimum rounds: never accept too early (Winner's Curse avoidance)
        if current_round < min_rounds_before_accept:
            return False, f"Too early to accept (round {current_round}/{min_rounds_before_accept})"
        
        # Calculate how close the offer is to our aspiration
        gap = self._calculate_gap(opponent_offer_price, aspiration_state.current_aspiration)
        total_range = abs(self.target_price - self.resistance_price)
        gap_pct_of_range = gap / total_range if total_range > 0 else 0.0
        
        # If we're within 3% of aspiration → accept
        if gap_pct_of_range <= 0.03:
            return True, (
                f"Offer at €{opponent_offer_price:.2f} is within 3% of aspiration "
                f"(€{aspiration_state.current_aspiration:.2f}) — accepting"
            )
        
        # Risk-reward based: if risk-reward ratio is unfavorable → accept
        # Lowered threshold from 0.4 to 0.25 to maintain higher negotiation ambition
        if risk_reward_ratio < 0.25:
            return True, (
                f"Risk-reward ratio ({risk_reward_ratio:.2f}) too low — "
                f"expected gain doesn't justify continued negotiation risk"
            )
        
        # If near resistance AND risk-reward is neutral → accept
        if aspiration_state.is_near_resistance and risk_reward_ratio < 1.0:
            return True, (
                f"Near resistance point with poor risk-reward — securing the deal"
            )
        
        # Don't accept yet
        remaining_potential = gap
        return False, (
            f"Still €{remaining_potential:.2f} from aspiration — risk-reward "
            f"({risk_reward_ratio:.2f}) justifies continued negotiation"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CONTEXT STRING FOR LLM
    # ═══════════════════════════════════════════════════════════════════════

    def to_prompt_context(self, aspiration_state: AspirationState) -> str:
        """Generate LLM-readable aspiration context."""
        lines = [
            f"My target (aspiration): €{aspiration_state.current_aspiration:.2f}",
            f"Original target: €{aspiration_state.original_target:.2f}",
            f"Walk-away point: €{aspiration_state.resistance_price:.2f}",
            f"Aspiration confidence: {aspiration_state.confidence.value}",
            f"Gap to aspiration: €{aspiration_state.aspiration_gap:.2f} ({aspiration_state.gap_pct:.1%} of negotiation range)",
        ]
        
        if aspiration_state.is_near_aspiration:
            lines.append("STATUS: Very close to aspiration — consider accepting if risk-reward is unfavorable")
        elif aspiration_state.is_near_resistance:
            lines.append("WARNING: Aspiration is near resistance point — limited room for further adjustment")
        
        if len(aspiration_state.aspiration_history) > 2:
            change = aspiration_state.aspiration_history[-1] - aspiration_state.aspiration_history[-2]
            direction = "lowered" if (self.is_supplier and change < 0) or (not self.is_supplier and change > 0) else "maintained/raised"
            lines.append(f"Aspiration {direction} this round by €{abs(change):.2f}")
        
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE CALCULATION METHODS
    # ═══════════════════════════════════════════════════════════════════════

    def _calculate_time_pressure(
        self,
        current_round: int,
        rounds_remaining: int,
        max_rounds: int,
    ) -> float:
        """
        Calculate time pressure (0 = none, 1 = extreme).
        
        Time pressure is non-linear: it stays low for a long time,
        then spikes as the deadline approaches — like a real negotiation.
        """
        if max_rounds <= 0:
            return 0.0
        
        rounds_used_pct = current_round / max_rounds
        
        # Logistic curve: pressure is low until ~70% of rounds used,
        # then rises sharply
        # At 50% rounds used → 0.15 pressure
        # At 75% rounds used → 0.55 pressure
        # At 90% rounds used → 0.85 pressure
        k = 12  # Steepness of the curve
        midpoint = 0.75
        pressure = 1 / (1 + math.exp(-k * (rounds_used_pct - midpoint)))
        
        return min(1.0, max(0.0, pressure))

    def _calculate_opponent_pressure(
        self,
        opponent_last_price: Optional[float],
        opponent_stubbornness: float,
        opponent_type: str,
    ) -> float:
        """
        Estimate how much pressure the opponent is under.
        
        High opponent pressure = they're near their limit → 
        WE should hold firm (they need to move to us).
        Low opponent pressure = they have room to give more →
        We can hold our aspiration.
        """
        # Base pressure from stubbornness
        # Very stubborn = they're protecting near their limit OR they're Boulware
        if opponent_type == "boulware":
            # Boulware: systematic resistance, not necessarily near limit
            type_pressure = 0.4
        elif opponent_type == "conceder":
            # Conceder: slowing down = approaching limit
            type_pressure = 0.7
        elif opponent_type == "linear":
            # Linear: consistent, hard to tell where limit is
            type_pressure = 0.5
        else:
            type_pressure = 0.5
        
        # Combine stubbornness + type
        combined = (opponent_stubbornness * 0.6 + type_pressure * 0.4)
        return min(1.0, max(0.0, combined))

    def _calculate_new_aspiration(
        self,
        current_aspiration: float,
        opponent_concession: float,
        opponent_pressure: float,
        time_pressure: float,
        opponent_cooperation: float,
        rounds_remaining: int,
    ) -> float:
        """
        Calculate the new aspiration level.
        
        Aspiration dynamics:
        - Decays based on opponent pressure + time pressure
        - Increases if opponent makes a large unexpected concession
        - Minimum decay ensures aspiration doesn't stagnate
        """
        total_range = abs(self.target_price - self.minimum_aspiration)
        
        if total_range < 0.01:
            return current_aspiration
        
        # Base decay: very small per round (0.2% of remaining range) — slower decay for higher ambition
        remaining_to_min = abs(current_aspiration - self.minimum_aspiration)
        base_decay = remaining_to_min * 0.002
        
        # Pressure multiplier: high pressure = faster decay
        # (If opponent is near their limit, we need to come down)
        pressure_decay = remaining_to_min * opponent_pressure * 0.02
        
        # Time pressure: as deadline approaches, decay faster
        time_decay = remaining_to_min * time_pressure * 0.03
        
        # Total decay
        total_decay = base_decay + pressure_decay + time_decay
        
        # Opponent concession: if opponent gave a lot, RAISE aspiration slightly
        # (signals they have more room)
        if opponent_concession > 2.0:  # Large concession (>€2)
            # They moved a lot → we should push more
            concession_boost = min(remaining_to_min * 0.05, opponent_concession * 0.1)
            total_decay -= concession_boost  # Negative decay = aspiration stays or rises
        
        # Apply decay in correct direction
        if self.is_supplier:
            new_aspiration = current_aspiration - total_decay
        else:
            new_aspiration = current_aspiration + total_decay
        
        return new_aspiration

    def _clamp_aspiration(self, aspiration: float) -> float:
        """Ensure aspiration never crosses minimum_aspiration."""
        if self.is_supplier:
            return max(self.minimum_aspiration, aspiration)
        else:
            return min(self.minimum_aspiration, aspiration)

    def _get_best_current_offer(
        self,
        my_price: float,
        opponent_price: float,
    ) -> float:
        """Get the most favorable current price from our perspective."""
        if self.is_supplier:
            # Supplier: higher price from opponent = better for us
            return opponent_price
        else:
            # Retailer: lower price from opponent = better for us
            return opponent_price

    def _calculate_gap(self, current_best: float, aspiration: float) -> float:
        """Calculate absolute gap between current best offer and aspiration."""
        return abs(current_best - aspiration)

    def _assess_confidence(
        self,
        aspiration_gap: float,
        opponent_stubbornness: float,
        opponent_type: str,
        time_pressure: float,
    ) -> AspirationConfidence:
        """Assess how achievable the current aspiration is."""
        # Small gap = near aspiration = either high or low confidence
        # depending on what it took to get here
        total_range = abs(self.target_price - self.minimum_aspiration)
        gap_pct = aspiration_gap / total_range if total_range > 0 else 0.0
        
        # If opponent is very stubborn and type is boulware + time pressure is low
        # → they might just be protecting near their limit → HIGH confidence (hold firm)
        if opponent_stubbornness > 0.7 and opponent_type == "boulware" and time_pressure < 0.3:
            return AspirationConfidence.MEDIUM  # Uncertain, but hold
        
        if gap_pct < 0.10:
            # Very close to aspiration
            if opponent_stubbornness < 0.4:
                return AspirationConfidence.HIGH  # Low resistance, push for it
            else:
                return AspirationConfidence.MEDIUM
        elif gap_pct < 0.35:
            if time_pressure < 0.5 and opponent_type != "boulware":
                return AspirationConfidence.HIGH
            else:
                return AspirationConfidence.MEDIUM
        else:
            # Far from aspiration
            if time_pressure > 0.7:
                return AspirationConfidence.LOW
            else:
                return AspirationConfidence.MEDIUM

    def get_current_aspiration(self) -> float:
        """Return current aspiration price."""
        return self.current_aspiration

    def get_aspiration_progress(self) -> float:
        """
        How far has the aspiration moved from target toward resistance?
        0.0 = still at target (aspirational)
        1.0 = at minimum aspiration (near limit)
        """
        total_range = abs(self.target_price - self.minimum_aspiration)
        if total_range <= 0.01:
            return 1.0
        moved = abs(self.target_price - self.current_aspiration)
        return min(1.0, moved / total_range)