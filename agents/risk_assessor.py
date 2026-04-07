"""
agents/risk_assessor.py
────────────────────────
Risk Assessment Engine for B2B Negotiation Agents

Scientific Foundation:
─────────────────────────────────────────────────
- Lax & Sebenius (1986): The Manager as Negotiator — risk vs. value creation
- Raiffa (1982): The Art and Science of Negotiation — expected utility under uncertainty
- Bazerman & Neale (1992): Negotiating Rationally — cognitive biases in risk assessment

Core Insight:
─────────────────────────────────────────────────
Every decision to push further instead of accepting has TWO costs:
1. Risk of losing the deal entirely (opponent walks away)
2. Opportunity cost of time spent negotiating

Every decision to push further has ONE benefit:
1. Expected price improvement (if opponent concedes)

Rational negotiation = push when E[gain] > E[cost of loss]
Accept when the math flips.

This module quantifies that tradeoff so the agent can make
evidence-based decisions instead of arbitrary threshold checks.
"""

import logging
import math
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class StrategyRecommendation(str, Enum):
    """What the agent should do this round."""
    PUSH_AGGRESSIVE   = "push_aggressive"    # Demand big concession, high leverage
    PUSH_MODERATE     = "push_moderate"      # Push but with reasonable offer
    HOLD              = "hold"               # Maintain position, patience game
    CONCEDE_SMALL     = "concede_small"      # Small tactical concession
    CONCEDE_MEANINGFUL= "concede_meaningful" # Meaningful concession to unlock progress
    ACCEPT            = "accept"             # Deal is good enough, secure it
    WALK_AWAY_SIGNAL  = "walk_away_signal"   # Credible threat, not actual walk-away


@dataclass
class RiskAssessment:
    """Complete risk assessment for the current negotiation state."""
    # Core risk-reward metrics
    walk_away_probability: float    # 0-1: probability opponent will walk away if we push
    expected_gain_eur: float        # EUR: expected price improvement from pushing
    risk_reward_ratio: float        # expected_gain / (walk_away_prob × deal_value_at_risk)
    
    # Time and deal metrics
    time_pressure: float            # 0-1: urgency to close
    deal_value_at_risk: float       # EUR: value of the deal on the table right now
    
    # Recommendation
    recommendation: StrategyRecommendation
    confidence: float               # 0-1: how confident is this assessment
    
    # Explanation for LLM context
    reasoning: str
    
    # Detailed breakdown
    opponent_room_estimate: float   # EUR: estimated remaining room in opponent's position
    rounds_used_pct: float          # % of max_rounds used


class RiskAssessor:
    """
    Assesses the risk-reward of continuing to push vs. accepting.

    Used every round to determine the strategic recommendation.
    The recommendation feeds into the StrategyEngine which selects the actual tactic.
    """

    def __init__(self, is_supplier: bool):
        self.is_supplier = is_supplier
        self._assessment_history: list[RiskAssessment] = []

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN ASSESSMENT METHOD
    # ═══════════════════════════════════════════════════════════════════════

    def assess(
        self,
        current_round: int,
        max_rounds: int,
        my_current_price: float,
        opponent_last_price: Optional[float],
        my_resistance_price: float,
        my_aspiration_price: float,
        opponent_stubbornness: float,
        opponent_cooperation: float,
        opponent_type: str,
        opponent_concession_history: list[float],  # List of opponent's concessions per round
        opponent_sentiment: str,                    # "cooperative" / "neutral" / "frustrated" / "threatening"
        rounds_without_concession: int,
        resistance_point_estimate: Optional[float], # From OpponentModel
        convergence_rate: Optional[float] = None,   # EUR/round convergence from OpponentModel
    ) -> RiskAssessment:
        """
        Full risk-reward assessment for the current moment.

        Args:
            current_round: Current round number
            max_rounds: Maximum allowed rounds
            my_current_price: Our last offered price
            opponent_last_price: Opponent's last offered price
            my_resistance_price: Our absolute walk-away point
            my_aspiration_price: Our current aspiration target
            opponent_stubbornness: 0-1 from OpponentModel
            opponent_cooperation: 0-1 from OpponentModel
            opponent_type: Classification from OpponentModel
            opponent_concession_history: List of concession amounts per round
            opponent_sentiment: Tone of last message
            rounds_without_concession: Consecutive rounds with no meaningful move
            resistance_point_estimate: OpponentModel's estimate of opponent's limit

        Returns:
            RiskAssessment with recommendation and full context
        """
        rounds_remaining = max_rounds - current_round
        rounds_used_pct = current_round / max_rounds if max_rounds > 0 else 0.0
        time_pressure = self._calculate_time_pressure(rounds_used_pct)

        # Current price gap between parties
        price_gap = None
        if opponent_last_price is not None:
            price_gap = abs(my_current_price - opponent_last_price)

        # Deal value currently on the table (from our perspective)
        deal_value_at_risk = self._calculate_deal_value_at_risk(
            my_current_price, my_resistance_price, opponent_last_price
        )

        # Estimate how much room opponent has left
        opponent_room = self._estimate_opponent_room(
            opponent_last_price=opponent_last_price,
            opponent_type=opponent_type,
            opponent_stubbornness=opponent_stubbornness,
            opponent_concession_history=opponent_concession_history,
            resistance_point_estimate=resistance_point_estimate,
        )

        # Expected gain if we push: fraction of opponent's remaining room
        expected_gain = self._calculate_expected_gain(
            opponent_room=opponent_room,
            opponent_cooperation=opponent_cooperation,
            opponent_stubbornness=opponent_stubbornness,
            time_pressure=time_pressure,
            rounds_without_concession=rounds_without_concession,
        )

        # Walk-away probability if we push (don't concede or only slightly)
        walk_away_prob = self._calculate_walk_away_probability(
            opponent_stubbornness=opponent_stubbornness,
            opponent_sentiment=opponent_sentiment,
            opponent_type=opponent_type,
            rounds_without_concession=rounds_without_concession,
            time_pressure=time_pressure,
            price_gap=price_gap,
            opponent_cooperation=opponent_cooperation,
        )

        # Risk-reward ratio
        # = expected_gain / (walk_away_prob × deal_value_at_risk)
        # > 1 = pushing is rational, < 1 = accepting is rational
        denominator = walk_away_prob * deal_value_at_risk
        if denominator > 0.01:
            risk_reward = expected_gain / denominator
        elif walk_away_prob < 0.05:
            # Very low walk-away risk → high ratio
            risk_reward = 5.0
        else:
            risk_reward = 0.5

        # Get recommendation
        recommendation, confidence, reasoning = self._generate_recommendation(
            risk_reward_ratio=risk_reward,
            walk_away_prob=walk_away_prob,
            expected_gain=expected_gain,
            time_pressure=time_pressure,
            opponent_type=opponent_type,
            opponent_sentiment=opponent_sentiment,
            rounds_without_concession=rounds_without_concession,
            price_gap=price_gap,
            deal_value_at_risk=deal_value_at_risk,
            rounds_remaining=rounds_remaining,
            opponent_room=opponent_room,
            convergence_rate=convergence_rate,
        )

        assessment = RiskAssessment(
            walk_away_probability=round(walk_away_prob, 3),
            expected_gain_eur=round(expected_gain, 2),
            risk_reward_ratio=round(risk_reward, 2),
            time_pressure=round(time_pressure, 3),
            deal_value_at_risk=round(deal_value_at_risk, 2),
            recommendation=recommendation,
            confidence=round(confidence, 2),
            reasoning=reasoning,
            opponent_room_estimate=round(opponent_room, 2),
            rounds_used_pct=round(rounds_used_pct, 3),
        )

        self._assessment_history.append(assessment)

        logger.debug(
            f"RiskAssessment: R/R={risk_reward:.2f}, walk_away={walk_away_prob:.2%}, "
            f"expected_gain=€{expected_gain:.2f}, rec={recommendation.value}"
        )

        return assessment

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE CALCULATION METHODS
    # ═══════════════════════════════════════════════════════════════════════

    def _calculate_time_pressure(self, rounds_used_pct: float) -> float:
        """Logistic time pressure curve — low until 70% rounds used, then spikes."""
        k = 12
        midpoint = 0.75
        pressure = 1 / (1 + math.exp(-k * (rounds_used_pct - midpoint)))
        return min(1.0, max(0.0, pressure))

    def _calculate_deal_value_at_risk(
        self,
        my_current_price: float,
        my_resistance_price: float,
        opponent_last_price: Optional[float],
    ) -> float:
        """
        Calculate the value of the deal currently on the table.
        
        If we walk away from the current situation, this is what we lose.
        For a supplier: value = (my_price - resistance) × estimated_volume
        We simplify to just the per-unit margin above resistance.
        """
        if self.is_supplier:
            # Supplier: current offer is opponent's bid price to us
            reference_price = opponent_last_price or my_current_price
            value = reference_price - my_resistance_price
        else:
            # Retailer: current offer is opponent's ask price to us
            reference_price = opponent_last_price or my_current_price
            value = my_resistance_price - reference_price

        return max(0.0, value)

    def _estimate_opponent_room(
        self,
        opponent_last_price: Optional[float],
        opponent_type: str,
        opponent_stubbornness: float,
        opponent_concession_history: list[float],
        resistance_point_estimate: Optional[float],
    ) -> float:
        """
        Estimate how much more the opponent can concede (in EUR).
        
        Uses:
        1. Resistance point estimate from OpponentModel (if available)
        2. Concession trend extrapolation
        3. Type-based heuristics
        """
        if opponent_last_price is None:
            return 2.0  # Default estimate with no data

        # If we have a resistance point estimate
        if resistance_point_estimate is not None:
            distance_to_resistance = abs(opponent_last_price - resistance_point_estimate)
            # They have roughly this much room left
            return max(0.0, distance_to_resistance * 0.7)  # 70% of estimated remaining room

        # Extrapolate from concession history
        if len(opponent_concession_history) >= 3:
            recent = [c for c in opponent_concession_history[-3:] if c > 0]
            if recent:
                avg_recent = statistics.mean(recent)
                # If concessions are decreasing (Boulware), they're near the end
                if len(recent) >= 2 and recent[-1] < recent[0] * 0.5:
                    # Rapidly decreasing — limited room
                    room = avg_recent * 1.5
                else:
                    # Still room to move
                    room = avg_recent * 3.0
                return max(0.5, room)

        # Type-based heuristics
        if opponent_type == "boulware":
            # Boulware players protect their limit — less room
            return 1.5 - opponent_stubbornness * 1.0
        elif opponent_type == "conceder":
            # Conceder is winding down — some room but decreasing
            return 2.0 - opponent_stubbornness * 1.5
        elif opponent_type == "linear":
            # Linear: consistent movement, moderate room
            return 2.5 - opponent_stubbornness * 1.0
        else:
            return 2.0  # Unknown: moderate estimate

    def _calculate_expected_gain(
        self,
        opponent_room: float,
        opponent_cooperation: float,
        opponent_stubbornness: float,
        time_pressure: float,
        rounds_without_concession: int,
    ) -> float:
        """
        Expected EUR gain from pushing this round.
        
        = opponent_room × probability_they_concede
        """
        # Probability opponent concedes if we push
        # Base: 50% — they might give, they might not
        # Adjust: cooperative → higher, stubborn → lower
        p_concede = 0.5
        p_concede += opponent_cooperation * 0.3       # Cooperative → more likely to concede
        p_concede -= opponent_stubbornness * 0.3      # Stubborn → less likely
        p_concede -= rounds_without_concession * 0.05  # Stalled → decreasing probability
        p_concede -= time_pressure * 0.1               # Time pressure → less likely they concede TO US
        p_concede = max(0.05, min(0.9, p_concede))

        expected_gain = opponent_room * p_concede
        return max(0.0, expected_gain)

    def _calculate_walk_away_probability(
        self,
        opponent_stubbornness: float,
        opponent_sentiment: str,
        opponent_type: str,
        rounds_without_concession: int,
        time_pressure: float,
        price_gap: Optional[float],
        opponent_cooperation: float,
    ) -> float:
        """
        Probability the opponent walks away if we don't concede meaningfully.
        
        Based on:
        - Sentiment signals ("final offer", "alternative", etc.)
        - Stubbornness level
        - Rounds without progress
        - Price gap magnitude
        """
        # Base probability
        base = 0.10  # 10% base walk-away chance

        # Sentiment signals — strongest indicator
        sentiment_adjustments = {
            "threatening": 0.35,   # "final offer", "walk away" → high risk
            "frustrated":  0.15,   # Frustration often precedes escalation
            "neutral":     0.00,
            "cooperative": -0.08,  # Cooperative tone → low walk-away risk
        }
        base += sentiment_adjustments.get(opponent_sentiment, 0.0)

        # Type adjustments
        if opponent_type == "boulware":
            # Boulware players are principled — they might actually walk
            base += 0.10
        elif opponent_type == "conceder":
            # Conceders rarely walk away
            base -= 0.05

        # Stubbornness — if they've been very stubborn and we keep pushing
        if opponent_stubbornness > 0.7:
            base += 0.15  # Rigid players eventually hit their limit

        # Rounds without concession — stalemate = danger
        if rounds_without_concession >= 4:
            base += 0.20  # Long stalemate = escalating risk
        elif rounds_without_concession >= 2:
            base += 0.08

        # Price gap — if gap is small, lower walk-away risk (close to deal)
        if price_gap is not None:
            if price_gap < 2.0:
                base -= 0.10  # Very close, neither wants to blow it
            elif price_gap > 15.0:
                base += 0.10  # Large gap = genuine uncertainty

        # Time pressure — late in negotiation, BOTH sides reluctant to walk
        if time_pressure > 0.7:
            base -= 0.15  # Both parties feel pressure to close

        # Cooperative opponent rarely walks
        if opponent_cooperation > 0.7:
            base -= 0.12

        return max(0.03, min(0.95, base))

    def _detect_stagnation(
        self,
        rounds_without_concession: int,
        expected_gain: float,
        opponent_room: float,
        price_gap: Optional[float],
        convergence_rate: Optional[float] = None,
    ) -> tuple[bool, str]:
        """
        Detect if negotiation has stagnated with no realistic path to agreement.
        
        Args:
            convergence_rate: EUR/round convergence (positive=closing, negative=diverging)
        
        Stagnation indicators:
        - Lack of convergence (gap not closing despite movement)
        - 4+ rounds without meaningful concession from either side
        - Expected gain < €0.30 (very low upside)
        - Opponent room < €1.00 (they're near their limit)
        - Large price gap that hasn't narrowed
        
        Returns:
            (is_stagnated, reasoning)
        """
        # PRIORITY 1: Check for lack of convergence (new detection)
        if convergence_rate is not None and price_gap is not None:
            logger.info(
                f"[STAGNATION CHECK] convergence_rate={convergence_rate:.2f}, "
                f"price_gap={price_gap:.2f}, rounds_without_concession={rounds_without_concession}"
            )
            # Check 1: Divergenz (Gap wird größer - negativer convergence_rate)
            if convergence_rate < 0 and price_gap > 20.0:
                logger.warning(
                    f"[STAGNATION DETECTED] Divergenz: Gap €{price_gap:.2f} wächst "
                    f"(€{abs(convergence_rate):.2f}/Runde)"
                )
                return (
                    True,
                    f"Divergenz erkannt: Gap €{price_gap:.2f} wächst "
                    f"(€{abs(convergence_rate):.2f}/Runde). "
                    f"Keine Annäherung möglich."
                )
            
            # Check 2: Insufficient convergence rate - Gap won't close in time
            # Calculate: At current rate, how many rounds to close the gap?
            if convergence_rate > 0:
                rounds_to_close = price_gap / convergence_rate
                # If it would take more rounds than we have left, it's stagnation
                # Add buffer: if it takes > 80% of max rounds, unlikely to succeed
                if rounds_to_close > 12:  # More than 12 rounds needed
                    logger.warning(
                        f"[STAGNATION DETECTED] Unzureichende Konvergenz: Gap €{price_gap:.2f} "
                        f"bei €{convergence_rate:.2f}/Runde → {rounds_to_close:.1f} Runden benötigt"
                    )
                    return (
                        True,
                        f"Unzureichende Konvergenz: Gap €{price_gap:.2f} würde bei aktueller Rate "
                        f"(€{convergence_rate:.2f}/Runde) {rounds_to_close:.0f} Runden zum Schließen benötigen. "
                        f"Deal nicht erreichbar."
                    )
            
            # Check 3: Moderate Divergenz
            if convergence_rate < -0.2 and price_gap > 15.0:
                logger.warning(
                    f"[STAGNATION DETECTED] Divergenz: Gap €{price_gap:.2f} wächst "
                    f"(€{abs(convergence_rate):.2f}/Runde)"
                )
                return (
                    True,
                    f"Divergenz erkannt: Gap €{price_gap:.2f} wächst "
                    f"(€{abs(convergence_rate):.2f}/Runde). "
                    f"Keine Annäherung möglich."
                )
        else:
            if convergence_rate is None:
                logger.debug("[STAGNATION CHECK] convergence_rate is None (< 4 rounds history)")
            if price_gap is None:
                logger.debug("[STAGNATION CHECK] price_gap is None (no opponent offer)")
        
        # Strong stagnation signal: long deadlock + no value left
        if rounds_without_concession >= 5 and expected_gain < 0.50:
            return (
                True,
                f"Verhandlung seit {rounds_without_concession} Runden festgefahren "
                f"mit minimalem Gewinnpotenzial (€{expected_gain:.2f}). "
                f"Keine Konvergenz erkennbar."
            )
        
        # Moderate stagnation: deadlock + low opponent room
        if rounds_without_concession >= 4 and opponent_room < 1.20:
            return (
                True,
                f"Stagnation nach {rounds_without_concession} Runden: "
                f"Gegner hat kaum Spielraum (€{opponent_room:.2f}). "
                f"Weitere Verhandlung unproduktiv."
            )
        
        # Persistent large gap that isn't closing
        if rounds_without_concession >= 4 and price_gap is not None and price_gap > 10.0:
            return (
                True,
                f"Seit {rounds_without_concession} Runden keine Annäherung "
                f"bei großer Preisdifferenz (€{price_gap:.2f}). "
                f"Parteien zu weit auseinander."
            )
        
        return (False, "")

    def _generate_recommendation(
        self,
        risk_reward_ratio: float,
        walk_away_prob: float,
        expected_gain: float,
        time_pressure: float,
        opponent_type: str,
        opponent_sentiment: str,
        rounds_without_concession: int,
        price_gap: Optional[float],
        deal_value_at_risk: float,
        rounds_remaining: int,
        opponent_room: float,
        convergence_rate: Optional[float] = None,
    ) -> tuple[StrategyRecommendation, float, str]:
        """
        Generate strategic recommendation based on risk-reward analysis.

        Decision Matrix:
        ┌────────────────────┬──────────────────┬──────────────────┐
        │                    │ Low Walk-Away     │ High Walk-Away    │
        │                    │ Risk (<25%)       │ Risk (>25%)       │
        ├────────────────────┼──────────────────┼──────────────────┤
        │ High Expected Gain │ PUSH AGGRESSIVE  │ PUSH MODERATE    │
        │ (>€2.00)           │                  │ (calculated risk)│
        ├────────────────────┼──────────────────┼──────────────────┤
        │ Medium Expected    │ HOLD / CONCEDE   │ CONCEDE SMALL    │
        │ Gain (€0.50-2.00)  │ SMALL            │                  │
        ├────────────────────┼──────────────────┼──────────────────┤
        │ Low Expected Gain  │ HOLD             │ ACCEPT / WALK-   │
        │ (<€0.50)           │ (patience)       │ AWAY SIGNAL      │
        └────────────────────┴──────────────────┴──────────────────┘
        """
        # PRIORITY 1: Check for stagnation → autonomous walk-away
        is_stagnated, stagnation_reason = self._detect_stagnation(
            rounds_without_concession=rounds_without_concession,
            expected_gain=expected_gain,
            opponent_room=opponent_room,
            price_gap=price_gap,
            convergence_rate=convergence_rate,
        )
        if is_stagnated:
            return (
                StrategyRecommendation.WALK_AWAY_SIGNAL,
                0.85,
                f"STAGNATION DETECTED: {stagnation_reason} "
                f"→ Autonomer Walk-Away empfohlen."
            )
        
        # Override: threatening sentiment + high walk-away prob = concede or accept
        if opponent_sentiment == "threatening" and walk_away_prob > 0.50:
            if deal_value_at_risk > 5.0:
                return (
                    StrategyRecommendation.CONCEDE_MEANINGFUL,
                    0.80,
                    f"Threatening opponent with {walk_away_prob:.0%} walk-away risk and "
                    f"€{deal_value_at_risk:.2f} at stake — meaningful concession to secure deal"
                )
            else:
                return (
                    StrategyRecommendation.ACCEPT,
                    0.85,
                    f"Threatening opponent + low expected gain (€{expected_gain:.2f}) — "
                    f"secure the deal"
                )

        # Override: almost no rounds left → accept if reasonable
        if rounds_remaining <= 2 and deal_value_at_risk > 0:
            if risk_reward_ratio < 1.5:
                return (
                    StrategyRecommendation.ACCEPT,
                    0.90,
                    f"Only {rounds_remaining} rounds remaining — risk-reward ({risk_reward_ratio:.2f}) "
                    f"doesn't justify risking the deal"
                )

        # Override: long stalemate with threatening signals → walk-away signal
        if rounds_without_concession >= 5 and walk_away_prob < 0.40:
            return (
                StrategyRecommendation.WALK_AWAY_SIGNAL,
                0.70,
                f"Stalemate for {rounds_without_concession} rounds — "
                f"credible walk-away signal to break deadlock"
            )

        # Main decision logic
        high_gain = expected_gain > 2.0
        medium_gain = 0.50 <= expected_gain <= 2.0
        low_walk_away = walk_away_prob < 0.25
        high_walk_away = walk_away_prob > 0.45

        if high_gain and low_walk_away:
            return (
                StrategyRecommendation.PUSH_AGGRESSIVE,
                0.75,
                f"Strong position: €{expected_gain:.2f} expected gain, only "
                f"{walk_away_prob:.0%} walk-away risk — push aggressively"
            )

        if high_gain and not high_walk_away:
            return (
                StrategyRecommendation.PUSH_MODERATE,
                0.70,
                f"Good upside (€{expected_gain:.2f}) with moderate risk "
                f"({walk_away_prob:.0%}) — push with reasonable offer"
            )

        if risk_reward_ratio > 2.5:
            return (
                StrategyRecommendation.PUSH_MODERATE,
                0.65,
                f"Favorable risk-reward ({risk_reward_ratio:.2f}) — "
                f"push for improvement"
            )

        if medium_gain and low_walk_away:
            return (
                StrategyRecommendation.CONCEDE_SMALL,
                0.65,
                f"Moderate gain potential with low risk — small tactical concession "
                f"to maintain momentum"
            )

        if medium_gain and high_walk_away:
            return (
                StrategyRecommendation.CONCEDE_SMALL,
                0.60,
                f"Walk-away risk ({walk_away_prob:.0%}) is elevated — "
                f"small concession to show good faith"
            )

        if not high_gain and low_walk_away:
            return (
                StrategyRecommendation.HOLD,
                0.70,
                f"Low expected gain (€{expected_gain:.2f}) but low walk-away risk — "
                f"hold position, let time pressure work"
            )

        # Low gain + high walk-away → accept or signal
        if deal_value_at_risk > 3.0:
            return (
                StrategyRecommendation.ACCEPT,
                0.75,
                f"Poor risk-reward ({risk_reward_ratio:.2f}) with significant "
                f"deal value at risk (€{deal_value_at_risk:.2f}) — secure the deal"
            )

        return (
            StrategyRecommendation.CONCEDE_MEANINGFUL,
            0.55,
            f"Risk-reward ({risk_reward_ratio:.2f}) suggests conceding — "
            f"unlock progress with meaningful move"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # CONTEXT FOR LLM
    # ═══════════════════════════════════════════════════════════════════════

    def to_prompt_context(self, assessment: RiskAssessment) -> str:
        """Generate LLM-readable risk assessment summary."""
        lines = [
            f"Risk Assessment:",
            f"  Walk-away probability: {assessment.walk_away_probability:.0%}",
            f"  Expected gain from pushing: €{assessment.expected_gain_eur:.2f}",
            f"  Risk-reward ratio: {assessment.risk_reward_ratio:.2f} "
            f"({'favorable' if assessment.risk_reward_ratio > 1.5 else 'unfavorable'})",
            f"  Deal value at risk: €{assessment.deal_value_at_risk:.2f}",
            f"  Time pressure: {assessment.time_pressure:.0%}",
            f"  Opponent's estimated remaining room: €{assessment.opponent_room_estimate:.2f}",
            f"  RECOMMENDATION: {assessment.recommendation.value.upper().replace('_', ' ')}",
            f"  Reasoning: {assessment.reasoning}",
        ]
        return "\n".join(lines)

    def get_assessment_trend(self) -> str:
        """Summarize how risk assessment has changed over recent rounds."""
        if len(self._assessment_history) < 2:
            return "Insufficient history for trend analysis"

        recent = self._assessment_history[-3:]
        rr_values = [a.risk_reward_ratio for a in recent]
        wa_values = [a.walk_away_probability for a in recent]

        rr_trend = "improving" if rr_values[-1] > rr_values[0] else "deteriorating"
        wa_trend = "decreasing" if wa_values[-1] < wa_values[0] else "increasing"

        return (
            f"Risk-reward trend: {rr_trend} "
            f"({rr_values[0]:.2f} → {rr_values[-1]:.2f}), "
            f"Walk-away risk: {wa_trend} "
            f"({wa_values[0]:.0%} → {wa_values[-1]:.0%})"
        )