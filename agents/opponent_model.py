"""
agents/opponent_model.py
────────────────────────
Opponent Modeling for B2B Negotiation Agents

Tracks and analyzes counterparty behavior to enable reactive,
adaptive negotiation strategies.

Scientific Foundation:
─────────────────────
- Hindriks & Tykhonov (2008): Opponent Modelling in Automated Multi-Issue Negotiation
- Zeng & Sycara (1998): Bayesian Learning in Negotiation
- Baarslag et al. (2013): BOA Architecture - Opponent Model as independent component

Implementation: Heuristic pattern recognition (pragmatic PoC approach)
  - Tracks concession trends per attribute
  - Estimates opponent's attribute priorities (what they protect vs. give away)
  - Classifies opponent type: Boulware / Linear / Conceder
  - Calculates stubbornness score
  - Generates natural language summary for LLM prompts
"""

import logging
import statistics
from typing import Optional

from models.negotiation_models import AgentRole, NegotiationRound

logger = logging.getLogger(__name__)


class OpponentModel:
    """
    Heuristic opponent model that tracks counterparty behavior across rounds.

    Updated after each new offer from the counterparty. Used by the agent
    to adapt its strategy based on observed opponent patterns.
    """

    def __init__(self, my_role: AgentRole):
        self.my_role = my_role
        # Opponent is the other party
        self.opponent_role = (
            AgentRole.RETAILER
            if my_role == AgentRole.SUPPLIER
            else AgentRole.SUPPLIER
        )

        # Concession history per attribute
        self._price_history: list[float] = []
        self._volume_history: list[int] = []
        self._delivery_history: list[int] = []
        self._payment_history: list[str] = []

        # Derived metrics (updated on each call to update())
        self._price_concessions: list[float] = []   # Positive = moving toward agreement
        self._volume_concessions: list[float] = []
        self._delivery_concessions: list[float] = []

        # Opponent classification
        self.opponent_type: str = "unknown"         # boulware / linear / conceder / unknown
        self.concession_trend: str = "stable"       # decreasing / stable / increasing
        self.stubbornness_score: float = 0.5        # 0 = very flexible, 1 = totally rigid
        self.cooperation_score: float = 0.5         # 0 = adversarial, 1 = cooperative

        # Estimated attribute importance (0-1, higher = more important to opponent)
        self.estimated_price_importance: float = 0.5
        self.estimated_volume_importance: float = 0.25
        self.estimated_delivery_importance: float = 0.15
        self.estimated_payment_importance: float = 0.10

        # Rounds since significant concession
        self._rounds_without_concession: int = 0

        # Sentiment approximation from justification text
        self.last_sentiment: str = "neutral"   # cooperative / neutral / frustrated / threatening

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN UPDATE METHOD
    # ═══════════════════════════════════════════════════════════════════════

    def update(self, history: list[NegotiationRound]) -> None:
        """
        Update opponent model based on full negotiation history.
        Called after each new round is added.

        Args:
            history: Complete list of NegotiationRound objects so far
        """
        # Filter to opponent's rounds only
        opponent_rounds = [r for r in history if r.role == self.opponent_role]

        if len(opponent_rounds) < 1:
            return

        # Extract attribute histories
        self._price_history = [r.offer.unit_price for r in opponent_rounds]
        self._volume_history = [r.offer.volume for r in opponent_rounds]
        self._delivery_history = [r.offer.delivery_days for r in opponent_rounds]
        self._payment_history = [r.offer.payment_terms for r in opponent_rounds]

        # Compute concessions (positive = moving toward agreement)
        self._compute_concessions()

        # Classify opponent type
        self._classify_opponent()

        # Estimate attribute priorities
        self._estimate_attribute_priorities()

        # Update stubbornness and cooperation scores
        self._update_scores()

        # Detect sentiment from last justification
        if opponent_rounds:
            last_justification = opponent_rounds[-1].offer.justification or ""
            self._detect_sentiment(last_justification)

        logger.debug(
            f"OpponentModel updated: type={self.opponent_type}, "
            f"trend={self.concession_trend}, stubbornness={self.stubbornness_score:.2f}"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE CALCULATION METHODS
    # ═══════════════════════════════════════════════════════════════════════

    def _compute_concessions(self) -> None:
        """Compute concession amounts per attribute across rounds."""
        if len(self._price_history) < 2:
            self._price_concessions = []
            self._volume_concessions = []
            self._delivery_concessions = []
            return

        # Price concessions:
        #   Supplier moving DOWN = concession (positive)
        #   Retailer moving UP = concession (positive)
        self._price_concessions = []
        for i in range(1, len(self._price_history)):
            prev = self._price_history[i - 1]
            curr = self._price_history[i]
            if self.opponent_role == AgentRole.SUPPLIER:
                # Supplier concedes by lowering price
                concession = prev - curr
            else:
                # Retailer concedes by raising price
                concession = curr - prev
            self._price_concessions.append(concession)

        # Volume concessions (larger volume = supplier concession / flexibility shown)
        self._volume_concessions = []
        for i in range(1, len(self._volume_history)):
            delta = abs(self._volume_history[i] - self._volume_history[i - 1])
            self._volume_concessions.append(float(delta))

        # Delivery concessions (fewer days = supplier concession)
        self._delivery_concessions = []
        for i in range(1, len(self._delivery_history)):
            prev = self._delivery_history[i - 1]
            curr = self._delivery_history[i]
            if self.opponent_role == AgentRole.SUPPLIER:
                # Supplier concedes by reducing delivery days
                concession = prev - curr
            else:
                # Retailer concedes by accepting more days
                concession = curr - prev
            self._delivery_concessions.append(concession)

        # Count rounds without meaningful price concession
        if self._price_concessions:
            last_price_concession = self._price_concessions[-1]
            if abs(last_price_concession) < 0.10:  # Less than €0.10 = no real concession
                self._rounds_without_concession += 1
            else:
                self._rounds_without_concession = 0

    def _classify_opponent(self) -> None:
        """
        Classify opponent negotiation type based on concession pattern.

        Boulware: concessions get smaller over time (protecting position)
        Linear: concessions roughly equal each round
        Conceder: concessions front-loaded (large early, smaller later)
        """
        if len(self._price_concessions) < 2:
            self.opponent_type = "unknown"
            self.concession_trend = "stable"
            return

        # Only positive concessions (actual movement)
        positive_concessions = [c for c in self._price_concessions if c > 0.05]

        if len(positive_concessions) < 2:
            self.opponent_type = "boulware"
            self.concession_trend = "stable"
            return

        # Compare first half vs second half
        mid = len(positive_concessions) // 2
        first_half_avg = statistics.mean(positive_concessions[:mid]) if mid > 0 else 0
        second_half_avg = statistics.mean(positive_concessions[mid:]) if mid < len(positive_concessions) else 0

        if first_half_avg == 0 and second_half_avg == 0:
            self.opponent_type = "boulware"
            self.concession_trend = "stable"
            return

        ratio = second_half_avg / max(first_half_avg, 0.01)

        if ratio < 0.6:
            # Concessions are shrinking → Boulware (or running out of room)
            self.opponent_type = "boulware"
            self.concession_trend = "decreasing"
        elif ratio > 1.4:
            # Concessions are growing → Conceder (or increasing pressure)
            self.opponent_type = "conceder"
            self.concession_trend = "increasing"
        else:
            # Roughly even → Linear
            self.opponent_type = "linear"
            self.concession_trend = "stable"

    def _estimate_attribute_priorities(self) -> None:
        """
        Estimate how important each attribute is to the opponent.

        Logic: The attribute they change the LEAST is most important to them.
        Low variance in changes → high importance (they're protecting it)
        High variance in changes → low importance (they use it as a bargaining chip)
        """
        if len(self._price_history) < 2:
            return

        # Calculate variance of changes for each attribute (normalized 0-1)
        price_variance = self._compute_normalized_variance(self._price_history, scale=50.0)
        volume_variance = self._compute_normalized_variance(
            [float(v) for v in self._volume_history], scale=2000.0
        )
        delivery_variance = self._compute_normalized_variance(
            [float(d) for d in self._delivery_history], scale=30.0
        )

        # Low variance = high importance (attribute is protected)
        # High variance = low importance (attribute is used as trade-off chip)
        self.estimated_price_importance = max(0.1, 1.0 - price_variance)
        self.estimated_volume_importance = max(0.05, 0.5 - volume_variance * 0.5)
        self.estimated_delivery_importance = max(0.05, 0.5 - delivery_variance * 0.5)

        # Payment terms: count how often it changes
        if len(self._payment_history) >= 2:
            changes = sum(
                1 for i in range(1, len(self._payment_history))
                if self._payment_history[i] != self._payment_history[i - 1]
            )
            payment_change_rate = changes / (len(self._payment_history) - 1)
            self.estimated_payment_importance = max(0.05, 0.5 - payment_change_rate * 0.4)
        
        # Normalize so they roughly sum to ~1.0
        total = (
            self.estimated_price_importance
            + self.estimated_volume_importance
            + self.estimated_delivery_importance
            + self.estimated_payment_importance
        )
        if total > 0:
            self.estimated_price_importance /= total
            self.estimated_volume_importance /= total
            self.estimated_delivery_importance /= total
            self.estimated_payment_importance /= total

    def _update_scores(self) -> None:
        """Update stubbornness and cooperation scores."""
        if not self._price_concessions:
            return

        # Stubbornness: how small are the concessions relative to early ones?
        avg_concession = statistics.mean(
            [abs(c) for c in self._price_concessions]
        ) if self._price_concessions else 0

        # Recent concessions (last 2)
        recent_concessions = [abs(c) for c in self._price_concessions[-2:]]
        recent_avg = statistics.mean(recent_concessions) if recent_concessions else 0

        if avg_concession > 0:
            # If recent concessions are smaller than average → more stubborn
            stubbornness_raw = 1.0 - min(1.0, recent_avg / avg_concession)
        else:
            stubbornness_raw = 0.8  # No concessions at all = very stubborn

        # Smooth stubbornness
        self.stubbornness_score = 0.6 * self.stubbornness_score + 0.4 * stubbornness_raw

        # Cooperation: positive concessions = cooperative
        positive = [c for c in self._price_concessions if c > 0.05]
        cooperation_raw = len(positive) / max(len(self._price_concessions), 1)
        self.cooperation_score = 0.6 * self.cooperation_score + 0.4 * cooperation_raw

    def _detect_sentiment(self, justification: str) -> None:
        """
        Approximate sentiment detection from opponent's justification text.
        Simple keyword matching (no external NLP library needed).
        """
        if not justification:
            self.last_sentiment = "neutral"
            return

        text = justification.lower()

        # Threatening / walk-away signals
        threat_keywords = [
            "alternative", "other supplier", "competitor", "final offer",
            "last offer", "cannot go", "firm position", "walk away",
            "better offer", "market rate", "cannot accept"
        ]
        # Cooperative / deal-seeking signals
        cooperative_keywords = [
            "compromise", "meet halfway", "flexible", "understand",
            "appreciate", "work together", "partnership", "long-term",
            "mutual benefit", "good faith"
        ]
        # Frustrated / pressure signals
        frustrated_keywords = [
            "already", "significant", "substantial", "major concession",
            "moved", "reduced", "increased", "adjusted", "cannot further"
        ]

        threat_count = sum(1 for kw in threat_keywords if kw in text)
        cooperative_count = sum(1 for kw in cooperative_keywords if kw in text)
        frustrated_count = sum(1 for kw in frustrated_keywords if kw in text)

        if threat_count >= 2:
            self.last_sentiment = "threatening"
        elif cooperative_count >= 2:
            self.last_sentiment = "cooperative"
        elif frustrated_count >= 2:
            self.last_sentiment = "frustrated"
        else:
            self.last_sentiment = "neutral"

    @staticmethod
    def _compute_normalized_variance(values: list[float], scale: float) -> float:
        """Compute variance of consecutive changes, normalized by scale."""
        if len(values) < 2:
            return 0.0
        changes = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
        if not changes:
            return 0.0
        avg_change = statistics.mean(changes)
        return min(1.0, avg_change / max(scale, 0.01))

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC QUERY METHODS
    # ═══════════════════════════════════════════════════════════════════════

    def get_opponent_type(self) -> str:
        """Returns 'boulware', 'linear', 'conceder', or 'unknown'."""
        return self.opponent_type

    def get_concession_trend(self) -> str:
        """Returns 'decreasing', 'stable', or 'increasing'."""
        return self.concession_trend

    def get_stubbornness_score(self) -> float:
        """Returns 0.0 (very flexible) to 1.0 (totally rigid)."""
        return self.stubbornness_score

    def get_cooperation_score(self) -> float:
        """Returns 0.0 (adversarial) to 1.0 (very cooperative)."""
        return self.cooperation_score

    def get_rounds_without_concession(self) -> int:
        """Returns number of consecutive rounds without meaningful price concession."""
        return self._rounds_without_concession

    def get_convergence_rate(self, my_price_history: list[float]) -> Optional[float]:
        """
        Berechnet die Konvergenzrate in EUR/Runde über die letzten 4 Runden.
        
        Vergleicht parallele Preisverläufe beider Parteien, um echte Konvergenz zu messen.
        
        Positiv = Annäherung (Gap wird kleiner)
        Negativ = Divergenz (Gap wird größer)
        ~0 = Stagnation (Gap bleibt konstant)
        
        Args:
            my_price_history: Liste meiner Preise (parallel zu opponent history)
            
        Returns:
            Konvergenzrate in EUR/Runde, oder None wenn < 4 Runden History
        """
        if len(self._price_history) < 4 or len(my_price_history) < 4:
            return None
        
        # Nehme die letzten 4 Preise beider Parteien
        # Gap vor 4 Runden (mit Zeitstempel-Match)
        opp_old = self._price_history[-4]
        my_old = my_price_history[-4]
        old_gap = abs(opp_old - my_old)
        
        # Aktueller Gap
        opp_current = self._price_history[-1]
        my_current = my_price_history[-1]
        current_gap = abs(opp_current - my_current)
        
        # Konvergenzrate: positive = gut (Gap schließt sich), negative = schlecht (Gap wächst)
        convergence_rate = (old_gap - current_gap) / 4.0
        
        return convergence_rate

    def get_most_flexible_attribute(self) -> str:
        """Returns the attribute the opponent changes most (= their trade-off chip)."""
        importances = {
            "price": self.estimated_price_importance,
            "volume": self.estimated_volume_importance,
            "delivery": self.estimated_delivery_importance,
            "payment": self.estimated_payment_importance,
        }
        # Most flexible = LOWEST importance to them
        return min(importances, key=importances.get)

    def get_most_protected_attribute(self) -> str:
        """Returns the attribute the opponent changes least (= most important to them)."""
        importances = {
            "price": self.estimated_price_importance,
            "volume": self.estimated_volume_importance,
            "delivery": self.estimated_delivery_importance,
            "payment": self.estimated_payment_importance,
        }
        return max(importances, key=importances.get)

    def get_estimated_weights(self) -> dict:
        """Returns estimated attribute importance weights for opponent."""
        return {
            "price": round(self.estimated_price_importance, 3),
            "volume": round(self.estimated_volume_importance, 3),
            "delivery": round(self.estimated_delivery_importance, 3),
            "payment": round(self.estimated_payment_importance, 3),
        }

    def estimate_resistance_point(self) -> Optional[float]:
        """
        Extrapolate opponent's likely resistance/limit price based on trend.
        Returns None if not enough data.
        """
        if len(self._price_history) < 3:
            return None

        # Simple extrapolation: if concessions are decreasing,
        # estimate where the curve hits zero
        if len(self._price_concessions) < 2:
            return None

        recent_concessions = [c for c in self._price_concessions[-3:] if c > 0]
        if not recent_concessions:
            return None

        avg_recent = statistics.mean(recent_concessions)
        last_price = self._price_history[-1]

        if avg_recent < 0.05:
            # Already at resistance point
            return last_price

        # Estimate: current price ± (recent avg × 2 more rounds)
        if self.opponent_role == AgentRole.SUPPLIER:
            return round(last_price - avg_recent * 2, 2)
        else:
            return round(last_price + avg_recent * 2, 2)

    def to_prompt_context(self) -> str:
        """
        Generate a natural language summary of the opponent model for use in LLM prompts.
        This gives the LLM a human-readable situational analysis.
        """
        lines = []

        # Opponent classification
        type_desc = {
            "boulware": "Tough/Boulware negotiator (small concessions, protecting position)",
            "linear": "Steady/Linear negotiator (consistent, predictable concessions)",
            "conceder": "Conceder (front-loaded concessions, now slowing down)",
            "unknown": "Still assessing opponent strategy",
        }
        lines.append(f"Opponent type: {type_desc.get(self.opponent_type, 'unknown')}")
        lines.append(f"Concession trend: {self.concession_trend}")
        lines.append(f"Stubbornness: {self.stubbornness_score:.0%} rigid")
        lines.append(f"Cooperation: {self.cooperation_score:.0%} cooperative")

        # Attribute priorities
        most_protected = self.get_most_protected_attribute()
        most_flexible = self.get_most_flexible_attribute()
        lines.append(f"Most protected attribute (DO NOT push here): {most_protected}")
        lines.append(f"Most flexible attribute (use for trade-offs): {most_flexible}")

        # Resistance point
        resistance = self.estimate_resistance_point()
        if resistance is not None:
            lines.append(f"Estimated resistance point: ~€{resistance:.2f}")

        # Rounds stuck
        if self._rounds_without_concession >= 2:
            lines.append(
                f"WARNING: Opponent has made no meaningful price concession "
                f"for {self._rounds_without_concession} rounds"
            )

        # Sentiment
        sentiment_desc = {
            "cooperative": "Cooperative tone — open to compromise",
            "neutral": "Neutral/professional tone",
            "frustrated": "Showing frustration — may be near their limit",
            "threatening": "Signaling alternatives/walk-away — take seriously",
        }
        lines.append(
            f"Last message sentiment: {sentiment_desc.get(self.last_sentiment, 'neutral')}"
        )

        return "\n".join(lines)