"""
agents/simple_agent.py
─────────────────────
Strategic negotiation agent — Agentic 2.0 (ZOPA-free design).

Design Principle — No Shared ZOPA:
───────────────────────────────────
Each agent knows ONLY its own constraints (PartyLimits). There is no shared
"Zone of Possible Agreement" passed between agents — exactly as in real
human-to-human negotiation. The counterparty's limits are unknown.

Acceptance, anchoring, and concessions are driven by:
  - Own PartyLimits (floor / ceiling)
  - Utility of the incoming offer against own preferences
  - Opponent model inference from observed behaviour
  - Time pressure (rounds remaining / phase)

Theoretical Foundation:
───────────────────────
- ReAct Pattern (Yao 2023): Thought → Action → Observation
- Multi-Attribute Utility (O'Brien 2024, Fujita)
- Concession Strategies (Okunev 2022, Monczka 2009)
- Opponent Modeling (Hindriks & Tykhonov 2008)
- Phase Theory (Gulliver 1979, Adair & Brett 2005)
"""

import json
import logging
from typing import Optional, Tuple

from llm.ai_core_client import AICoreClient
from models.negotiation_models import (
    AgentRole,
    NegotiationOffer,
    PartyLimits,
    NegotiationRound,
)
from models.agent_reasoning import AgentReasoning, ReasoningStep

logger = logging.getLogger(__name__)

# ── Strategic modules (graceful fallback if unavailable) ──────────────────────
try:
    from models.utility import (
        NegotiationPreferences,
        calculate_utility,
    )
    from models.constraints import convert_limits_to_preferences
    from agents.strategy import (
        NegotiationStrategy,
        NegotiationPhase,
        TacticType,
        ConcessionPattern,
        LeverageType,
        NegotiationPersonality,
        SUPPLIER_DEFAULT_STRATEGY,
        RETAILER_DEFAULT_STRATEGY,
        calculate_concession_amount,
        calculate_initial_anchor,
        select_leverage,
        should_make_tradeoff,
        detect_phase,
    )
    STRATEGIC_MODE_AVAILABLE = True
except ImportError as _e:
    STRATEGIC_MODE_AVAILABLE = False
    logger.warning(f"Strategic mode not available — falling back to basic: {_e}")

try:
    from agents.opponent_model import OpponentModel
    OPPONENT_MODEL_AVAILABLE = True
except ImportError:
    OPPONENT_MODEL_AVAILABLE = False
    logger.warning("OpponentModel not available")


class NegotiationAgent:
    """
    Adaptive B2B negotiation agent with opponent modeling and autonomous acceptance.

    Each agent operates from its own limits only — it never receives the
    counterparty's min/max prices. Convergence emerges through the natural
    process of mutual concessions, mirroring real-world negotiations.
    """

    def __init__(
        self,
        role: AgentRole,
        llm_client: AICoreClient,
        limits: PartyLimits,
        product_name: str,
        preferences: Optional["NegotiationPreferences"] = None,
        strategy: Optional["NegotiationStrategy"] = None,
        product_data: Optional[dict] = None,
        personality_seed: Optional[int] = None,
    ):
        self.role = role
        self.llm_client = llm_client
        self.limits = limits
        self.product_name = product_name
        self.product_data = product_data

        if STRATEGIC_MODE_AVAILABLE:
            self.preferences = preferences or convert_limits_to_preferences(
                limits, is_supplier=(role == AgentRole.SUPPLIER)
            )
            base = (
                SUPPLIER_DEFAULT_STRATEGY
                if role == AgentRole.SUPPLIER
                else RETAILER_DEFAULT_STRATEGY
            )
            self._personality = NegotiationPersonality(
                strategy if strategy is not None else base,
                seed=personality_seed,
            )
            self.strategy = self._personality.strategy
        else:
            self.preferences = None
            self.strategy = None
            self._personality = None

        self.opponent_model = OpponentModel(my_role=role) if OPPONENT_MODEL_AVAILABLE else None
        self._consecutive_no_progress = 0

    # ─────────────────────────────────────────────────────────────────────────
    # Own-limits helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _own_floor(self) -> float:
        """The price below which this agent will NEVER go."""
        if self.role == AgentRole.SUPPLIER:
            return self.limits.min_price or 0.0
        else:
            return 0.0  # Retailer has no floor (they want low prices)

    def _own_ceiling(self) -> float:
        """The price above which this agent will NEVER go."""
        if self.role == AgentRole.RETAILER:
            return self.limits.max_price or float("inf")
        else:
            return float("inf")  # Supplier has no ceiling (they want high prices)

    def _own_range(self) -> float:
        """Width of this agent's own price range (for gap-% calculations)."""
        if self.role == AgentRole.SUPPLIER:
            lo = self.limits.min_price or 0.0
            hi = (
                self.limits.max_price
                or (lo * 1.40)  # assume 40% upside if no explicit max
            )
            return max(hi - lo, 1.0)
        else:
            hi = self.limits.max_price or 1.0
            lo = hi * 0.60  # assume retailer wants ~40% below their ceiling
            return max(hi - lo, 1.0)

    def _own_opening_anchor(self) -> float:
        """
        Round-1 anchor based solely on own limits.

        Supplier starts high (near/at own max_price or well above min_price).
        Retailer starts low (well below own max_price).
        """
        if self.role == AgentRole.SUPPLIER:
            if self.limits.max_price:
                return self.limits.max_price * 0.97
            elif self.limits.min_price:
                return self.limits.min_price * 1.35
            return 100.0
        else:
            if self.limits.max_price:
                return self.limits.max_price * 0.65
            return 50.0

    def _price_within_own_limits(self, price: float) -> bool:
        if self.role == AgentRole.SUPPLIER:
            return price >= self._own_floor()
        else:
            return price <= self._own_ceiling()

    # ─────────────────────────────────────────────────────────────────────────
    # ACCEPTANCE CHECK
    # ─────────────────────────────────────────────────────────────────────────

    def should_accept_offer(
        self,
        counterparty_offer: NegotiationOffer,
        current_round: int,
        max_rounds: int,
        history: list,
    ) -> Tuple[bool, str]:
        """
        Decide autonomously whether to accept the counterparty's offer.

        Only own limits are consulted — no shared ZOPA knowledge.

        Criteria (all must hold):
        1. Price satisfies own hard limit
        2. Not too early in negotiation (round ≥ 4)
        3. Phase is at least BARGAINING
        4. Utility exceeds acceptance threshold (adjusted for urgency)
        """
        price = counterparty_offer.unit_price

        # ── 1. Own hard-limit check ────────────────────────────────────────
        if not self._price_within_own_limits(price):
            limit_val = self._own_floor() if self.role == AgentRole.SUPPLIER else self._own_ceiling()
            direction = "below floor" if self.role == AgentRole.SUPPLIER else "above ceiling"
            return False, f"Price €{price:.2f} {direction} (limit=€{limit_val:.2f})"

        # ── 2. Minimum-rounds guard ────────────────────────────────────────
        if current_round < 4:
            return False, f"Too early (round {current_round} < 4) — establishing position"

        # ── 3. Phase guard ─────────────────────────────────────────────────
        if STRATEGIC_MODE_AVAILABLE:
            phase = detect_phase(current_round, max_rounds)
            if phase in (NegotiationPhase.OPENING, NegotiationPhase.EXPLORING):
                return False, f"Phase {phase.value} — still exploring"

        # ── 4. Utility check (adjusted for urgency) ────────────────────────
        utility_score = 0.5
        rounds_remaining = max_rounds - current_round
        base_threshold = 0.72
        urgency_bonus = max(0.0, (5 - rounds_remaining) * 0.03)
        acceptance_utility = max(0.50, base_threshold - urgency_bonus)

        if STRATEGIC_MODE_AVAILABLE and self.preferences:
            try:
                res = calculate_utility(
                    preferences=self.preferences,
                    price=price,
                    volume=counterparty_offer.volume,
                    delivery_days=counterparty_offer.delivery_days,
                    payment_terms=counterparty_offer.payment_terms,
                )
                utility_score = res.total_utility
            except Exception:
                pass

        # ── 5. Opponent-model signal ───────────────────────────────────────
        opponent_stuck = (
            self.opponent_model
            and self.opponent_model.get_rounds_without_concession() >= 3
        )

        price_ok = self._price_within_own_limits(price)
        utility_ok = utility_score >= acceptance_utility
        closing_phase = STRATEGIC_MODE_AVAILABLE and detect_phase(current_round, max_rounds) == NegotiationPhase.CLOSING

        if (utility_ok and price_ok) or (closing_phase and price_ok and utility_score >= 0.50) or opponent_stuck:
            return True, (
                f"Accepting: utility={utility_score:.2f}≥{acceptance_utility:.2f}, "
                f"price=€{price:.2f}"
            )

        return False, (
            f"Not accepting: utility={utility_score:.2f}<{acceptance_utility:.2f}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1: THINK
    # ─────────────────────────────────────────────────────────────────────────

    def _analyze_situation(
        self,
        current_round: int,
        max_rounds: int,
        history: list,
        counterparty_last_offer: Optional[NegotiationOffer],
    ) -> dict:
        """Analyze negotiation situation using only own-limits knowledge."""
        analysis = {
            "current_price_gap": 0.0,
            "gap_percentage": 0.0,
            "rounds_remaining": max_rounds - current_round,
            "counterparty_concession": None,
            "is_stuck": False,
            "urgency_level": "low",
            "phase": "opening",
        }

        if STRATEGIC_MODE_AVAILABLE:
            analysis["phase"] = detect_phase(current_round, max_rounds).value

        if not counterparty_last_offer:
            return analysis

        my_rounds = [r for r in history if r.role == self.role]
        my_last_price = my_rounds[-1].offer.unit_price if my_rounds else None

        if my_last_price is not None:
            analysis["current_price_gap"] = abs(my_last_price - counterparty_last_offer.unit_price)
            own_range = self._own_range()
            analysis["gap_percentage"] = (analysis["current_price_gap"] / own_range) * 100

        counterparty_rounds = [r for r in history if r.role != self.role]
        if len(counterparty_rounds) >= 2:
            prev = counterparty_rounds[-2].offer.unit_price
            curr = counterparty_rounds[-1].offer.unit_price
            analysis["counterparty_concession"] = (
                prev - curr if self.role == AgentRole.SUPPLIER else curr - prev
            )
            if abs(curr - prev) < 0.10:
                analysis["is_stuck"] = True
                self._consecutive_no_progress += 1
            else:
                self._consecutive_no_progress = 0

        rem = analysis["rounds_remaining"]
        if rem <= 2:
            analysis["urgency_level"] = "critical"
        elif rem <= 4:
            analysis["urgency_level"] = "high"
        elif rem <= 6:
            analysis["urgency_level"] = "medium"

        return analysis

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2: STRATEGIZE — LLM selects tactic
    # ─────────────────────────────────────────────────────────────────────────

    def _select_tactic_with_llm(
        self,
        analysis: dict,
        current_round: int,
        max_rounds: int,
        counterparty_last_offer: Optional[NegotiationOffer],
    ) -> dict:
        """LLM decides which tactic to use based on situation analysis."""
        role_name = "supplier" if self.role == AgentRole.SUPPLIER else "retailer"

        opponent_context = (
            f"\n\nOPPONENT ANALYSIS:\n{self.opponent_model.to_prompt_context()}"
            if self.opponent_model else ""
        )
        personality_hint = (
            f"\n\nYOUR PERSONALITY THIS SESSION:\n{self._personality.to_prompt_hint()}"
            if self._personality else ""
        )
        counterparty_price_line = (
            f"\nCounterparty's latest offer: €{counterparty_last_offer.unit_price:.2f} | "
            f"{counterparty_last_offer.volume} units | {counterparty_last_offer.delivery_days}d | "
            f"{counterparty_last_offer.payment_terms}"
            if counterparty_last_offer else ""
        )

        own_limit_line = (
            f"\nYour hard floor: €{self._own_floor():.2f}"
            if self.role == AgentRole.SUPPLIER
            else f"\nYour hard ceiling: €{self._own_ceiling():.2f}"
        )

        prompt = f"""You are a strategic {role_name} in a B2B negotiation for: {self.product_name}

SITUATION:
- Round: {current_round}/{max_rounds} (Phase: {analysis.get('phase','bargaining').upper()})
- Price gap: €{analysis['current_price_gap']:.2f} ({analysis['gap_percentage']:.1f}% of your range)
- Rounds remaining: {analysis['rounds_remaining']}
- Urgency: {analysis['urgency_level']}
- Negotiation stuck: {analysis['is_stuck']}{own_limit_line}{counterparty_price_line}{opponent_context}{personality_hint}

AVAILABLE TACTICS (choose exactly ONE):
- concede: Make a price concession (standard move)
- hold_firm: Repeat your last offer, no change (signal you're near limit)
- tradeoff: Change a non-price attribute (delivery speed, payment terms) instead of price
- conditional: Offer a deal contingent on a condition (e.g., "If volume is 2000, I'll match €46")
- split_difference: Propose splitting the remaining gap exactly in half
- final_offer: Signal this is your last possible offer (use sparingly, max once)
- walk_away_threat: Signal you have alternatives / BATNA (use very sparingly)
- creative_bundle: Propose an entirely new package (different product mix, terms)

OUTPUT ONLY valid JSON (no markdown, no explanation):
{{
  "tactic": "<one of the tactic names above>",
  "tactic_reason": "<one sentence why>",
  "use_leverage": "<volume|timing|relationship|alternatives|quality|market|null>",
  "propose_tradeoff": <true|false>,
  "recommended_action": "<concede|hold|accept|reject|tradeoff>"
}}"""

        default = {
            "tactic": "concede",
            "tactic_reason": "Standard concession",
            "use_leverage": None,
            "propose_tradeoff": False,
            "recommended_action": "concede",
        }

        try:
            raw = self.llm_client.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            ).strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            parsed = json.loads(raw)

            valid_tactics = {
                "concede", "hold_firm", "tradeoff", "conditional",
                "split_difference", "final_offer", "walk_away_threat", "creative_bundle"
            }
            if parsed.get("tactic") not in valid_tactics:
                parsed["tactic"] = "concede"
            valid_leverages = {"volume", "timing", "relationship", "alternatives", "quality", "market"}
            if parsed.get("use_leverage") not in valid_leverages:
                parsed["use_leverage"] = None
            return parsed

        except Exception as e:
            logger.warning(f"[{self.role.value}] LLM tactic failed: {e}")
            if analysis["urgency_level"] == "critical" and analysis["gap_percentage"] < 10.0:
                default["tactic"] = "split_difference"
            elif analysis["is_stuck"] and self._consecutive_no_progress >= 3:
                default["tactic"] = "tradeoff"
                default["propose_tradeoff"] = True
            if self.opponent_model:
                if self.opponent_model.last_sentiment == "threatening":
                    default["tactic"] = "conditional"
                elif self.opponent_model.opponent_type == "boulware":
                    default["tactic"] = "split_difference"
            return default

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3: CALCULATE — Compute offer parameters
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_offer_params(
        self,
        tactic: dict,
        counterparty_last_offer: Optional[NegotiationOffer],
        current_round: int,
        history: Optional[list] = None,
        analysis: Optional[dict] = None,
        max_rounds: int = 50,
    ) -> dict:
        """
        Compute concrete offer parameters based only on own limits.
        No ZOPA passed or used.
        """
        params = {}
        analysis = analysis or {}

        # ── Price ──────────────────────────────────────────────────────────
        if current_round == 1:
            # Round-1: anchor based on own limits + strategy
            if STRATEGIC_MODE_AVAILABLE and self.strategy and self.preferences:
                target = self.preferences.target_price or self._own_opening_anchor()
                raw_anchor = calculate_initial_anchor(
                    target_price=target,
                    strategy=self.strategy,
                    is_supplier=(self.role == AgentRole.SUPPLIER),
                )
                if self._personality:
                    raw_anchor = target + (raw_anchor - target) * (
                        2.0 - self._personality.get_opening_concession_modifier()
                    )
                params["unit_price"] = raw_anchor
            else:
                params["unit_price"] = self._own_opening_anchor()
        else:
            tactic_type = tactic.get("tactic", "concede")

            # My last price
            my_last_price = None
            if history:
                my_rounds = [r for r in history if r.role == self.role]
                if my_rounds:
                    my_last_price = my_rounds[-1].offer.unit_price

            # Fallback: start from own best-case position
            if my_last_price is None:
                my_last_price = self._own_opening_anchor()

            current_gap = analysis.get(
                "current_price_gap",
                abs(my_last_price - (counterparty_last_offer.unit_price if counterparty_last_offer else my_last_price)),
            )

            if tactic_type == "hold_firm":
                params["unit_price"] = my_last_price

            elif tactic_type == "split_difference" and counterparty_last_offer:
                params["unit_price"] = (my_last_price + counterparty_last_offer.unit_price) / 2

            elif tactic_type == "final_offer":
                small = min(0.50, current_gap * 0.05)
                params["unit_price"] = (
                    my_last_price - small
                    if self.role == AgentRole.SUPPLIER
                    else my_last_price + small
                )

            elif tactic_type == "tradeoff":
                params["unit_price"] = my_last_price
                params["_tradeoff_active"] = True

            elif tactic_type in ("walk_away_threat", "conditional"):
                concession = (
                    calculate_concession_amount(
                        strategy=self.strategy,
                        round_number=current_round,
                        max_rounds=max_rounds,
                        current_gap=current_gap,
                        counterparty_last_concession=analysis.get("counterparty_concession"),
                    )
                    if STRATEGIC_MODE_AVAILABLE and self.strategy
                    else current_gap * 0.05
                )
                if self.role == AgentRole.SUPPLIER:
                    params["unit_price"] = my_last_price - concession * 0.3
                else:
                    params["unit_price"] = my_last_price + concession * 0.3

            else:
                # Standard concede
                if STRATEGIC_MODE_AVAILABLE and self.strategy:
                    concession = calculate_concession_amount(
                        strategy=self.strategy,
                        round_number=current_round,
                        max_rounds=max_rounds,
                        current_gap=current_gap,
                        counterparty_last_concession=analysis.get("counterparty_concession"),
                    )
                else:
                    concession = current_gap * 0.15

                # Opponent-adaptive concession scaling
                if self.opponent_model:
                    if self.opponent_model.opponent_type == "boulware":
                        concession *= 0.7   # Tougher against tough opponents
                    elif self.opponent_model.opponent_type == "conceder":
                        concession *= 1.2   # More generous toward cooperative opponents

                if self.role == AgentRole.SUPPLIER:
                    params["unit_price"] = my_last_price - concession
                else:
                    params["unit_price"] = my_last_price + concession

        # ── Hard own-limit clamp (never go past own floor/ceiling) ─────────
        if self.role == AgentRole.SUPPLIER and self.limits.min_price:
            params["unit_price"] = max(params["unit_price"], self.limits.min_price)
        elif self.role == AgentRole.RETAILER and self.limits.max_price:
            params["unit_price"] = min(params["unit_price"], self.limits.max_price)

        params["unit_price"] = round(params["unit_price"], 2)

        # ── Data-driven defaults ───────────────────────────────────────────
        _pd = self.product_data or {}
        _lead = _pd.get("lead_time_days", 14)
        _pay = _pd.get("default_payment_terms", "Net 30")
        _min_vol = _pd.get("min_order_quantity") or self.limits.min_volume or 500
        _max_vol = _pd.get("max_monthly_capacity") or self.limits.max_volume or (_min_vol * 4)

        params["volume"] = (
            counterparty_last_offer.volume
            if counterparty_last_offer
            else (_min_vol + _max_vol) // 2
        )
        params["delivery_days"] = (
            counterparty_last_offer.delivery_days
            if counterparty_last_offer
            else _lead
        )
        acceptable = self.limits.acceptable_payment_terms
        params["payment_terms"] = (
            counterparty_last_offer.payment_terms
            if counterparty_last_offer
            else (acceptable[0] if acceptable else _pay)
        )

        # ── Trade-off adjustments ──────────────────────────────────────────
        if tactic.get("propose_tradeoff") or params.get("_tradeoff_active"):
            if counterparty_last_offer:
                if self.role == AgentRole.SUPPLIER:
                    params["delivery_days"] = max(3, _lead - 3, params["delivery_days"] - 3)
                else:
                    params["payment_terms"] = "Net 60"
                    params["volume"] = min(
                        self.limits.max_volume or _max_vol,
                        int(params["volume"] * 1.10),
                    )

        return params

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4: GENERATE — LLM justification
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_justification(
        self,
        offer_params: dict,
        tactic: dict,
        analysis: dict,
        counterparty_last_offer: Optional[NegotiationOffer],
    ) -> Tuple[str, Optional[str]]:
        role_name = "supplier" if self.role == AgentRole.SUPPLIER else "retailer"
        tactic_type = tactic.get("tactic", "concede")

        opp_snippet = ""
        if self.opponent_model and self.opponent_model.opponent_type != "unknown":
            opp_snippet = (
                f"\nNote: Counterparty appears to be a "
                f"{self.opponent_model.opponent_type.upper()} negotiator "
                f"({self.opponent_model.last_sentiment} tone)."
            )

        prompt = f"""You are a professional B2B {role_name} justifying an offer for: {self.product_name}

YOUR OFFER:
- Price: €{offer_params['unit_price']:.2f}
- Volume: {offer_params['volume']} units
- Delivery: {offer_params['delivery_days']} days
- Payment: {offer_params['payment_terms']}

CONTEXT:
- Tactic: {tactic_type} — {tactic.get('tactic_reason', '')}
- Phase: {analysis.get('phase', 'bargaining').upper()}, urgency={analysis['urgency_level']}
- Gap remaining: €{analysis['current_price_gap']:.2f}{opp_snippet}"""

        if counterparty_last_offer:
            prompt += f"\nCounterparty's last: €{counterparty_last_offer.unit_price:.2f}"

        if tactic.get("use_leverage"):
            prompt += f"\n\nLEVERAGE to use: {tactic['use_leverage']} — weave it naturally."

        tactic_hints = {
            "hold_firm": "Explain firmly but professionally why you cannot move further on price.",
            "split_difference": "Propose splitting the difference as a fair compromise.",
            "final_offer": "Signal clearly this is your best offer.",
            "walk_away_threat": "Hint professionally that you have other options.",
            "conditional": "Frame the offer as conditional on a specific counterparty commitment.",
            "tradeoff": "Explain the non-price improvements you're offering.",
            "creative_bundle": "Propose a creatively restructured package.",
        }
        hint = tactic_hints.get(tactic_type, "")
        if hint:
            prompt += f"\n\nINSTRUCTION: {hint}"

        prompt += "\n\nWrite a professional, concise justification (2-3 sentences). Output ONLY the text."

        try:
            justification = self.llm_client.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.65,
            ).strip().strip('"')
            return justification, tactic.get("use_leverage")
        except Exception as e:
            logger.warning(f"Justification generation failed: {e}")
            return (
                f"Adjusted offer to €{offer_params['unit_price']:.2f} based on current market conditions.",
                None,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 5: VALIDATE
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_offer(self, offer: NegotiationOffer) -> Tuple[bool, str]:
        if self.role == AgentRole.SUPPLIER:
            if self.limits.min_price and offer.unit_price < self.limits.min_price:
                offer.unit_price = self.limits.min_price
                return True, f"Auto-corrected to min_price={self.limits.min_price:.2f}"
            if self.limits.min_volume and offer.volume < self.limits.min_volume:
                return False, f"Volume {offer.volume} below min {self.limits.min_volume}"
            if self.limits.max_volume and offer.volume > self.limits.max_volume:
                return False, f"Volume {offer.volume} exceeds capacity {self.limits.max_volume}"
        else:
            if self.limits.max_price and offer.unit_price > self.limits.max_price:
                offer.unit_price = self.limits.max_price
                return True, f"Auto-corrected to max_price={self.limits.max_price:.2f}"
            if self.limits.max_delivery_days and offer.delivery_days > self.limits.max_delivery_days:
                return False, f"Delivery {offer.delivery_days}d exceeds limit {self.limits.max_delivery_days}d"

        if self.limits.acceptable_payment_terms:
            if offer.payment_terms not in self.limits.acceptable_payment_terms:
                offer.payment_terms = self.limits.acceptable_payment_terms[0]

        return True, "Valid"

    # ─────────────────────────────────────────────────────────────────────────
    # BUILD REASONING
    # ─────────────────────────────────────────────────────────────────────────

    def _build_reasoning(
        self,
        analysis: dict,
        tactic: dict,
        offer: NegotiationOffer,
    ) -> AgentReasoning:
        """Build AgentReasoning using own-limits knowledge only."""
        own_utility = 0.5
        if STRATEGIC_MODE_AVAILABLE and self.preferences:
            try:
                res = calculate_utility(
                    preferences=self.preferences,
                    price=offer.unit_price,
                    volume=offer.volume,
                    delivery_days=offer.delivery_days,
                    payment_terms=offer.payment_terms,
                )
                own_utility = res.total_utility
            except Exception:
                pass

        # Convergence progress: how far has this agent come from its opening anchor
        # toward the counterparty? Measures own concession depth as % of own range.
        own_range = self._own_range()
        opening = self._own_opening_anchor()
        if own_range > 0:
            if self.role == AgentRole.SUPPLIER:
                # How far down from opening anchor (toward floor)?
                conceded = max(0.0, opening - offer.unit_price)
                max_possible = max(0.0, opening - self._own_floor())
                convergence_progress = min(100.0, (conceded / max(max_possible, 1.0)) * 100)
            else:
                # How far up from opening anchor (toward ceiling)?
                conceded = max(0.0, offer.unit_price - opening)
                max_possible = max(0.0, self._own_ceiling() - opening)
                convergence_progress = min(100.0, (conceded / max(max_possible, 1.0)) * 100)
        else:
            convergence_progress = 0.0

        strategy_name = (
            self.strategy.concession_pattern.value.upper()
            if self.strategy else "LINEAR"
        )
        tactic_name = tactic.get("tactic", "concede")

        opp_insight = (
            f" Opponent: {self.opponent_model.opponent_type}."
            if self.opponent_model and self.opponent_model.opponent_type != "unknown"
            else ""
        )

        reasoning_steps = [
            ReasoningStep(
                phase="THINK",
                observation=f"Gap: €{analysis['current_price_gap']:.2f} | {analysis['rounds_remaining']} rounds left | phase={analysis.get('phase', 'unknown')}",
                reasoning=f"Urgency={analysis['urgency_level']}, stuck={analysis['is_stuck']}{opp_insight}",
                conclusion=f"Phase-appropriate action: {analysis.get('phase', 'bargaining')}",
            ),
            ReasoningStep(
                phase="STRATEGIZE",
                observation=f"LLM selected tactic: {tactic_name}",
                reasoning=tactic.get("tactic_reason", "LLM-driven decision"),
                conclusion=f"Recommended action: {tactic.get('recommended_action', 'concede')}",
            ),
            ReasoningStep(
                phase="CALCULATE",
                observation=f"Price: €{offer.unit_price:.2f} | utility={own_utility:.2f}",
                reasoning=f"Calculation based on own limits + {strategy_name} strategy",
                conclusion="Offer parameters validated",
            ),
        ]

        context_factors = []
        if analysis["urgency_level"] in ("high", "critical"):
            context_factors.append("time_pressure")
        if analysis["is_stuck"]:
            context_factors.append("negotiation_stalled")
        if tactic.get("propose_tradeoff"):
            context_factors.append("multi_attribute_trade")
        if analysis["gap_percentage"] < 5:
            context_factors.append("near_agreement")

        return AgentReasoning(
            strategy_used=strategy_name,
            tactic=tactic_name,
            own_utility=own_utility,
            estimated_counterparty_utility=None,
            concession_amount_eur=tactic.get("concession_amount", 0.0) or 0.0,
            concession_percentage=(
                (tactic.get("concession_amount", 0.0) or 0.0) / analysis["current_price_gap"] * 100
                if analysis["current_price_gap"] > 0 else 0.0
            ),
            convergence_progress=convergence_progress,
            gap_remaining_eur=analysis["current_price_gap"],
            leverage_used=offer.leverage_used,
            context_factors=context_factors,
            reasoning_steps=reasoning_steps,
            summary=(
                f"[{strategy_name}] {tactic_name}: €{offer.unit_price:.2f} — "
                f"utility={own_utility:.2f}, gap=€{analysis['current_price_gap']:.2f}"
            ),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    def generate_counteroffer(
        self,
        current_round: int,
        history: list,
        counterparty_last_offer: Optional[NegotiationOffer],
        max_rounds: int = 50,
    ) -> Tuple[NegotiationOffer, AgentReasoning]:
        """
        Generate next counteroffer using 5-phase strategic process.

        No ZOPA parameters — decisions based purely on own limits.

        Flow:
        1. UPDATE: Refresh opponent model
        2. ACCEPT CHECK: Should we accept counterparty's offer?
        3. THINK → STRATEGIZE → CALCULATE → GENERATE → VALIDATE
        """
        logger.info(f"[{self.role.value}] Round {current_round}: generating offer")

        # ── 1. Update opponent model ───────────────────────────────────────
        if self.opponent_model and history:
            self.opponent_model.update(history)

        # ── 2. Acceptance check ────────────────────────────────────────────
        if counterparty_last_offer and current_round > 1:
            should_accept, accept_reason = self.should_accept_offer(
                counterparty_offer=counterparty_last_offer,
                current_round=current_round,
                max_rounds=max_rounds,
                history=history,
            )
            if should_accept:
                acceptance_offer = NegotiationOffer(
                    unit_price=counterparty_last_offer.unit_price,
                    volume=counterparty_last_offer.volume,
                    delivery_days=counterparty_last_offer.delivery_days,
                    payment_terms=counterparty_last_offer.payment_terms,
                    justification=f"[ACCEPTED] {accept_reason}",
                    leverage_used="acceptance",
                )
                reasoning = AgentReasoning(
                    strategy_used=self.strategy.concession_pattern.value.upper() if self.strategy else "LINEAR",
                    tactic="accept",
                    own_utility=0.8,
                    convergence_progress=100.0,
                    gap_remaining_eur=0.0,
                    summary=f"Autonomous acceptance: {accept_reason}",
                    context_factors=["autonomous_acceptance"],
                    reasoning_steps=[
                        ReasoningStep(
                            phase="THINK",
                            observation=f"Counterparty offered €{counterparty_last_offer.unit_price:.2f}",
                            reasoning=accept_reason,
                            conclusion="Accept — utility threshold met",
                        )
                    ],
                )
                return acceptance_offer, reasoning

        # ── 3. THINK ───────────────────────────────────────────────────────
        analysis = self._analyze_situation(
            current_round=current_round,
            max_rounds=max_rounds,
            history=history,
            counterparty_last_offer=counterparty_last_offer,
        )

        # ── 4. STRATEGIZE ──────────────────────────────────────────────────
        tactic = self._select_tactic_with_llm(
            analysis=analysis,
            current_round=current_round,
            max_rounds=max_rounds,
            counterparty_last_offer=counterparty_last_offer,
        )

        # ── 5. CALCULATE ───────────────────────────────────────────────────
        offer_params = self._calculate_offer_params(
            tactic=tactic,
            counterparty_last_offer=counterparty_last_offer,
            current_round=current_round,
            history=history,
            analysis=analysis,
            max_rounds=max_rounds,
        )

        # ── 6. GENERATE ────────────────────────────────────────────────────
        justification, leverage_used = self._generate_justification(
            offer_params=offer_params,
            tactic=tactic,
            analysis=analysis,
            counterparty_last_offer=counterparty_last_offer,
        )

        offer = NegotiationOffer(
            unit_price=offer_params["unit_price"],
            volume=offer_params["volume"],
            delivery_days=offer_params["delivery_days"],
            payment_terms=offer_params["payment_terms"],
            justification=justification,
            leverage_used=leverage_used,
        )

        # ── 7. VALIDATE ────────────────────────────────────────────────────
        is_valid, msg = self._validate_offer(offer)
        if not is_valid:
            logger.warning(f"[{self.role.value}] Validation failed: {msg} — applying clamp")
            if self.role == AgentRole.SUPPLIER and self.limits.min_price:
                offer.unit_price = max(offer.unit_price, self.limits.min_price)
            elif self.role == AgentRole.RETAILER and self.limits.max_price:
                offer.unit_price = min(offer.unit_price, self.limits.max_price)

        logger.info(
            f"[{self.role.value}] Offer: €{offer.unit_price:.2f} | "
            f"{offer.volume}u | {offer.delivery_days}d | {offer.payment_terms} | "
            f"tactic={tactic.get('tactic')}"
        )

        reasoning = self._build_reasoning(analysis=analysis, tactic=tactic, offer=offer)
        return offer, reasoning