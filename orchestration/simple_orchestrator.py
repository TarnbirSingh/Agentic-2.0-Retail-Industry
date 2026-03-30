"""
orchestration/simple_orchestrator.py
────────────────────────────────────
Simplified negotiation orchestrator - just agent-to-agent rounds.

No complex state machines, just:
1. Check ZOPA
2. Let agents negotiate
3. Validate each offer
4. Stop when converged or max rounds
"""

import logging
import time
from datetime import datetime
from typing import Optional

from agents.simple_agent import NegotiationAgent
from llm.ai_core_client import AICoreClient
from models.constraints import (
    calculate_zopa,
    validate_offer_against_retailer_limits,
    validate_offer_against_supplier_limits,
)
from models.negotiation_models import (
    AgentRole,
    NegotiationOffer,
    NegotiationRound,
    NegotiationSession,
    SessionStatus,
    PartyLimits,
    ZOPAAnalysis,
    HITLTrigger,
    HITLTriggerReason,
    HITLSeverity,
)

logger = logging.getLogger(__name__)


class SimpleOrchestrator:
    """Orchestrates agent-to-agent negotiation rounds."""

    def __init__(self, llm_client: AICoreClient):
        self.llm_client = llm_client

    def start_negotiation(
        self,
        session: NegotiationSession,
    ) -> NegotiationSession:
        """
        Check ZOPA and start negotiation if viable.

        Returns updated session.
        """
        # Calculate ZOPA
        if not session.supplier_limits or not session.retailer_limits:
            session.status = SessionStatus.PENDING_LIMITS
            session.status_message = "Waiting for both parties to set their limits"
            return session

        zopa_min, zopa_max, zopa_exists = calculate_zopa(
            supplier_min_price=session.supplier_limits.min_price,
            retailer_max_price=session.retailer_limits.max_price,
        )

        session.zopa_min = zopa_min
        session.zopa_max = zopa_max
        session.zopa_exists = zopa_exists

        if not zopa_exists:
            session.status = SessionStatus.NO_ZOPA
            session.status_message = (
                f"No zone of possible agreement. "
                f"Supplier minimum ({zopa_min:.2f} EUR) exceeds "
                f"retailer maximum ({zopa_max:.2f} EUR)."
            )
            logger.warning(f"Session {session.session_id}: No ZOPA exists")
            return session

        # ZOPA exists, start negotiating
        session.status = SessionStatus.NEGOTIATING
        session.status_message = f"Negotiating within ZOPA: {zopa_min:.2f} - {zopa_max:.2f} EUR"
        logger.info(
            f"Session {session.session_id}: ZOPA exists [{zopa_min:.2f}, {zopa_max:.2f}] EUR"
        )

        return session

    def run_negotiation_round(
        self,
        session: NegotiationSession,
    ) -> NegotiationSession:
        """
        Execute one round of negotiation.

        Returns updated session.
        """
        if session.status not in [SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING]:
            logger.warning(f"Session {session.session_id} is not in NEGOTIATING status")
            return session

        if session.current_round >= session.max_rounds:
            session.status = SessionStatus.MAX_ROUNDS
            session.status_message = f"Maximum rounds ({session.max_rounds}) reached without agreement"
            logger.info(f"Session {session.session_id}: Max rounds reached")
            return session

        # Determine who's turn it is
        if session.current_round == 0:
            # First round: counterparty of initiator responds
            next_role = (
                AgentRole.RETAILER if session.initiator == AgentRole.SUPPLIER
                else AgentRole.SUPPLIER
            )
        else:
            # Alternate turns
            last_role = session.rounds[-1].role
            next_role = (
                AgentRole.SUPPLIER if last_role == AgentRole.RETAILER
                else AgentRole.RETAILER
            )

        session.current_round += 1
        logger.info(f"Session {session.session_id}: Round {session.current_round} - {next_role.value}'s turn")

        # Get agent limits
        limits = (
            session.supplier_limits if next_role == AgentRole.SUPPLIER
            else session.retailer_limits
        )

        # Create agent
        agent = NegotiationAgent(
            role=next_role,
            llm_client=self.llm_client,
            limits=limits,
            product_name=session.product_name,
        )

        # Get last counterparty offer
        counterparty_last_offer = None
        if session.rounds:
            # Get last offer from the OTHER party
            for round_info in reversed(session.rounds):
                if round_info.role != next_role:
                    counterparty_last_offer = round_info.offer
                    break
        else:
            # Use initial offer
            counterparty_last_offer = session.initial_offer

        # Generate counteroffer (returns tuple: offer + reasoning)
        try:
            new_offer, agent_reasoning = agent.generate_counteroffer(
                current_round=session.current_round,
                history=session.rounds,
                counterparty_last_offer=counterparty_last_offer,
                zopa_min=session.zopa_min,
                zopa_max=session.zopa_max,
            )
        except Exception as e:
            logger.error(f"Failed to generate offer: {e}", exc_info=True)
            session.status = SessionStatus.FAILED
            session.status_message = f"Agent failed to generate offer: {str(e)}"
            return session

        # Validate offer
        validation = self._validate_offer(new_offer, next_role, session)

        # Create round record with agent reasoning (for Agentic 2.0 transparency)
        round_record = NegotiationRound(
            round_number=session.current_round,
            role=next_role,
            offer=new_offer,
            is_valid=validation.is_valid,
            validation_message=validation.message,
            agent_reasoning=agent_reasoning.dict() if agent_reasoning else None,
        )

        session.rounds.append(round_record)
        session.updated_at = datetime.now().isoformat()

        if not validation.is_valid:
            logger.warning(
                f"Session {session.session_id} Round {session.current_round}: "
                f"Invalid offer from {next_role.value}: {validation.message}"
            )
            # Don't fail immediately, let the other agent respond
            return session

        # Check if we're converging
        if self._check_convergence(session):
            session.status = SessionStatus.PENDING_APPROVAL
            session.status_message = "Offers have converged — pending approval"
            logger.info(f"Session {session.session_id}: Convergence reached, moving to approval")

        return session

    def _validate_offer(
        self,
        offer: NegotiationOffer,
        role: AgentRole,
        session: NegotiationSession,
    ):
        """Validate offer against the agent's own limits."""
        if role == AgentRole.SUPPLIER:
            return validate_offer_against_supplier_limits(
                unit_price=offer.unit_price,
                volume=offer.volume,
                delivery_days=offer.delivery_days,
                payment_terms=offer.payment_terms,
                supplier_min_price=session.supplier_limits.min_price,
                supplier_min_volume=session.supplier_limits.min_volume,
                supplier_max_volume=session.supplier_limits.max_volume,
                supplier_acceptable_payment_terms=session.supplier_limits.acceptable_payment_terms,
            )
        else:
            return validate_offer_against_retailer_limits(
                unit_price=offer.unit_price,
                volume=offer.volume,
                delivery_days=offer.delivery_days,
                payment_terms=offer.payment_terms,
                retailer_max_price=session.retailer_limits.max_price,
                retailer_min_volume=session.retailer_limits.min_volume,
                retailer_max_volume=session.retailer_limits.max_volume,
                retailer_max_delivery_days=session.retailer_limits.max_delivery_days,
                retailer_acceptable_payment_terms=session.retailer_limits.acceptable_payment_terms,
                retailer_target_margin=session.retailer_limits.target_margin,
                retailer_retail_price=session.retailer_limits.retail_price,
            )

    def _check_convergence(self, session: NegotiationSession) -> bool:
        """
        Check if agents have converged to a deal.

        Convergence = price difference < 1.50 EUR and other terms compatible.
        """
        if len(session.rounds) < 2:
            return False

        # Get last offers from both parties
        supplier_offer = None
        retailer_offer = None

        for round_info in reversed(session.rounds):
            if round_info.role == AgentRole.SUPPLIER and supplier_offer is None:
                supplier_offer = round_info.offer
            elif round_info.role == AgentRole.RETAILER and retailer_offer is None:
                retailer_offer = round_info.offer

            if supplier_offer and retailer_offer:
                break

        if not supplier_offer or not retailer_offer:
            return False

        # Check price convergence
        price_diff = abs(supplier_offer.unit_price - retailer_offer.unit_price)
        if price_diff > 1.50:
            return False

        # Check volume compatibility (within 10%)
        volume_diff_pct = abs(supplier_offer.volume - retailer_offer.volume) / max(supplier_offer.volume, retailer_offer.volume)
        if volume_diff_pct > 0.10:
            return False

        logger.info(
            f"Convergence detected: price_diff={price_diff:.2f} EUR, "
            f"volume_diff={volume_diff_pct:.1%}"
        )

        return True

    def approve_deal(
        self,
        session: NegotiationSession,
        approving_role: AgentRole,
    ) -> NegotiationSession:
        """Record approval from one party."""
        if session.status != SessionStatus.PENDING_APPROVAL:
            logger.warning(f"Session {session.session_id} is not pending approval")
            return session

        if approving_role == AgentRole.SUPPLIER:
            session.supplier_approved = True
        else:
            session.retailer_approved = True

        session.updated_at = datetime.now().isoformat()

        # Check if both approved
        if session.supplier_approved and session.retailer_approved:
            session.status = SessionStatus.ACCEPTED
            session.status_message = "Deal accepted by both parties"
            logger.info(f"Session {session.session_id}: Deal accepted")

        return session

    def reject_deal(
        self,
        session: NegotiationSession,
        rejecting_role: AgentRole,
        reason: str = "",
    ) -> NegotiationSession:
        """One party rejects the current deal."""
        session.status = SessionStatus.REJECTED
        session.status_message = f"Deal rejected by {rejecting_role.value}: {reason}"
        session.updated_at = datetime.now().isoformat()
        logger.info(f"Session {session.session_id}: Deal rejected by {rejecting_role.value}")
        return session

    def check_zopa_with_recommendations(
        self,
        session: NegotiationSession,
    ) -> ZOPAAnalysis:
        """
        Check ZOPA and provide intelligent recommendations if no ZOPA.

        Uses LLM to suggest who should adjust and by how much.
        """
        if not session.supplier_limits or not session.retailer_limits:
            return ZOPAAnalysis(
                zopa_exists=False,
                recommendation="Both parties need to set their constraints first"
            )

        supplier_min = session.supplier_limits.min_price
        retailer_max = session.retailer_limits.max_price

        zopa_min, zopa_max, zopa_exists = calculate_zopa(
            supplier_min_price=supplier_min,
            retailer_max_price=retailer_max,
        )

        if zopa_exists:
            return ZOPAAnalysis(
                zopa_exists=True,
                zopa_min=zopa_min,
                zopa_max=zopa_max,
                recommendation=f"ZOPA exists: {zopa_min:.2f} - {zopa_max:.2f} EUR. Ready to negotiate."
            )

        # No ZOPA - calculate gap and get recommendations
        gap = supplier_min - retailer_max

        # Use LLM for intelligent recommendations
        prompt = f"""You are a B2B negotiation mediator analyzing a no-ZOPA situation.

Situation:
- Supplier minimum price: €{supplier_min:.2f}
- Retailer maximum price: €{retailer_max:.2f}
- Gap: €{gap:.2f}

Context:
- Product: {session.product_name}
- Initial offer price: €{session.initial_offer.unit_price:.2f}
- Volume: {session.initial_offer.volume} units

Your task:
1. Suggest who should adjust (supplier lower min, retailer raise max, or both meet in middle)
2. Provide specific € amounts for adjustments
3. Suggest 2-3 alternative approaches (e.g., volume discount, extended payment terms, faster delivery)

Output ONLY valid JSON in this exact format:
{{
    "recommendation": "Brief overall recommendation (1 sentence)",
    "supplier_suggestion": "What supplier should do (e.g., 'Lower min price to €43')" or null,
    "retailer_suggestion": "What retailer should do (e.g., 'Raise max price to €45')" or null,
    "alternative_approaches": [
        "Alternative 1 (e.g., 'Increase volume to 1000 units for volume discount')",
        "Alternative 2"
    ]
}}

Be specific with numbers. Focus on practical solutions."""

        try:
            import json
            response = self.llm_client.generate_text(prompt, max_tokens=400)
            suggestions = json.loads(response)

            return ZOPAAnalysis(
                zopa_exists=False,
                zopa_min=supplier_min,
                zopa_max=retailer_max,
                gap_amount=gap,
                recommendation=suggestions.get("recommendation", f"Gap of €{gap:.2f} needs to be bridged"),
                supplier_suggestion=suggestions.get("supplier_suggestion"),
                retailer_suggestion=suggestions.get("retailer_suggestion"),
                alternative_approaches=suggestions.get("alternative_approaches", []),
            )
        except Exception as e:
            logger.error(f"Failed to generate ZOPA recommendations: {e}", exc_info=True)
            # Fallback to simple analysis
            if gap <= 3:
                rec = "Small gap — both parties could adjust slightly"
                supplier_sugg = f"Consider lowering minimum to €{retailer_max:.2f}"
                retailer_sugg = f"Consider raising maximum to €{supplier_min:.2f}"
            elif gap <= 5:
                rec = "Moderate gap — negotiation needed"
                mid_point = (supplier_min + retailer_max) / 2
                supplier_sugg = f"Lower minimum to €{mid_point:.2f}"
                retailer_sugg = f"Raise maximum to €{mid_point:.2f}"
            else:
                rec = "Large gap — significant adjustments or alternatives needed"
                supplier_sugg = f"Supplier needs to lower minimum by €{gap / 2:.2f}"
                retailer_sugg = f"Retailer needs to raise maximum by €{gap / 2:.2f}"

            return ZOPAAnalysis(
                zopa_exists=False,
                zopa_min=supplier_min,
                zopa_max=retailer_max,
                gap_amount=gap,
                recommendation=rec,
                supplier_suggestion=supplier_sugg,
                retailer_suggestion=retailer_sugg,
                alternative_approaches=[
                    "Increase volume for better pricing",
                    "Extend payment terms for price flexibility",
                    "Consider alternative products with better margins",
                ],
            )

    def check_hitl_needed(
        self,
        session: NegotiationSession,
    ) -> Optional[HITLTrigger]:
        """
        Check if human intervention is needed during negotiation.

        IMPORTANT: Early rounds (1-3) are the anchoring phase — both agents
        intentionally offer outside the ZOPA as a negotiation tactic. This is
        completely normal and must NOT trigger HITL.

        Real HITL triggers:
        1. Agent violated its own declared hard limits (LLM hallucination)
        2. Negotiation stalled — no meaningful movement after the anchoring phase
        3. Max rounds approaching — only 2 rounds left before cutoff
        """
        if not session.rounds:
            return None

        last_round = session.rounds[-1]
        last_offer = last_round.offer
        anchoring_phase = session.current_round <= 3  # rounds 1-3 are anchoring

        # ── Helper: latest prices from each party ──────────────────────────────
        def _party_prices():
            s = next(
                (r.offer.unit_price for r in reversed(session.rounds)
                 if r.role == AgentRole.SUPPLIER), None
            )
            r = next(
                (r.offer.unit_price for r in reversed(session.rounds)
                 if r.role == AgentRole.RETAILER), None
            )
            gap = abs(s - r) if (s is not None and r is not None) else None
            return s, r, gap

        # ── 1. Agent violated its own hard limits ──────────────────────────────
        # A supplier offering BELOW their own declared min_price, or a retailer
        # offering ABOVE their own declared max_price, is a true LLM error.
        # We add a 2% tolerance to avoid false positives from rounding.
        if not anchoring_phase:
            s_price, r_price, gap = _party_prices()

            if last_round.role == AgentRole.SUPPLIER and session.supplier_limits:
                min_price = session.supplier_limits.min_price or 0.0
                if min_price > 0 and last_offer.unit_price < min_price * 0.98:
                    return HITLTrigger(
                        reason=HITLTriggerReason.ZOPA_BREACH,
                        severity=HITLSeverity.CRITICAL,
                        message=(
                            f"Supplier agent offered €{last_offer.unit_price:.2f}, "
                            f"which is below its own declared minimum of €{min_price:.2f}. "
                            "This may indicate a model reasoning error."
                        ),
                        recommended_action=(
                            "Review the supplier agent's reasoning chain. "
                            "Adjust the min_price constraint if this concession is intentional."
                        ),
                        current_price=last_offer.unit_price,
                        supplier_last_price=s_price,
                        retailer_last_price=r_price,
                        price_gap=gap,
                        zopa_min=session.zopa_min,
                        zopa_max=session.zopa_max,
                    )

            if last_round.role == AgentRole.RETAILER and session.retailer_limits:
                max_price = session.retailer_limits.max_price
                if max_price and last_offer.unit_price > max_price * 1.02:
                    return HITLTrigger(
                        reason=HITLTriggerReason.ZOPA_BREACH,
                        severity=HITLSeverity.CRITICAL,
                        message=(
                            f"Retailer agent offered €{last_offer.unit_price:.2f}, "
                            f"which exceeds its own declared maximum of €{max_price:.2f}. "
                            "This may indicate a model reasoning error."
                        ),
                        recommended_action=(
                            "Review the retailer agent's reasoning chain. "
                            "Adjust the max_price constraint if this acceptance is intentional."
                        ),
                        current_price=last_offer.unit_price,
                        supplier_last_price=s_price,
                        retailer_last_price=r_price,
                        price_gap=gap,
                        zopa_min=session.zopa_min,
                        zopa_max=session.zopa_max,
                    )

        # ── 2. Negotiation stalled ─────────────────────────────────────────────
        # Both agents have made less than €0.50 total movement across their last
        # 3 turns each. Only check after at least 8 rounds (post-anchoring phase).
        if len(session.rounds) >= 8:
            supplier_prices = [
                r.offer.unit_price for r in session.rounds
                if r.role == AgentRole.SUPPLIER
            ][-3:]
            retailer_prices = [
                r.offer.unit_price for r in session.rounds
                if r.role == AgentRole.RETAILER
            ][-3:]

            if len(supplier_prices) >= 3 and len(retailer_prices) >= 3:
                supplier_movement = max(supplier_prices) - min(supplier_prices)
                retailer_movement = max(retailer_prices) - min(retailer_prices)

                if supplier_movement < 0.50 and retailer_movement < 0.50:
                    s_price, r_price, price_gap = _party_prices()
                    return HITLTrigger(
                        reason=HITLTriggerReason.NEGOTIATION_STALLED,
                        severity=HITLSeverity.WARNING,
                        message=(
                            f"Negotiation stalled after {session.current_round} rounds. "
                            f"Neither party has moved more than €0.50 in the last 3 turns."
                        ),
                        recommended_action=(
                            "Consider widening your acceptable price range, "
                            "or offering concessions on delivery timeline or payment terms "
                            "to unlock progress."
                        ),
                        current_price=last_offer.unit_price,
                        supplier_last_price=s_price,
                        retailer_last_price=r_price,
                        price_gap=price_gap,
                        zopa_min=session.zopa_min,
                        zopa_max=session.zopa_max,
                    )

        # ── 3. Max rounds approaching ──────────────────────────────────────────
        rounds_remaining = session.max_rounds - session.current_round
        if 0 < rounds_remaining <= 2:
            s_price, r_price, price_gap = _party_prices()

            return HITLTrigger(
                reason=HITLTriggerReason.MAX_ROUNDS_APPROACHING,
                severity=HITLSeverity.WARNING,
                message=(
                    f"Only {rounds_remaining} round(s) remaining before the "
                    f"maximum of {session.max_rounds} is reached."
                ),
                recommended_action=(
                    "Review both positions and decide whether to extend the "
                    "negotiation by adjusting constraints, or accept the closest offer."
                ),
                current_price=last_offer.unit_price,
                supplier_last_price=s_price,
                retailer_last_price=r_price,
                price_gap=price_gap,
                zopa_min=session.zopa_min,
                zopa_max=session.zopa_max,
                rounds_remaining=rounds_remaining,
            )

        return None
