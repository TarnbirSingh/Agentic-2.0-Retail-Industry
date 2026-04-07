"""
agents/negotiation_agent.py
─────────────────────────────
Agentic AI 2.0 — Professional B2B Negotiation Agent

Replaces simple_agent.py with a fully integrated, aspiration-driven,
risk-aware, adaptive negotiation engine.

Architecture:
─────────────────────────────────────────────────────────────────
                         NegotiationAgent
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
  AspirationManager    OpponentModel         RiskAssessor
  (What do I want?)   (Who am I facing?)   (Is pushing worth it?)
         │                    │                    │
         └────────────────────┼────────────────────┘
                              │
                       TradeoffEngine
                    (Can we do better?)
                              │
                    StrategyEngine (dynamic)
                    4 LLM Calls per round:
                    1. Situation analysis
                    2. Tactic selection
                    3. Offer generation
                    4. Justification

Core Design Principles:
─────────────────────────────────────────────────────────────────
1. ASPIRATION-DRIVEN: Agents fight for their target, not just their minimum
2. DYNAMIC PHASES: No round-based cutoffs — phase from situation
3. RISK-REWARD: Every decision is a calculated gamble, not a threshold check
4. TRADE-OFFS: Logrolling when pure price negotiation is stuck
5. LLM-ENHANCED: 4 strategic LLM calls per round for realistic behavior
"""

import json
import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agents.aspiration_manager import AspirationManager, AspirationState
from agents.opponent_model import OpponentModel
from agents.risk_assessor import RiskAssessor, RiskAssessment, StrategyRecommendation
from agents.tradeoff_engine import TradeoffEngine, TradeoffAnalysis
from llm.ai_core_client import AICoreClient
from models.agent_reasoning import AgentReasoning, ReasoningStep
from models.negotiation_models import (
    AgentRole,
    NegotiationOffer,
    NegotiationRound,
    PartyLimits,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# PERSONALITY SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

class NegotiationPersonality:
    """
    Per-session personality that makes each negotiation feel distinct.
    
    Seeded by session_id for reproducibility — same session always
    gets same personality, but different sessions vary.
    """

    def __init__(self, is_supplier: bool, seed: int = 42):
        rng = random.Random(seed)

        # Toughness: how aggressively agent pursues its aspiration (0=soft, 1=hard)
        self.toughness: float = rng.uniform(0.45, 0.90)

        # Patience: willingness to hold without conceding (0=impatient, 1=very patient)
        self.patience: float = rng.uniform(0.40, 0.85)

        # Risk appetite: willingness to push into high walk-away territory (0=cautious, 1=bold)
        self.risk_appetite: float = rng.uniform(0.25, 0.75)

        # Opening style: how extreme is the opening anchor
        # 1.0 = moderate, 1.3 = aggressive
        self.opening_multiplier: float = rng.uniform(1.08, 1.28) if is_supplier else rng.uniform(0.72, 0.92)

        # Reciprocity: how much we mirror opponent's cooperation (0=ignore, 1=mirror fully)
        self.reciprocity: float = rng.uniform(0.30, 0.70)

        # Trade-off affinity: how willing to use logrolling vs. pure price
        self.tradeoff_affinity: float = rng.uniform(0.30, 0.70)


# ═══════════════════════════════════════════════════════════════════════════
# NEGOTIATION PHASE (situational, not round-based)
# ═══════════════════════════════════════════════════════════════════════════

class NegotiationPhase(str, Enum):
    """
    Dynamic negotiation phases determined by situation, NOT round number.
    
    ANCHORING:  No counterparty data yet; establishing opening position
    EXPLORING:  Opponent type unknown; gathering behavioral data
    BARGAINING: Opponent classified; active concession exchange
    LOGROLLING: Stuck on price; exploring multi-attribute trade-offs
    CLOSING:    Risk-reward says accept; moving toward deal
    DEADLOCK:   No movement; need to escalate or try different tactic
    """
    ANCHORING  = "anchoring"
    EXPLORING  = "exploring"
    BARGAINING = "bargaining"
    LOGROLLING = "logrolling"
    CLOSING    = "closing"
    DEADLOCK   = "deadlock"


@dataclass
class PhaseAssessment:
    """Result of situational phase detection."""
    phase: NegotiationPhase
    confidence: float
    reasoning: str


# ═══════════════════════════════════════════════════════════════════════════
# MAIN AGENT
# ═══════════════════════════════════════════════════════════════════════════

class NegotiationAgent:
    """
    Agentic AI 2.0 Professional B2B Negotiation Agent.
    
    Integrates aspiration management, opponent modeling, risk assessment,
    and logrolling into a coherent negotiation strategy executed with
    multiple LLM calls per round for realistic, adaptive behavior.
    
    Interface compatible with SimpleOrchestrator.
    """

    # Default attribute importance weights by role
    _SUPPLIER_WEIGHTS = {
        "price": 0.50, "volume": 0.25,
        "delivery_days": 0.10, "payment_terms": 0.15,
    }
    _RETAILER_WEIGHTS = {
        "price": 0.40, "volume": 0.20,
        "delivery_days": 0.25, "payment_terms": 0.15,
    }

    def __init__(
        self,
        role: AgentRole,
        llm_client: AICoreClient,
        limits: PartyLimits,
        product_name: str = "product",
        product_data: Optional[dict] = None,
        personality_seed: int = 42,
    ):
        self.role = role
        self.llm_client = llm_client
        self.limits = limits
        self.product_name = product_name
        self.product_data = product_data or {}
        self.is_supplier = (role == AgentRole.SUPPLIER)

        # Initialize personality
        self.personality = NegotiationPersonality(
            is_supplier=self.is_supplier,
            seed=personality_seed,
        )

        # Determine weights
        self.my_weights = (
            self._SUPPLIER_WEIGHTS.copy() if self.is_supplier
            else self._RETAILER_WEIGHTS.copy()
        )

        # Sub-components (initialized in generate_counteroffer with full context)
        self._opponent_model: Optional[OpponentModel] = None
        self._risk_assessor: Optional[RiskAssessor] = None
        self._tradeoff_engine: Optional[TradeoffEngine] = None
        self._aspiration_manager: Optional[AspirationManager] = None

        logger.info(
            f"NegotiationAgent [{role.value}] initialized — "
            f"toughness={self.personality.toughness:.2f}, "
            f"patience={self.personality.patience:.2f}, "
            f"risk_appetite={self.personality.risk_appetite:.2f}"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIMARY INTERFACE — called by SimpleOrchestrator every round
    # ═══════════════════════════════════════════════════════════════════════

    def generate_counteroffer(
        self,
        current_round: int,
        history: list[NegotiationRound],
        counterparty_last_offer: Optional[NegotiationOffer],
        max_rounds: int,
    ) -> tuple[NegotiationOffer, AgentReasoning]:
        """
        Generate the next offer using the full Agentic AI 2.0 pipeline.

        Pipeline:
        1. Initialize / update all sub-components from history
        2. Situational phase detection
        3. Aspiration update
        4. Risk-reward assessment
        5. Trade-off analysis
        6. LLM Call 1: Situationsanalyse (situation analysis)
        7. LLM Call 2: Taktikwahl (tactic selection)
        8. LLM Call 3: Angebotsberechnung (offer computation)
        9. LLM Call 4: Begründung (professional justification)
        10. Acceptance check
        11. Build + return offer + AgentReasoning
        """
        logger.info(
            f"[{self.role.value.upper()}] Round {current_round}/{max_rounds} — "
            f"generating offer"
        )

        rounds_remaining = max_rounds - current_round

        # ── Step 1: Initialize sub-components ─────────────────────────────
        self._initialize_components(history, current_round, max_rounds)

        # ── Step 2: Detect current phase ──────────────────────────────────
        phase_assessment = self._detect_phase(history, current_round, max_rounds)
        logger.debug(f"Phase: {phase_assessment.phase.value} ({phase_assessment.confidence:.0%} confidence)")

        # ── Step 3: Aspiration update ──────────────────────────────────────
        aspiration_state = self._update_aspiration(
            history=history,
            counterparty_last_offer=counterparty_last_offer,
            current_round=current_round,
            rounds_remaining=rounds_remaining,
            max_rounds=max_rounds,
        )

        # ── Step 4: Risk assessment ────────────────────────────────────────
        risk_assessment = self._assess_risk(
            history=history,
            counterparty_last_offer=counterparty_last_offer,
            aspiration_state=aspiration_state,
            current_round=current_round,
            max_rounds=max_rounds,
        )

        # ── Step 5: Trade-off analysis ─────────────────────────────────────
        my_last_offer = self._get_my_last_offer(history)
        tradeoff_analysis = self._analyze_tradeoffs(
            my_last_offer=my_last_offer,
            counterparty_last_offer=counterparty_last_offer,
        )

        # ── Step 6: LLM Situationsanalyse ────────────────────────────────
        situation_analysis = self._llm_situation_analysis(
            history=history,
            counterparty_last_offer=counterparty_last_offer,
            phase_assessment=phase_assessment,
            aspiration_state=aspiration_state,
            risk_assessment=risk_assessment,
            tradeoff_analysis=tradeoff_analysis,
            current_round=current_round,
            max_rounds=max_rounds,
        )

        # ── Step 7: LLM Taktikwahl ───────────────────────────────────────
        tactic_decision = self._llm_tactic_selection(
            situation_analysis=situation_analysis,
            phase_assessment=phase_assessment,
            risk_assessment=risk_assessment,
            tradeoff_analysis=tradeoff_analysis,
            aspiration_state=aspiration_state,
        )

        # ── Step 8: LLM Angebotsberechnung ───────────────────────────────
        offer_data = self._llm_generate_offer(
            my_last_offer=my_last_offer,
            counterparty_last_offer=counterparty_last_offer,
            tactic_decision=tactic_decision,
            aspiration_state=aspiration_state,
            risk_assessment=risk_assessment,
            tradeoff_analysis=tradeoff_analysis,
            current_round=current_round,
        )

        # ── Step 9: Acceptance check ──────────────────────────────────────
        # Check if we should accept the counterparty's last offer
        should_accept_opponent, accept_reason = self._check_acceptance(
            counterparty_last_offer=counterparty_last_offer,
            aspiration_state=aspiration_state,
            risk_assessment=risk_assessment,
            current_round=current_round,
        )

        if should_accept_opponent and counterparty_last_offer:
            logger.info(
                f"[{self.role.value.upper()}] Accepting opponent's offer at "
                f"€{counterparty_last_offer.unit_price:.2f}: {accept_reason}"
            )
            # Build acceptance offer (echo opponent's terms + acceptance tag)
            acceptance_offer = NegotiationOffer(
                unit_price=counterparty_last_offer.unit_price,
                volume=counterparty_last_offer.volume,
                delivery_days=counterparty_last_offer.delivery_days,
                payment_terms=counterparty_last_offer.payment_terms,
                justification=f"[ACCEPTED] {accept_reason}",
            )
            reasoning = self._build_reasoning(
                strategy="ASPIRATION_ACCEPT",
                tactic="accept",
                offer_data={"unit_price": counterparty_last_offer.unit_price},
                my_last_offer=my_last_offer,
                counterparty_last_offer=counterparty_last_offer,
                phase=phase_assessment.phase,
                risk_assessment=risk_assessment,
                aspiration_state=aspiration_state,
                summary=f"Accepted opponent's offer. {accept_reason}",
            )
            return acceptance_offer, reasoning

        # ── Step 10: LLM Begründung ───────────────────────────────────────
        justification = self._llm_generate_justification(
            offer_data=offer_data,
            tactic_decision=tactic_decision,
            situation_analysis=situation_analysis,
            counterparty_last_offer=counterparty_last_offer,
        )

        # ── Step 11: Build final offer ─────────────────────────────────────
        offer = self._build_offer(
            offer_data=offer_data,
            justification=justification,
            my_last_offer=my_last_offer,
            counterparty_last_offer=counterparty_last_offer,
            aspiration_state=aspiration_state,
        )

        # ── Step 12: Build AgentReasoning for transparency ─────────────────
        reasoning = self._build_reasoning(
            strategy=self._get_strategy_name(phase_assessment.phase),
            tactic=tactic_decision.get("tactic", "hold_firm"),
            offer_data=offer_data,
            my_last_offer=my_last_offer,
            counterparty_last_offer=counterparty_last_offer,
            phase=phase_assessment.phase,
            risk_assessment=risk_assessment,
            aspiration_state=aspiration_state,
            summary=tactic_decision.get("summary", f"Generated offer for round {current_round}"),
        )

        logger.info(
            f"[{self.role.value.upper()}] Offer: €{offer.unit_price:.2f} × {offer.volume} units, "
            f"{offer.delivery_days}d, {offer.payment_terms} | "
            f"phase={phase_assessment.phase.value}, tactic={tactic_decision.get('tactic', '?')}"
        )

        return offer, reasoning

    # ═══════════════════════════════════════════════════════════════════════
    # COMPONENT INITIALIZATION
    # ═══════════════════════════════════════════════════════════════════════

    def _initialize_components(
        self,
        history: list[NegotiationRound],
        current_round: int,
        max_rounds: int,
    ) -> None:
        """Initialize/rebuild all sub-components from history."""
        # Opponent model — always rebuilt fresh from full history
        self._opponent_model = OpponentModel(my_role=self.role)
        if history:
            self._opponent_model.update(history)

        # Risk assessor
        self._risk_assessor = RiskAssessor(is_supplier=self.is_supplier)

        # Trade-off engine
        self._tradeoff_engine = TradeoffEngine(is_supplier=self.is_supplier)

        # Aspiration manager — compute from limits
        resistance = self._get_resistance_price()
        target = self._compute_opening_target(resistance)

        self._aspiration_manager = AspirationManager(
            is_supplier=self.is_supplier,
            target_price=target,
            resistance_price=resistance,
        )

        # Replay history to get correct current aspiration state
        my_rounds = [r for r in history if r.role == self.role]
        opponent_rounds = [r for r in history if r.role != self.role]

        for i, my_round in enumerate(my_rounds):
            if i == 0:
                continue  # Skip first (opening) round
            opponent_match = [
                r for r in opponent_rounds
                if r.round_number < my_round.round_number
            ]
            if opponent_match:
                latest_opp = opponent_match[-1].offer
                prev_opp = opponent_match[-2].offer if len(opponent_match) >= 2 else None
                opp_concession = (
                    abs(latest_opp.unit_price - prev_opp.unit_price)
                    if prev_opp else 0.0
                )
                rounds_done = my_round.round_number
                self._aspiration_manager.update(
                    current_round=rounds_done,
                    my_last_price=my_round.offer.unit_price,
                    opponent_last_price=latest_opp.unit_price,
                    opponent_concession_this_round=opp_concession,
                    opponent_stubbornness=self._opponent_model.stubbornness_score,
                    opponent_cooperation=self._opponent_model.cooperation_score,
                    opponent_type=self._opponent_model.opponent_type,
                    rounds_remaining=max(0, max_rounds - rounds_done),
                    max_rounds=max_rounds,
                )

    def _get_resistance_price(self) -> float:
        """Get our absolute limit (walk-away) price."""
        if self.is_supplier:
            return self.limits.min_price or 0.0
        else:
            return self.limits.max_price or 999.0

    def _compute_opening_target(self, resistance: float) -> float:
        """
        Compute the opening target/aspiration price.
        
        This is the BEST price we hope to achieve, not just the limit.
        Uses the opening multiplier from personality.
        """
        if self.is_supplier:
            # Supplier: wants to sell HIGH
            # Target = resistance × opening_multiplier
            return round(resistance * self.personality.opening_multiplier, 2)
        else:
            # Retailer: wants to buy LOW
            # Target = resistance × opening_multiplier (which is < 1)
            return round(resistance * self.personality.opening_multiplier, 2)

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE DETECTION (situational, NOT round-based)
    # ═══════════════════════════════════════════════════════════════════════

    def _detect_phase(
        self,
        history: list[NegotiationRound],
        current_round: int,
        max_rounds: int,
    ) -> PhaseAssessment:
        """
        Detect current negotiation phase from the situation.
        
        Phase is NOT based on round numbers — it's based on:
        - Do we have opponent data? → no: ANCHORING
        - Do we know opponent type? → unknown: EXPLORING
        - Are we stuck (stalled)? → DEADLOCK or LOGROLLING
        - Is risk-reward saying accept? → CLOSING
        - Otherwise: BARGAINING
        """
        opponent_rounds = [r for r in history if r.role != self.role]
        my_rounds = [r for r in history if r.role == self.role]

        # ANCHORING: No opponent offer yet
        if not opponent_rounds:
            return PhaseAssessment(
                phase=NegotiationPhase.ANCHORING,
                confidence=1.0,
                reasoning="No opponent offer received yet — establishing opening position",
            )

        # Need opponent model
        if self._opponent_model is None:
            return PhaseAssessment(
                phase=NegotiationPhase.EXPLORING,
                confidence=0.8,
                reasoning="Opponent model not yet initialized",
            )

        opponent_type = self._opponent_model.get_opponent_type()
        stubbornness = self._opponent_model.get_stubbornness_score()
        rounds_without_concession = self._opponent_model.get_rounds_without_concession()
        cooperation = self._opponent_model.get_cooperation_score()

        # EXPLORING: Opponent type still unknown (< 3 data points)
        if opponent_type == "unknown" and len(opponent_rounds) < 3:
            return PhaseAssessment(
                phase=NegotiationPhase.EXPLORING,
                confidence=0.85,
                reasoning=f"Only {len(opponent_rounds)} opponent rounds — still building behavioral model",
            )

        # DEADLOCK: No movement from BOTH sides for many rounds
        if rounds_without_concession >= 4 and len(my_rounds) >= 3:
            my_prices = [r.offer.unit_price for r in my_rounds[-3:]]
            my_movement = max(my_prices) - min(my_prices)
            if my_movement < 0.50:
                # Check if there's a viable trade-off to try first
                if self._tradeoff_engine is not None:
                    # Trade-offs might break the deadlock
                    return PhaseAssessment(
                        phase=NegotiationPhase.LOGROLLING,
                        confidence=0.75,
                        reasoning=(
                            f"Deadlock: {rounds_without_concession} rounds without meaningful movement — "
                            f"attempting multi-attribute trade-off"
                        ),
                    )
                return PhaseAssessment(
                    phase=NegotiationPhase.DEADLOCK,
                    confidence=0.80,
                    reasoning=(
                        f"Both parties stuck: {rounds_without_concession} rounds no concession, "
                        f"own movement only €{my_movement:.2f}"
                    ),
                )

        # CLOSING: Risk-reward says it's time
        # (checked later in risk assessment, but we can pre-assess)
        rounds_used_pct = current_round / max_rounds if max_rounds > 0 else 0
        if rounds_used_pct > 0.80 and len(opponent_rounds) >= 2:
            opp_prices = [r.offer.unit_price for r in opponent_rounds[-2:]]
            price_gap = None
            if my_rounds:
                my_last = my_rounds[-1].offer.unit_price
                opp_last = opponent_rounds[-1].offer.unit_price
                price_gap = abs(my_last - opp_last)

            if price_gap is not None and price_gap < 5.0:
                return PhaseAssessment(
                    phase=NegotiationPhase.CLOSING,
                    confidence=0.75,
                    reasoning=(
                        f"Late stage ({rounds_used_pct:.0%} rounds used) with small gap "
                        f"(€{price_gap:.2f}) — time to close"
                    ),
                )

        # BARGAINING: Active negotiation in progress
        return PhaseAssessment(
            phase=NegotiationPhase.BARGAINING,
            confidence=0.75 + (0.25 if opponent_type != "unknown" else 0.0),
            reasoning=(
                f"Active bargaining: opponent is {opponent_type}, "
                f"stubbornness={stubbornness:.2f}, cooperation={cooperation:.2f}"
            ),
        )

    # ═══════════════════════════════════════════════════════════════════════
    # ASPIRATION UPDATE
    # ═══════════════════════════════════════════════════════════════════════

    def _update_aspiration(
        self,
        history: list[NegotiationRound],
        counterparty_last_offer: Optional[NegotiationOffer],
        current_round: int,
        rounds_remaining: int,
        max_rounds: int,
    ) -> AspirationState:
        """Update aspiration manager with current round data."""
        my_last_offer = self._get_my_last_offer(history)
        my_last_price = my_last_offer.unit_price if my_last_offer else None
        opp_last_price = counterparty_last_offer.unit_price if counterparty_last_offer else None

        # Calculate opponent's concession this round
        opp_rounds = [r for r in history if r.role != self.role]
        opp_concession = 0.0
        if len(opp_rounds) >= 2:
            prev = opp_rounds[-2].offer.unit_price
            curr = opp_rounds[-1].offer.unit_price if opp_rounds else prev
            if self.is_supplier:
                opp_concession = prev - curr  # Supplier concedes by lowering
            else:
                opp_concession = curr - prev  # Retailer concedes by raising

        return self._aspiration_manager.update(
            current_round=current_round,
            my_last_price=my_last_price,
            opponent_last_price=opp_last_price,
            opponent_concession_this_round=opp_concession,
            opponent_stubbornness=self._opponent_model.stubbornness_score,
            opponent_cooperation=self._opponent_model.cooperation_score,
            opponent_type=self._opponent_model.opponent_type,
            rounds_remaining=rounds_remaining,
            max_rounds=max_rounds,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # RISK ASSESSMENT
    # ═══════════════════════════════════════════════════════════════════════

    def _assess_risk(
        self,
        history: list[NegotiationRound],
        counterparty_last_offer: Optional[NegotiationOffer],
        aspiration_state: AspirationState,
        current_round: int,
        max_rounds: int,
    ) -> RiskAssessment:
        """Run risk assessment for current round."""
        my_last_offer = self._get_my_last_offer(history)
        my_current_price = (
            my_last_offer.unit_price if my_last_offer
            else aspiration_state.current_aspiration
        )
        opp_last_price = counterparty_last_offer.unit_price if counterparty_last_offer else None

        # Build opponent concession history
        opp_rounds = [r for r in history if r.role != self.role]
        opp_concession_history: list[float] = []
        for i in range(1, len(opp_rounds)):
            prev = opp_rounds[i - 1].offer.unit_price
            curr = opp_rounds[i].offer.unit_price
            if self.is_supplier:
                opp_concession_history.append(prev - curr)
            else:
                opp_concession_history.append(curr - prev)

        # Calculate convergence rate (EUR/round over last 4 rounds)
        # Benötigt Preis-Historie für korrekte parallele Gap-Berechnung
        my_rounds = [r for r in history if r.role == self.role]
        my_price_history = [r.offer.unit_price for r in my_rounds]
        convergence_rate = self._opponent_model.get_convergence_rate(my_price_history)

        return self._risk_assessor.assess(
            current_round=current_round,
            max_rounds=max_rounds,
            my_current_price=my_current_price,
            opponent_last_price=opp_last_price,
            my_resistance_price=self._get_resistance_price(),
            my_aspiration_price=aspiration_state.current_aspiration,
            opponent_stubbornness=self._opponent_model.stubbornness_score,
            opponent_cooperation=self._opponent_model.cooperation_score,
            opponent_type=self._opponent_model.opponent_type,
            opponent_concession_history=opp_concession_history,
            opponent_sentiment=self._opponent_model.last_sentiment,
            rounds_without_concession=self._opponent_model.get_rounds_without_concession(),
            resistance_point_estimate=self._opponent_model.estimate_resistance_point(),
            convergence_rate=convergence_rate,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # TRADE-OFF ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_tradeoffs(
        self,
        my_last_offer: Optional[NegotiationOffer],
        counterparty_last_offer: Optional[NegotiationOffer],
    ) -> TradeoffAnalysis:
        """Identify logrolling opportunities."""
        opp_weights = self._opponent_model.get_estimated_weights()

        # Current state defaults
        my_price = my_last_offer.unit_price if my_last_offer else self._aspiration_manager.current_aspiration
        my_volume = my_last_offer.volume if my_last_offer else (self.limits.min_volume or 100)
        my_delivery = my_last_offer.delivery_days if my_last_offer else 14
        my_payment = my_last_offer.payment_terms if my_last_offer else "Net 30"

        return self._tradeoff_engine.analyze(
            my_price=my_price,
            my_volume=my_volume,
            my_delivery_days=my_delivery,
            my_payment_terms=my_payment,
            opponent_price=counterparty_last_offer.unit_price if counterparty_last_offer else None,
            opponent_volume=counterparty_last_offer.volume if counterparty_last_offer else None,
            opponent_delivery_days=counterparty_last_offer.delivery_days if counterparty_last_offer else None,
            opponent_payment_terms=counterparty_last_offer.payment_terms if counterparty_last_offer else None,
            my_min_price=self.limits.min_price,
            my_max_price=self.limits.max_price,
            my_min_volume=self.limits.min_volume,
            my_max_volume=self.limits.max_volume,
            my_max_delivery_days=self.limits.max_delivery_days,
            my_acceptable_payment_terms=self.limits.acceptable_payment_terms or [],
            my_price_weight=self.my_weights["price"],
            my_volume_weight=self.my_weights["volume"],
            my_delivery_weight=self.my_weights["delivery_days"],
            my_payment_weight=self.my_weights["payment_terms"],
            opp_price_weight=opp_weights.get("price", 0.40),
            opp_volume_weight=opp_weights.get("volume", 0.25),
            opp_delivery_weight=opp_weights.get("delivery", 0.20),
            opp_payment_weight=opp_weights.get("payment", 0.15),
        )

    # ═══════════════════════════════════════════════════════════════════════
    # LLM CALL 1: SITUATION ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════

    def _llm_situation_analysis(
        self,
        history: list[NegotiationRound],
        counterparty_last_offer: Optional[NegotiationOffer],
        phase_assessment: PhaseAssessment,
        aspiration_state: AspirationState,
        risk_assessment: RiskAssessment,
        tradeoff_analysis: TradeoffAnalysis,
        current_round: int,
        max_rounds: int,
    ) -> dict:
        """
        LLM Call 1: Assess the current negotiation situation.
        Returns structured analysis of the landscape.
        """
        my_last = self._get_my_last_offer(history)
        opp_rounds = [r for r in history if r.role != self.role]
        my_rounds = [r for r in history if r.role == self.role]

        # Build price history summary
        history_summary = ""
        if len(opp_rounds) >= 2:
            opp_prices = [f"€{r.offer.unit_price:.2f}" for r in opp_rounds[-4:]]
            history_summary += f"Opponent price trend: {' → '.join(opp_prices)}\n"
        if len(my_rounds) >= 2:
            my_prices = [f"€{r.offer.unit_price:.2f}" for r in my_rounds[-4:]]
            history_summary += f"My price trend: {' → '.join(my_prices)}\n"

        prompt = f"""You are an expert B2B negotiation analyst with 15 years of experience in retail supply chain negotiations.

ROLE: {self.role.value.upper()} negotiating for {self.product_name}
ROUND: {current_round} of {max_rounds}
PHASE: {phase_assessment.phase.value} — {phase_assessment.reasoning}

=== CURRENT POSITION ===
My last offer: {f"€{my_last.unit_price:.2f} × {my_last.volume} units, {my_last.delivery_days}d, {my_last.payment_terms}" if my_last else "No offer yet"}
Opponent's last offer: {f"€{counterparty_last_offer.unit_price:.2f} × {counterparty_last_offer.volume} units, {counterparty_last_offer.delivery_days}d, {counterparty_last_offer.payment_terms}" if counterparty_last_offer else "No offer yet"}
Price gap: {f"€{abs(my_last.unit_price - counterparty_last_offer.unit_price):.2f}" if my_last and counterparty_last_offer else "N/A"}

=== NEGOTIATION INTELLIGENCE ===
{self._opponent_model.to_prompt_context() if self._opponent_model else "No opponent data yet"}

=== ASPIRATION STATUS ===
{self._aspiration_manager.to_prompt_context(aspiration_state)}

=== RISK-REWARD ANALYSIS ===
{self._risk_assessor.to_prompt_context(risk_assessment)}

=== TRADE-OFF OPPORTUNITIES ===
{self._tradeoff_engine.to_prompt_context(tradeoff_analysis)}

=== PRICE HISTORY ===
{history_summary if history_summary else "No history yet"}

Analyze this negotiation situation. Consider:
1. What is the opponent's likely strategy and true position?
2. What is the power balance right now?
3. What are the key risks in this situation?
4. What opportunity exists that we haven't yet exploited?

Respond in JSON format:
{{
    "power_balance": "describe who has more leverage and why",
    "opponent_likely_position": "estimate what the opponent truly wants and their realistic limit",
    "key_opportunity": "the single best opportunity in this situation",
    "key_risk": "the biggest risk if we push too hard",
    "situation_summary": "one sentence executive summary"
}}"""

        try:
            response = self.llm_client.generate_text(prompt, max_tokens=500)
            # Find JSON in response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except Exception as e:
            logger.warning(f"LLM situation analysis failed: {e}")

        return {
            "power_balance": f"Phase {phase_assessment.phase.value}, round {current_round}/{max_rounds}",
            "opponent_likely_position": f"Opponent is {self._opponent_model.opponent_type if self._opponent_model else 'unknown'}",
            "key_opportunity": tradeoff_analysis.analysis_summary if tradeoff_analysis.has_viable_tradeoff else "Direct price pressure",
            "key_risk": f"Walk-away probability: {risk_assessment.walk_away_probability:.0%}",
            "situation_summary": f"Round {current_round}: {phase_assessment.phase.value} phase, R/R={risk_assessment.risk_reward_ratio:.2f}",
        }

    # ═══════════════════════════════════════════════════════════════════════
    # LLM CALL 2: TACTIC SELECTION
    # ═══════════════════════════════════════════════════════════════════════

    def _llm_tactic_selection(
        self,
        situation_analysis: dict,
        phase_assessment: PhaseAssessment,
        risk_assessment: RiskAssessment,
        tradeoff_analysis: TradeoffAnalysis,
        aspiration_state: AspirationState,
    ) -> dict:
        """
        LLM Call 2: Choose the optimal tactic for this round.
        Combines quantitative recommendation with qualitative judgment.
        """
        role_description = "supplier (selling, want higher price)" if self.is_supplier else "retailer (buying, want lower price)"

        tactic_options = """Available tactics:
- "push_aggressive": Demand big concession, strong language, anchor high/low
- "push_moderate": Reasonable counter-offer, explain value, apply moderate pressure
- "hold_firm": Maintain position exactly, cite principles ("this is our fair price")
- "concede_small": Small tactical concession (<2%), show good faith
- "concede_meaningful": Meaningful concession (3-8%), signal genuine flexibility
- "logrolling": Propose multi-attribute trade-off (e.g., better price for faster delivery)
- "walk_away_signal": Credible walk-away threat to break deadlock (use sparingly)
- "accept": Accept opponent's last offer"""

        prompt = f"""You are a seasoned B2B negotiation strategist. Choose the optimal tactic for this round.

ROLE: {role_description}
PHASE: {phase_assessment.phase.value}

=== QUANTITATIVE RECOMMENDATION ===
Risk-reward system recommends: {risk_assessment.recommendation.value.upper()}
Reasoning: {risk_assessment.reasoning}
Walk-away probability: {risk_assessment.walk_away_probability:.0%}
Expected gain from pushing: €{risk_assessment.expected_gain_eur:.2f}
Risk-reward ratio: {risk_assessment.risk_reward_ratio:.2f}

=== SITUATION ANALYSIS ===
Power balance: {situation_analysis.get('power_balance', 'unknown')}
Key opportunity: {situation_analysis.get('key_opportunity', 'none identified')}
Key risk: {situation_analysis.get('key_risk', 'unknown')}
Opponent position: {situation_analysis.get('opponent_likely_position', 'unknown')}

=== ASPIRATION STATUS ===
Current aspiration: €{aspiration_state.current_aspiration:.2f}
Confidence: {aspiration_state.confidence.value}
Near aspiration: {aspiration_state.is_near_aspiration}
Near resistance (limit): {aspiration_state.is_near_resistance}

=== PERSONALITY FACTORS ===
Toughness: {self.personality.toughness:.2f} (higher = more aggressive)
Patience: {self.personality.patience:.2f} (higher = more willing to wait)
Risk appetite: {self.personality.risk_appetite:.2f} (higher = more willing to push)
Trade-off affinity: {self.personality.tradeoff_affinity:.2f}

{tactic_options}

Choose the tactic that maximizes long-term outcome while managing risk. The quantitative system is advisory — use your judgment.

Respond in JSON:
{{
    "tactic": "one of the tactic names above",
    "confidence": 0.0-1.0,
    "reasoning": "why this tactic is best for this moment",
    "alternative_tactic": "backup if primary doesn't work",
    "summary": "one sentence describing what we're doing and why",
    "use_tradeoff": true/false
}}"""

        try:
            response = self.llm_client.generate_text(prompt, max_tokens=400)
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except Exception as e:
            logger.warning(f"LLM tactic selection failed: {e}")

        # Fallback: use risk assessor recommendation directly
        tactic_map = {
            StrategyRecommendation.PUSH_AGGRESSIVE: "push_aggressive",
            StrategyRecommendation.PUSH_MODERATE: "push_moderate",
            StrategyRecommendation.HOLD: "hold_firm",
            StrategyRecommendation.CONCEDE_SMALL: "concede_small",
            StrategyRecommendation.CONCEDE_MEANINGFUL: "concede_meaningful",
            StrategyRecommendation.ACCEPT: "accept",
            StrategyRecommendation.WALK_AWAY_SIGNAL: "walk_away_signal",
        }
        return {
            "tactic": tactic_map.get(risk_assessment.recommendation, "push_moderate"),
            "confidence": risk_assessment.confidence,
            "reasoning": risk_assessment.reasoning,
            "alternative_tactic": "concede_small",
            "summary": risk_assessment.reasoning,
            "use_tradeoff": tradeoff_analysis.has_viable_tradeoff and self.personality.tradeoff_affinity > 0.5,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # LLM CALL 3: OFFER GENERATION
    # ═══════════════════════════════════════════════════════════════════════

    def _llm_generate_offer(
        self,
        my_last_offer: Optional[NegotiationOffer],
        counterparty_last_offer: Optional[NegotiationOffer],
        tactic_decision: dict,
        aspiration_state: AspirationState,
        risk_assessment: RiskAssessment,
        tradeoff_analysis: TradeoffAnalysis,
        current_round: int,
    ) -> dict:
        """
        LLM Call 3: Generate concrete offer numbers.
        The LLM decides the exact price, volume, delivery, payment within our constraints.
        """
        resistance = self._get_resistance_price()
        tactic = tactic_decision.get("tactic", "push_moderate")
        use_tradeoff = tactic_decision.get("use_tradeoff", False)

        # Compute a mathematically-suggested price as anchor for the LLM
        suggested_price = self._compute_suggested_price(
            tactic=tactic,
            my_last_offer=my_last_offer,
            counterparty_last_offer=counterparty_last_offer,
            aspiration_state=aspiration_state,
            risk_assessment=risk_assessment,
        )

        # Current defaults for non-price attributes
        current_volume = my_last_offer.volume if my_last_offer else (
            counterparty_last_offer.volume if counterparty_last_offer else (self.limits.min_volume or 100)
        )
        current_delivery = my_last_offer.delivery_days if my_last_offer else (
            counterparty_last_offer.delivery_days if counterparty_last_offer else 14
        )
        current_payment = my_last_offer.payment_terms if my_last_offer else (
            counterparty_last_offer.payment_terms if counterparty_last_offer else "Net 30"
        )

        # Acceptable payment terms
        acceptable_terms = self.limits.acceptable_payment_terms or ["Net 30", "Net 45", "Net 60"]
        if not acceptable_terms:
            acceptable_terms = ["Net 30", "Net 45", "Net 60"]

        tradeoff_instruction = ""
        if use_tradeoff and tradeoff_analysis.best_proposal:
            bp = tradeoff_analysis.best_proposal
            tradeoff_instruction = f"""
TRADE-OFF OPTION (use if beneficial):
{bp.proposal_text}
Consider including this trade-off in your offer for a multi-attribute package deal.
"""

        prompt = f"""You are generating a concrete offer for a B2B negotiation.

ROLE: {'Supplier' if self.is_supplier else 'Retailer'} for {self.product_name}
ROUND: {current_round}
TACTIC: {tactic} — {tactic_decision.get('reasoning', '')}

=== MY HARD CONSTRAINTS (NEVER VIOLATE) ===
{'Minimum price: €' + str(resistance) if self.is_supplier else 'Maximum price: €' + str(resistance)}
{'Minimum volume: ' + str(self.limits.min_volume) if self.limits.min_volume else ''}
{'Maximum volume: ' + str(self.limits.max_volume) if self.limits.max_volume else ''}
{'Maximum delivery days: ' + str(self.limits.max_delivery_days) if self.limits.max_delivery_days else ''}
Acceptable payment terms: {', '.join(acceptable_terms)}

=== CURRENT STATE ===
My last offer: {f"€{my_last_offer.unit_price:.2f} × {my_last_offer.volume} units, {my_last_offer.delivery_days}d, {my_last_offer.payment_terms}" if my_last_offer else "No previous offer (opening round)"}
Opponent's offer: {f"€{counterparty_last_offer.unit_price:.2f} × {counterparty_last_offer.volume} units, {counterparty_last_offer.delivery_days}d, {counterparty_last_offer.payment_terms}" if counterparty_last_offer else "None yet"}

=== GUIDANCE ===
Target (aspiration): €{aspiration_state.current_aspiration:.2f}
Walk-away limit: €{resistance:.2f}
Suggested price: €{suggested_price:.2f} (mathematical guidance, can adjust ±5%)
{tradeoff_instruction}

Tactic guidance:
- push_aggressive: Stay very close to aspiration or above, minimal concession
- push_moderate: Move 30-50% toward opponent from your position
- hold_firm: Keep exact same price as last round (change only non-price attributes if needed)
- concede_small: Move 5-15% of gap toward opponent
- concede_meaningful: Move 20-40% of gap toward opponent
- logrolling/trade-off: Adjust non-price attributes while keeping price firm
- walk_away_signal: Hint at walking away (keep price very firm)

Generate the offer. Output ONLY valid JSON:
{{
    "unit_price": <number: the offer price, MUST respect hard constraints>,
    "volume": <number: order quantity>,
    "delivery_days": <number: lead time in days>,
    "payment_terms": <string: one of the acceptable terms>,
    "price_rationale": "<one sentence: why this specific price>"
}}"""

        try:
            response = self.llm_client.generate_text(prompt, max_tokens=300)
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                # Enforce hard constraints
                data["unit_price"] = self._enforce_price_constraint(data.get("unit_price", suggested_price))
                return data
        except Exception as e:
            logger.warning(f"LLM offer generation failed: {e}")

        # Fallback
        return {
            "unit_price": suggested_price,
            "volume": current_volume,
            "delivery_days": current_delivery,
            "payment_terms": current_payment,
            "price_rationale": f"Mathematical guidance: {tactic}",
        }

    # ═══════════════════════════════════════════════════════════════════════
    # LLM CALL 4: JUSTIFICATION / NEGOTIATION LANGUAGE
    # ═══════════════════════════════════════════════════════════════════════

    def _llm_generate_justification(
        self,
        offer_data: dict,
        tactic_decision: dict,
        situation_analysis: dict,
        counterparty_last_offer: Optional[NegotiationOffer],
    ) -> str:
        """
        LLM Call 4: Generate professional negotiation language.
        The justification the counterparty sees and the LLM agent on the other side reads.
        """
        tactic = tactic_decision.get("tactic", "push_moderate")
        sentiment_guide = {
            "push_aggressive": "firm and confident, state your position clearly, reference your strong alternatives",
            "push_moderate": "professional and value-focused, highlight what you bring to the deal",
            "hold_firm": "principled and calm, explain why this is your fair position",
            "concede_small": "constructive and collaborative, signal good faith while noting limits",
            "concede_meaningful": "partnership-oriented, emphasize willingness to find mutual benefit",
            "logrolling": "creative and problem-solving, propose the package deal clearly",
            "walk_away_signal": "measured but serious, mention alternatives without threatening aggressively",
            "accept": "positive and decisive, confirm the deal clearly",
        }

        role_context = "supplier perspective (selling, protecting margin and volume)" if self.is_supplier else "retailer perspective (buying, optimizing cost and supply reliability)"

        prompt = f"""You are writing the negotiation message for a {role_context}.

TACTIC: {tactic}
TONE: {sentiment_guide.get(tactic, 'professional')}
PRODUCT: {self.product_name}

OUR OFFER:
- Price: €{offer_data.get('unit_price', 0):.2f} per unit
- Volume: {offer_data.get('volume', 0)} units
- Delivery: {offer_data.get('delivery_days', 0)} days
- Payment: {offer_data.get('payment_terms', 'Net 30')}

OPPONENT'S LAST OFFER: {f"€{counterparty_last_offer.unit_price:.2f}" if counterparty_last_offer else "None"}
SITUATION: {situation_analysis.get('situation_summary', '')}

Write a professional business negotiation message (2-4 sentences) that:
1. Briefly acknowledges the opponent's position (where relevant)
2. Presents our offer with clear business rationale
3. Uses the appropriate tone for the {tactic} tactic
4. Sounds like a real procurement/sales professional, NOT a script

Write ONLY the message text, no labels or JSON:"""

        try:
            response = self.llm_client.generate_text(prompt, max_tokens=200)
            return response.strip()
        except Exception as e:
            logger.warning(f"LLM justification failed: {e}")

        # Fallback
        price = offer_data.get("unit_price", 0)
        return (
            f"Based on current market conditions and our supply chain analysis, "
            f"we are offering €{price:.2f} per unit. "
            f"This reflects our commitment to a sustainable long-term partnership."
        )

    # ═══════════════════════════════════════════════════════════════════════
    # ACCEPTANCE CHECK
    # ═══════════════════════════════════════════════════════════════════════

    def _check_acceptance(
        self,
        counterparty_last_offer: Optional[NegotiationOffer],
        aspiration_state: AspirationState,
        risk_assessment: RiskAssessment,
        current_round: int,
    ) -> tuple[bool, str]:
        """
        Determine whether to accept the counterparty's offer.
        Uses AspirationManager + risk assessment, not simple threshold.
        """
        if counterparty_last_offer is None:
            return False, "No counterparty offer to accept"

        # Min rounds: never accept before round 4 (anchoring phase must complete)
        min_rounds = max(4, int(2 / max(self.personality.patience, 0.1)))

        return self._aspiration_manager.should_accept(
            opponent_offer_price=counterparty_last_offer.unit_price,
            aspiration_state=aspiration_state,
            risk_reward_ratio=risk_assessment.risk_reward_ratio,
            current_round=current_round,
            min_rounds_before_accept=min_rounds,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # OFFER BUILDING + CONSTRAINT ENFORCEMENT
    # ═══════════════════════════════════════════════════════════════════════

    def _compute_suggested_price(
        self,
        tactic: str,
        my_last_offer: Optional[NegotiationOffer],
        counterparty_last_offer: Optional[NegotiationOffer],
        aspiration_state: AspirationState,
        risk_assessment: RiskAssessment,
    ) -> float:
        """
        Compute a mathematically-grounded suggested price for the LLM.
        The LLM may adjust ±5% but this anchors the calculation.
        """
        resistance = self._get_resistance_price()
        aspiration = aspiration_state.current_aspiration

        # Opening round: use opening target
        if my_last_offer is None:
            return aspiration

        my_price = my_last_offer.unit_price
        opp_price = counterparty_last_offer.unit_price if counterparty_last_offer else my_price

        # Price gap (direction-aware)
        if self.is_supplier:
            gap = opp_price - my_price  # Negative = we're above opponent
        else:
            gap = my_price - opp_price  # Positive = we're above opponent

        if tactic == "push_aggressive":
            # Stay very close to aspiration
            return round(aspiration + (my_price - aspiration) * 0.1, 2) if self.is_supplier else round(aspiration + (my_price - aspiration) * 0.1, 2)

        elif tactic in ("push_moderate",):
            # Move 40% toward opponent from aspiration direction
            concession = self._aspiration_manager.calculate_concession_size(
                aspiration_state=aspiration_state,
                opponent_stubbornness=self._opponent_model.stubbornness_score,
                opponent_cooperation=self._opponent_model.cooperation_score,
                time_pressure=risk_assessment.time_pressure,
                risk_reward_ratio=risk_assessment.risk_reward_ratio,
                my_current_price=my_price,
            )
            if self.is_supplier:
                return round(max(resistance, my_price - concession * 0.5), 2)
            else:
                return round(min(resistance, my_price + concession * 0.5), 2)

        elif tactic == "hold_firm":
            return my_price

        elif tactic == "concede_small":
            concession = self._aspiration_manager.calculate_concession_size(
                aspiration_state=aspiration_state,
                opponent_stubbornness=self._opponent_model.stubbornness_score,
                opponent_cooperation=self._opponent_model.cooperation_score,
                time_pressure=risk_assessment.time_pressure,
                risk_reward_ratio=risk_assessment.risk_reward_ratio,
                my_current_price=my_price,
            )
            small = concession * 0.4  # 40% of calculated concession
            if self.is_supplier:
                return round(max(resistance, my_price - small), 2)
            else:
                return round(min(resistance, my_price + small), 2)

        elif tactic == "concede_meaningful":
            concession = self._aspiration_manager.calculate_concession_size(
                aspiration_state=aspiration_state,
                opponent_stubbornness=self._opponent_model.stubbornness_score,
                opponent_cooperation=self._opponent_model.cooperation_score,
                time_pressure=risk_assessment.time_pressure,
                risk_reward_ratio=risk_assessment.risk_reward_ratio,
                my_current_price=my_price,
            )
            if self.is_supplier:
                return round(max(resistance, my_price - concession), 2)
            else:
                return round(min(resistance, my_price + concession), 2)

        elif tactic in ("walk_away_signal",):
            # Hold near current position or slightly toward aspiration
            if self.is_supplier:
                return round(max(resistance, min(my_price + 0.50, aspiration)), 2)
            else:
                return round(min(resistance, max(my_price - 0.50, aspiration)), 2)

        elif tactic == "accept":
            return opp_price

        else:
            # Default: moderate concession
            if self.is_supplier:
                return round(max(resistance, my_price - 1.0), 2)
            else:
                return round(min(resistance, my_price + 1.0), 2)

    def _enforce_price_constraint(self, price: float) -> float:
        """Ensure price never violates hard constraints."""
        resistance = self._get_resistance_price()
        if self.is_supplier:
            return max(resistance, price)
        else:
            return min(resistance, price)

    def _build_offer(
        self,
        offer_data: dict,
        justification: str,
        my_last_offer: Optional[NegotiationOffer],
        counterparty_last_offer: Optional[NegotiationOffer],
        aspiration_state: AspirationState,
    ) -> NegotiationOffer:
        """Build the final NegotiationOffer, enforcing all constraints."""
        unit_price = self._enforce_price_constraint(
            float(offer_data.get("unit_price", aspiration_state.current_aspiration))
        )

        # Volume: respect limits
        volume = int(offer_data.get("volume", (
            my_last_offer.volume if my_last_offer else
            (counterparty_last_offer.volume if counterparty_last_offer else
             (self.limits.min_volume or 100))
        )))
        if self.limits.min_volume:
            volume = max(self.limits.min_volume, volume)
        if self.limits.max_volume:
            volume = min(self.limits.max_volume, volume)

        # Delivery days
        delivery_days = int(offer_data.get("delivery_days", (
            my_last_offer.delivery_days if my_last_offer else
            (counterparty_last_offer.delivery_days if counterparty_last_offer else 14)
        )))
        if self.limits.max_delivery_days:
            delivery_days = min(self.limits.max_delivery_days, delivery_days)
        delivery_days = max(1, delivery_days)

        # Payment terms
        payment_terms = str(offer_data.get("payment_terms", (
            my_last_offer.payment_terms if my_last_offer else
            (counterparty_last_offer.payment_terms if counterparty_last_offer else "Net 30")
        )))
        acceptable = self.limits.acceptable_payment_terms
        if acceptable and payment_terms not in acceptable:
            payment_terms = acceptable[0]

        return NegotiationOffer(
            unit_price=round(unit_price, 2),
            volume=volume,
            delivery_days=delivery_days,
            payment_terms=payment_terms,
            justification=justification,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # AGENT REASONING (transparency)
    # ═══════════════════════════════════════════════════════════════════════

    def _build_reasoning(
        self,
        strategy: str,
        tactic: str,
        offer_data: dict,
        my_last_offer: Optional[NegotiationOffer],
        counterparty_last_offer: Optional[NegotiationOffer],
        phase: NegotiationPhase,
        risk_assessment: RiskAssessment,
        aspiration_state: AspirationState,
        summary: str,
    ) -> AgentReasoning:
        """Build AgentReasoning for frontend transparency."""

        # Concession calculation
        concession_eur = None
        concession_pct = None
        if my_last_offer:
            delta = offer_data.get("unit_price", 0) - my_last_offer.unit_price
            concession_eur = round(delta, 2)
            if abs(my_last_offer.unit_price) > 0.01:
                concession_pct = round(abs(delta) / my_last_offer.unit_price * 100, 2)

        # Gap remaining
        gap_remaining = None
        if counterparty_last_offer and offer_data.get("unit_price"):
            gap_remaining = round(
                abs(float(offer_data["unit_price"]) - counterparty_last_offer.unit_price), 2
            )

        # Convergence progress
        convergence = 0.0
        if gap_remaining is not None and my_last_offer and counterparty_last_offer:
            initial_gap = abs(
                (my_last_offer.unit_price if my_last_offer else offer_data.get("unit_price", 0))
                - counterparty_last_offer.unit_price
            )
            if initial_gap > 0.01:
                convergence = min(100.0, max(0.0, (1 - gap_remaining / initial_gap) * 100))

        # Utility approximation
        resistance = self._get_resistance_price()
        total_range = abs(aspiration_state.original_target - resistance)
        if total_range > 0.01 and offer_data.get("unit_price"):
            price = float(offer_data["unit_price"])
            if self.is_supplier:
                own_utility = min(1.0, max(0.0, (price - resistance) / total_range))
            else:
                own_utility = min(1.0, max(0.0, (resistance - price) / total_range))
        else:
            own_utility = 0.5

        # Reasoning steps
        steps = [
            ReasoningStep(
                phase="THINK",
                observation=(
                    f"Phase: {phase.value}. "
                    f"Opponent: {self._opponent_model.opponent_type if self._opponent_model else 'unknown'}. "
                    f"Gap: €{gap_remaining:.2f}" if gap_remaining else f"Phase: {phase.value}"
                ),
                reasoning=f"Aspiration at €{aspiration_state.current_aspiration:.2f}, confidence: {aspiration_state.confidence.value}",
                conclusion=f"Aspiration gap: €{aspiration_state.aspiration_gap:.2f}",
            ),
            ReasoningStep(
                phase="STRATEGIZE",
                observation=f"Risk-reward: {risk_assessment.risk_reward_ratio:.2f}, walk-away risk: {risk_assessment.walk_away_probability:.0%}",
                reasoning=risk_assessment.reasoning,
                conclusion=f"Recommendation: {risk_assessment.recommendation.value}",
            ),
            ReasoningStep(
                phase="CALCULATE",
                observation=f"Tactic selected: {tactic}",
                reasoning=f"Suggested price: €{offer_data.get('unit_price', 0):.2f}",
                conclusion=f"Concession: €{concession_eur:.2f}" if concession_eur else "Opening offer",
            ),
        ]

        return AgentReasoning(
            strategy_used=strategy,
            tactic=tactic,
            own_utility=round(own_utility, 3),
            estimated_counterparty_utility=None,
            concession_amount_eur=concession_eur,
            concession_percentage=concession_pct,
            convergence_progress=round(convergence, 1),
            gap_remaining_eur=gap_remaining,
            leverage_used=self._opponent_model.get_most_flexible_attribute() if self._opponent_model else None,
            context_factors=[
                phase.value,
                risk_assessment.recommendation.value,
                f"walk_away_{int(risk_assessment.walk_away_probability * 100)}pct",
                f"aspiration_confidence_{aspiration_state.confidence.value}",
            ],
            reasoning_steps=steps,
            summary=summary,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════════

    def _get_my_last_offer(
        self, history: list[NegotiationRound]
    ) -> Optional[NegotiationOffer]:
        """Get our most recent offer from history."""
        for round_info in reversed(history):
            if round_info.role == self.role:
                return round_info.offer
        return None

    def _get_strategy_name(self, phase: NegotiationPhase) -> str:
        """Map phase to strategy name for AgentReasoning."""
        mapping = {
            NegotiationPhase.ANCHORING: "ANCHORING",
            NegotiationPhase.EXPLORING: "EXPLORING",
            NegotiationPhase.BARGAINING: "ADAPTIVE_BARGAINING",
            NegotiationPhase.LOGROLLING: "LOGROLLING",
            NegotiationPhase.CLOSING: "CLOSING",
            NegotiationPhase.DEADLOCK: "DEADLOCK_BREAKING",
        }
        return mapping.get(phase, "ADAPTIVE")