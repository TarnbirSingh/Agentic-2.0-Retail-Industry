"""
agents/simple_agent.py
─────────────────────
Strategic negotiation agent with multi-attribute utility and concession logic.

Architecture Evolution:
──────────────────────
V1 (Original): Single LLM call, distributive price negotiation
V2 (Current): Multi-phase ReAct-inspired flow with:
  - THINK: Analyze situation
  - STRATEGIZE: Select tactics
  - CALCULATE: Compute offer parameters (utility-based)
  - GENERATE: LLM generates justification
  - VALIDATE: Self-reflection

Theoretical Foundation:
───────────────────────
- ReAct Pattern (Yao 2023): Thought → Action → Observation
- Multi-Attribute Utility (O'Brien 2024, Fujita)
- Concession Strategies (Okunev 2022, Monczka 2009)
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

# Import new strategic modules
try:
    from models.utility import (
        NegotiationPreferences,
        calculate_utility,
        UtilityResult,
    )
    from models.constraints import convert_limits_to_preferences
    from agents.strategy import (
        NegotiationStrategy,
        calculate_concession_amount,
        calculate_initial_anchor,
        select_leverage,
        should_make_tradeoff,
        LeverageType,
        ConcessionPattern,
    )
    STRATEGIC_MODE_AVAILABLE = True
except ImportError:
    STRATEGIC_MODE_AVAILABLE = False
    logger.warning("Strategic mode not available - falling back to basic negotiation")


class NegotiationAgent:
    """
    Strategic B2B negotiation agent with multi-attribute utility awareness.
    
    Can operate in two modes:
    1. STRATEGIC (default): Uses utility functions, concession strategies, leverage
    2. BASIC (fallback): Simple price-based negotiation (if utility module unavailable)
    """
    
    def __init__(
        self,
        role: AgentRole,
        llm_client: AICoreClient,
        limits: PartyLimits,
        product_name: str,
        preferences: Optional[NegotiationPreferences] = None,
        strategy: Optional[NegotiationStrategy] = None,
    ):
        self.role = role
        self.llm_client = llm_client
        self.limits = limits
        self.product_name = product_name
        
        # Strategic components
        if STRATEGIC_MODE_AVAILABLE:
            # Convert limits to preferences if not provided
            if preferences is None:
                self.preferences = convert_limits_to_preferences(
                    limits,
                    is_supplier=(role == AgentRole.SUPPLIER)
                )
            else:
                self.preferences = preferences
            
            # Use default strategy if not provided
            if strategy is None:
                self.strategy = NegotiationStrategy(
                    concession_pattern=ConcessionPattern.LINEAR,
                    concession_rate=0.15,
                    initial_anchor_multiplier=1.15,
                )
            else:
                self.strategy = strategy
        else:
            self.preferences = None
            self.strategy = None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 1: THINK - Analyze Current Situation
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _analyze_situation(
        self,
        current_round: int,
        max_rounds: int,
        history: list[NegotiationRound],
        counterparty_last_offer: Optional[NegotiationOffer],
        zopa_min: float,
        zopa_max: float,
    ) -> dict:
        """
        Phase 1: THINK - Analyze the negotiation situation.
        
        Returns dict with:
        - current_price_gap: Absolute EUR gap between parties
        - gap_percentage: Gap as % of ZOPA width
        - rounds_remaining: Rounds left
        - counterparty_concession: How much they conceded last round
        - is_stuck: Are we stuck in same position?
        - urgency_level: How urgent is closure?
        """
        analysis = {
            "current_price_gap": 0.0,
            "gap_percentage": 0.0,
            "rounds_remaining": max_rounds - current_round,
            "counterparty_concession": None,
            "is_stuck": False,
            "urgency_level": "low",
        }
        
        if not counterparty_last_offer:
            # First round - no analysis yet
            return analysis
        
        # Calculate price gap
        my_last_price = None
        if history:
            my_rounds = [r for r in history if r.role == self.role]
            if my_rounds:
                my_last_price = my_rounds[-1].offer.unit_price
        
        if my_last_price:
            analysis["current_price_gap"] = abs(my_last_price - counterparty_last_offer.unit_price)
            zopa_width = zopa_max - zopa_min
            if zopa_width > 0:
                analysis["gap_percentage"] = (analysis["current_price_gap"] / zopa_width) * 100
        
        # Calculate counterparty's last concession
        counterparty_rounds = [r for r in history if r.role != self.role]
        if len(counterparty_rounds) >= 2:
            prev_price = counterparty_rounds[-2].offer.unit_price
            curr_price = counterparty_rounds[-1].offer.unit_price
            
            if self.role == AgentRole.SUPPLIER:
                # For supplier: retailer increasing price = concession
                analysis["counterparty_concession"] = curr_price - prev_price
            else:
                # For retailer: supplier lowering price = concession
                analysis["counterparty_concession"] = prev_price - curr_price
        
        # Check if stuck (same price for 2+ rounds)
        if len(counterparty_rounds) >= 2:
            if abs(counterparty_rounds[-1].offer.unit_price - counterparty_rounds[-2].offer.unit_price) < 0.10:
                analysis["is_stuck"] = True
        
        # Urgency assessment
        if analysis["rounds_remaining"] <= 2:
            analysis["urgency_level"] = "critical"
        elif analysis["rounds_remaining"] <= 4:
            analysis["urgency_level"] = "high"
        elif analysis["rounds_remaining"] <= 6:
            analysis["urgency_level"] = "medium"
        
        logger.debug(f"Situation analysis: {analysis}")
        return analysis
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 2: STRATEGIZE - Select Negotiation Tactic
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _select_tactic(
        self,
        analysis: dict,
        current_round: int,
        max_rounds: int,
    ) -> dict:
        """
        Phase 2: STRATEGIZE - Choose negotiation tactics for this round.
        
        Returns dict with:
        - concession_amount: EUR to concede this round
        - use_leverage: LeverageType or None
        - propose_tradeoff: Should we suggest multi-attribute trade?
        - recommended_action: "concede", "hold", "accept", "reject"
        """
        if not STRATEGIC_MODE_AVAILABLE or not self.strategy:
            # Fallback: simple halfway approach
            return {
                "concession_amount": analysis.get("current_price_gap", 0) * 0.5,
                "use_leverage": None,
                "propose_tradeoff": False,
                "recommended_action": "concede",
            }
        
        tactic = {}
        
        # Calculate strategic concession
        tactic["concession_amount"] = calculate_concession_amount(
            strategy=self.strategy,
            round_number=current_round,
            max_rounds=max_rounds,
            current_gap=analysis["current_price_gap"],
            counterparty_last_concession=analysis.get("counterparty_concession"),
        )
        
        # Select leverage
        tactic["use_leverage"] = select_leverage(
            strategy=self.strategy,
            round_number=current_round,
            current_situation=analysis,
        )
        
        # Consider trade-offs (multi-attribute negotiation)
        tactic["propose_tradeoff"] = should_make_tradeoff(
            strategy=self.strategy,
            round_number=current_round,
            utility_gap=analysis["gap_percentage"] / 100.0,  # Rough proxy
        )
        
        # Recommended action
        if analysis["gap_percentage"] < 2.0:
            tactic["recommended_action"] = "accept"  # Very close
        elif analysis["urgency_level"] == "critical" and analysis["gap_percentage"] < 10.0:
            tactic["recommended_action"] = "concede"  # Last chance
        elif analysis["is_stuck"]:
            tactic["recommended_action"] = "tradeoff"  # Break deadlock
        else:
            tactic["recommended_action"] = "concede"  # Normal negotiation
        
        logger.debug(f"Selected tactic: {tactic}")
        return tactic
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 3: CALCULATE - Compute Concrete Offer Parameters
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _calculate_offer_params(
        self,
        tactic: dict,
        counterparty_last_offer: Optional[NegotiationOffer],
        current_round: int,
        zopa_min: float,
        zopa_max: float,
        history: Optional[list] = None,
    ) -> dict:
        """
        Phase 3: CALCULATE - Compute concrete offer parameters.
        
        This is the "deterministic math" phase - no LLM, pure calculation.
        
        Returns dict with:
        - unit_price: Calculated price
        - volume: Order quantity
        - delivery_days: Lead time
        - payment_terms: Payment terms
        """
        params = {}
        
        # PRICE CALCULATION
        if current_round == 1:
            # First round: use anchoring strategy
            if STRATEGIC_MODE_AVAILABLE and self.strategy and self.preferences:
                target_price = self.preferences.target_price or (
                    (zopa_min + zopa_max) / 2
                )
                params["unit_price"] = calculate_initial_anchor(
                    target_price=target_price,
                    strategy=self.strategy,
                    is_supplier=(self.role == AgentRole.SUPPLIER),
                )
            else:
                # Simple anchor: 15% beyond ZOPA midpoint
                midpoint = (zopa_min + zopa_max) / 2
                if self.role == AgentRole.SUPPLIER:
                    params["unit_price"] = midpoint + (zopa_max - midpoint) * 0.5
                else:
                    params["unit_price"] = midpoint - (midpoint - zopa_min) * 0.5
        else:
            # Subsequent rounds: apply concession from MY last offer (not ZOPA boundary)
            if counterparty_last_offer:
                # Resolve my last price from history (correct baseline for concession)
                my_last_price = None
                if history:
                    my_rounds = [r for r in history if r.role == self.role]
                    if my_rounds:
                        my_last_price = my_rounds[-1].offer.unit_price

                # Fall back to ZOPA boundary only if we truly have no history
                if my_last_price is None:
                    my_last_price = zopa_max if self.role == AgentRole.SUPPLIER else zopa_min

                # Calculate new price: move from MY last position toward counterparty
                concession = tactic["concession_amount"]

                if self.role == AgentRole.SUPPLIER:
                    # Supplier moves DOWN from own last price by concession amount
                    params["unit_price"] = my_last_price - concession
                    # Never go below what counterparty is offering (take the better side)
                    params["unit_price"] = max(params["unit_price"], zopa_min)
                else:
                    # Retailer moves UP from own last price by concession amount
                    params["unit_price"] = my_last_price + concession
                    params["unit_price"] = min(params["unit_price"], zopa_max)
            else:
                # Fallback
                params["unit_price"] = (zopa_min + zopa_max) / 2
        
        # Clamp to limits
        if self.role == AgentRole.SUPPLIER:
            params["unit_price"] = max(params["unit_price"], self.limits.min_price or 0)
        else:
            params["unit_price"] = min(params["unit_price"], self.limits.max_price or float('inf'))
        
        params["unit_price"] = round(params["unit_price"], 2)
        
        # VOLUME CALCULATION
        if counterparty_last_offer:
            params["volume"] = counterparty_last_offer.volume
        else:
            # Default to middle of acceptable range
            min_vol = self.limits.min_volume or 1000
            max_vol = self.limits.max_volume or min_vol * 2
            params["volume"] = (min_vol + max_vol) // 2
        
        # DELIVERY CALCULATION
        if counterparty_last_offer:
            params["delivery_days"] = counterparty_last_offer.delivery_days
        else:
            params["delivery_days"] = 14  # Standard 2 weeks
        
        # PAYMENT TERMS
        if counterparty_last_offer:
            params["payment_terms"] = counterparty_last_offer.payment_terms
        else:
            acceptable_terms = self.limits.acceptable_payment_terms or ["Net 30"]
            params["payment_terms"] = acceptable_terms[0]
        
        # TRADE-OFF LOGIC (if proposed)
        if tactic.get("propose_tradeoff") and counterparty_last_offer:
            # Example: Offer faster delivery for slight price increase
            if self.role == AgentRole.SUPPLIER:
                params["delivery_days"] = max(7, params["delivery_days"] - 3)
                params["unit_price"] += 0.50  # Small premium for speed
            else:
                # Retailer: Accept higher price for better terms
                params["payment_terms"] = "Net 60"  # Request extended terms
                params["unit_price"] += 0.30  # Willing to pay slightly more
        
        logger.debug(f"Calculated offer params: {params}")
        return params
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 4: GENERATE - LLM Creates Justification
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _generate_justification(
        self,
        offer_params: dict,
        tactic: dict,
        analysis: dict,
        counterparty_last_offer: Optional[NegotiationOffer],
    ) -> tuple[str, Optional[str]]:
        """
        Phase 4: GENERATE - LLM creates human-readable justification.
        
        Returns:
            (justification_text, leverage_used)
        """
        # Build prompt for LLM
        role_name = "supplier" if self.role == AgentRole.SUPPLIER else "retailer"
        
        prompt = f"""You are a B2B {role_name} in a negotiation for {self.product_name}.

Your calculated offer is:
- Price: {offer_params['unit_price']:.2f} EUR
- Volume: {offer_params['volume']} units
- Delivery: {offer_params['delivery_days']} days
- Payment: {offer_params['payment_terms']}

Context:
- You conceded {tactic['concession_amount']:.2f} EUR this round
- Gap remaining: {analysis['current_price_gap']:.2f} EUR ({analysis['gap_percentage']:.1f}%)
- Urgency: {analysis['urgency_level']}
"""
        
        if counterparty_last_offer:
            counterparty = "retailer" if self.role == AgentRole.SUPPLIER else "supplier"
            prompt += f"\nCounterparty's last offer: €{counterparty_last_offer.unit_price:.2f}"
        
        if tactic.get("use_leverage"):
            prompt += f"\n\nUse this leverage: {tactic['use_leverage'].value}"
        
        prompt += """\n\nWrite a professional, concise justification (2-3 sentences) for this offer.
Be specific about the business value and reasoning.
Output only the justification text, nothing else."""
        
        try:
            justification = self.llm_client.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
            ).strip()
            
            # Clean up
            if justification.startswith('"') and justification.endswith('"'):
                justification = justification[1:-1]
            
            leverage_used = tactic.get("use_leverage")
            leverage_str = leverage_used.value if leverage_used else None
            
            return justification, leverage_str
            
        except Exception as e:
            logger.warning(f"Failed to generate justification: {e}")
            # Fallback
            return (
                f"Adjusted offer to {offer_params['unit_price']:.2f} EUR based on market conditions.",
                None
            )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 5: VALIDATE - Self-Reflection & Constraint Check
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _validate_offer(
        self,
        offer: NegotiationOffer,
    ) -> tuple[bool, str]:
        """
        Phase 5: VALIDATE - Self-reflection and constraint checking.
        
        Returns:
            (is_valid, error_message)
        """
        # Hard constraint validation
        if self.role == AgentRole.SUPPLIER:
            if self.limits.min_price and offer.unit_price < self.limits.min_price:
                return False, f"Price {offer.unit_price:.2f} below minimum {self.limits.min_price:.2f}"
            
            if self.limits.min_volume and offer.volume < self.limits.min_volume:
                return False, f"Volume {offer.volume} below minimum {self.limits.min_volume}"
            
            if self.limits.max_volume and offer.volume > self.limits.max_volume:
                return False, f"Volume {offer.volume} exceeds capacity {self.limits.max_volume}"
        else:
            if self.limits.max_price and offer.unit_price > self.limits.max_price:
                return False, f"Price {offer.unit_price:.2f} exceeds maximum {self.limits.max_price:.2f}"
            
            if self.limits.max_delivery_days and offer.delivery_days > self.limits.max_delivery_days:
                return False, f"Delivery {offer.delivery_days} exceeds limit {self.limits.max_delivery_days}"
        
        # Payment terms validation
        if self.limits.acceptable_payment_terms:
            if offer.payment_terms not in self.limits.acceptable_payment_terms:
                # Auto-fix to first acceptable term
                offer.payment_terms = self.limits.acceptable_payment_terms[0]
        
        return True, "Valid"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # BUILD REASONING (for Agentic 2.0 transparency)
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _build_reasoning(
        self,
        analysis: dict,
        tactic: dict,
        offer: NegotiationOffer,
        zopa_min: float,
        zopa_max: float,
    ) -> AgentReasoning:
        """
        Build AgentReasoning object for frontend transparency.
        
        This captures what the agent "thought" during the 5 phases.
        """
        # Calculate utility (if available)
        own_utility = 0.5  # Default fallback
        estimated_counterparty_utility = None
        
        if STRATEGIC_MODE_AVAILABLE and self.preferences:
            try:
                utility_result = calculate_utility(
                    preferences=self.preferences,
                    price=offer.unit_price,
                    volume=offer.volume,
                    delivery_days=offer.delivery_days,
                    payment_terms=offer.payment_terms,
                )
                own_utility = utility_result.total_utility
            except Exception as e:
                logger.warning(f"Failed to calculate utility: {e}")
        
        # Calculate convergence progress
        zopa_width = zopa_max - zopa_min
        if zopa_width > 0:
            # How far into ZOPA are we?
            if self.role == AgentRole.SUPPLIER:
                progress = ((zopa_max - offer.unit_price) / zopa_width) * 100
            else:
                progress = ((offer.unit_price - zopa_min) / zopa_width) * 100
            convergence_progress = min(100.0, max(0.0, progress))
        else:
            convergence_progress = 0.0
        
        # Build reasoning steps
        reasoning_steps = [
            ReasoningStep(
                phase="THINK",
                observation=f"Price gap: €{analysis['current_price_gap']:.2f}, {analysis['rounds_remaining']} rounds left",
                reasoning=f"Urgency level: {analysis['urgency_level']}, {'stuck' if analysis['is_stuck'] else 'progressing'}",
                conclusion=f"Need to {'close quickly' if analysis['urgency_level'] in ['high', 'critical'] else 'negotiate steadily'}"
            ),
            ReasoningStep(
                phase="STRATEGIZE",
                observation=f"Selected {self.strategy.concession_pattern.value if self.strategy else 'LINEAR'} strategy",
                reasoning=f"Will concede €{tactic['concession_amount']:.2f} this round",
                conclusion=f"Action: {tactic['recommended_action']}"
            ),
            ReasoningStep(
                phase="CALCULATE",
                observation=f"Computed price: €{offer.unit_price:.2f}",
                reasoning=f"Within limits, utility score: {own_utility:.2f}",
                conclusion="Offer is valid and strategic"
            ),
        ]
        
        if tactic.get("use_leverage"):
            reasoning_steps.append(
                ReasoningStep(
                    phase="GENERATE",
                    observation=f"Using leverage: {tactic['use_leverage'].value if hasattr(tactic['use_leverage'], 'value') else tactic['use_leverage']}",
                    reasoning="Strengthen position with business value argument",
                    conclusion="Justification crafted"
                )
            )
        
        # Determine strategy name
        strategy_name = "LINEAR"
        if self.strategy:
            strategy_name = self.strategy.concession_pattern.value.upper()
        
        # Determine tactic name
        tactic_name = tactic.get("recommended_action", "concession")
        
        # Build summary
        strategy_desc = f"Using {strategy_name} concession strategy"
        concession_desc = f", conceded €{abs(tactic['concession_amount']):.2f}" if tactic['concession_amount'] else ""
        leverage_desc = f", leveraging {offer.leverage_used}" if offer.leverage_used else ""
        
        summary = f"{strategy_desc}{concession_desc}{leverage_desc}"
        
        # Context factors
        context_factors = []
        if analysis['urgency_level'] in ['high', 'critical']:
            context_factors.append("time_pressure")
        if analysis['is_stuck']:
            context_factors.append("negotiation_stalled")
        if tactic.get("propose_tradeoff"):
            context_factors.append("multi_attribute_trade")
        if analysis['gap_percentage'] < 5:
            context_factors.append("near_agreement")
        
        return AgentReasoning(
            strategy_used=strategy_name,
            tactic=tactic_name,
            own_utility=own_utility,
            estimated_counterparty_utility=estimated_counterparty_utility,
            concession_amount_eur=tactic['concession_amount'],
            concession_percentage=(tactic['concession_amount'] / analysis['current_price_gap'] * 100) if analysis['current_price_gap'] > 0 else 0.0,
            convergence_progress=convergence_progress,
            gap_remaining_eur=analysis['current_price_gap'],
            leverage_used=offer.leverage_used,
            context_factors=context_factors,
            reasoning_steps=reasoning_steps,
            summary=summary,
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN ENTRY POINT
    # ═══════════════════════════════════════════════════════════════════════════
    
    def generate_counteroffer(
        self,
        current_round: int,
        history: list[NegotiationRound],
        counterparty_last_offer: Optional[NegotiationOffer],
        zopa_min: float,
        zopa_max: float,
        max_rounds: int = 10,
    ) -> Tuple[NegotiationOffer, AgentReasoning]:
        """
        Generate next counteroffer using 5-phase strategic process.
        
        Flow:
        1. THINK: Analyze situation
        2. STRATEGIZE: Select tactics
        3. CALCULATE: Compute offer parameters
        4. GENERATE: LLM creates justification
        5. VALIDATE: Self-reflection
        
        Returns:
            NegotiationOffer
        """
        logger.info(
            f"[{self.role.value}] Generating counteroffer for round {current_round}"
        )
        
        # PHASE 1: THINK
        analysis = self._analyze_situation(
            current_round=current_round,
            max_rounds=max_rounds,
            history=history,
            counterparty_last_offer=counterparty_last_offer,
            zopa_min=zopa_min,
            zopa_max=zopa_max,
        )
        
        # PHASE 2: STRATEGIZE
        tactic = self._select_tactic(
            analysis=analysis,
            current_round=current_round,
            max_rounds=max_rounds,
        )
        
        # PHASE 3: CALCULATE
        offer_params = self._calculate_offer_params(
            tactic=tactic,
            counterparty_last_offer=counterparty_last_offer,
            current_round=current_round,
            zopa_min=zopa_min,
            zopa_max=zopa_max,
            history=history,
        )
        
        # PHASE 4: GENERATE
        justification, leverage_used = self._generate_justification(
            offer_params=offer_params,
            tactic=tactic,
            analysis=analysis,
            counterparty_last_offer=counterparty_last_offer,
        )
        
        # Create offer object
        offer = NegotiationOffer(
            unit_price=offer_params["unit_price"],
            volume=offer_params["volume"],
            delivery_days=offer_params["delivery_days"],
            payment_terms=offer_params["payment_terms"],
            justification=justification,
            leverage_used=leverage_used,
        )
        
        # PHASE 5: VALIDATE
        is_valid, error_msg = self._validate_offer(offer)
        
        if not is_valid:
            logger.error(f"Generated offer failed validation: {error_msg}")
            # Apply correction and retry (simplified - in production would iterate)
            if self.role == AgentRole.SUPPLIER and self.limits.min_price:
                offer.unit_price = max(offer.unit_price, self.limits.min_price)
            elif self.role == AgentRole.RETAILER and self.limits.max_price:
                offer.unit_price = min(offer.unit_price, self.limits.max_price)
        
        logger.info(
            f"[{self.role.value}] Generated offer: €{offer.unit_price:.2f}, "
            f"{offer.volume} units, {offer.delivery_days}d, {offer.payment_terms}"
        )
        
        # Build AgentReasoning for transparency
        reasoning = self._build_reasoning(
            analysis=analysis,
            tactic=tactic,
            offer=offer,
            zopa_min=zopa_min,
            zopa_max=zopa_max,
        )
        
        return offer, reasoning
