"""
api/live_negotiation_endpoints.py
──────────────────────────────────
Live negotiation endpoints for auto-negotiation with HITL.

Design philosophy:
- Negotiation starts automatically after both parties set constraints
- Runs to completion without requiring manual round-by-round interaction
- HITL pauses only for meaningful situations (stall, limit violation, near max rounds)
- Human can adjust constraints and resume seamlessly
"""

from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel
import logging

from models.negotiation_models import (
    AgentRole,
    PartyLimits,
    SessionStatus,
    HITLSeverity,
    InterventionAction,
    RenegotiationContext,
)

logger = logging.getLogger(__name__)


# ── Request body models ──────────────────────────────────────────────────────

class InterventionBody(BaseModel):
    """Body for human intervention endpoint."""
    action: str                          # continue | adjust_constraints | pause | abort
    role: Optional[str] = None           # supplier | retailer — whose limits to update
    new_limits: Optional[dict] = None    # new PartyLimits as dict
    notes: Optional[str] = None


class RenegotiateBody(BaseModel):
    """Body for renegotiation after rejection."""
    role: str                            # supplier | retailer
    new_limits: dict                     # new PartyLimits
    rejection_context: Optional[dict] = None


# ── Route registration ───────────────────────────────────────────────────────

def add_live_negotiation_routes(app, sessions_db, orchestrator):
    """
    Register live negotiation routes on the FastAPI app.
    Called from api/main.py after app initialisation.
    """

    # ── Auto-negotiate (primary flow) ──────────────────────────────────────

    @app.post("/api/negotiations/{session_id}/negotiate-auto")
    def run_negotiation_auto(session_id: str, max_rounds: int = 50) -> dict:
        """
        AUTO MODE — run negotiation to completion without manual intervention.

        Runs all rounds automatically until one of:
        - Convergence reached (deal)
        - Max rounds exhausted
        - HITL trigger fires (stall / limit violation / near max rounds)
        - Terminal status (accepted / rejected / failed)

        This is the primary negotiation endpoint.  The frontend should call
        this once after both parties have set constraints.
        """
        session = sessions_db.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.status not in [SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot negotiate: session status is '{session.status}'"
            )

        logger.info(
            f"Session {session_id}: Starting auto-negotiation (max {max_rounds} rounds)"
        )

        rounds_executed = 0
        hitl_trigger = None

        while (
            session.status in [SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING]
            and rounds_executed < max_rounds
        ):
            session = orchestrator.run_negotiation_round(session)

            # Check for HITL only on WARNING+ severity
            trigger = orchestrator.check_hitl_needed(session)
            if trigger and trigger.severity in [HITLSeverity.WARNING, HITLSeverity.CRITICAL]:
                hitl_trigger = trigger
                session.status = SessionStatus.HITL_REQUIRED
                session.status_message = trigger.message
                logger.info(
                    f"Session {session_id}: Auto-negotiation paused for HITL "
                    f"({trigger.reason.value})"
                )
                break

            sessions_db[session_id] = session
            rounds_executed += 1

            logger.info(
                f"Session {session_id}: Round {session.current_round} complete "
                f"— status={session.status}"
            )

        sessions_db[session_id] = session

        # Collect all rounds for timeline display
        rounds_summary = [
            {
                "round_number": r.round_number,
                "role": r.role,
                "unit_price": r.offer.unit_price,
                "volume": r.offer.volume,
                "delivery_days": r.offer.delivery_days,
                "payment_terms": r.offer.payment_terms,
                "is_valid": r.is_valid,
                "reasoning_summary": (
                    r.agent_reasoning.get("reasoning_summary", "")
                    if r.agent_reasoning else ""
                ),
            }
            for r in session.rounds
        ]

        return {
            "session_id": session_id,
            "status": session.status,
            "message": session.status_message,
            "rounds_completed": rounds_executed,
            "total_rounds": session.current_round,
            "hitl_triggered": hitl_trigger is not None,
            "hitl_trigger": hitl_trigger.dict() if hitl_trigger else None,
            "rounds": rounds_summary,
            "zopa_min": session.zopa_min,
            "zopa_max": session.zopa_max,
        }

    # ── Single-round (optional live mode) ──────────────────────────────────

    @app.post("/api/negotiations/{session_id}/negotiate-round")
    def negotiate_single_round(session_id: str) -> dict:
        """
        LIVE MODE — execute exactly one negotiation round.

        Useful when the frontend wants to animate each round individually.
        The frontend is responsible for polling until a terminal / HITL status.
        """
        session = sessions_db.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.status not in [SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot negotiate: session status is '{session.status}'"
            )

        logger.info(
            f"Session {session_id}: Executing single round {session.current_round + 1}"
        )

        session = orchestrator.run_negotiation_round(session)

        # Check HITL — only pause on CRITICAL to avoid interrupting normal flow
        hitl_trigger = orchestrator.check_hitl_needed(session)
        if hitl_trigger and hitl_trigger.severity == HITLSeverity.CRITICAL:
            session.status = SessionStatus.HITL_REQUIRED
            session.status_message = hitl_trigger.message
            logger.warning(
                f"Session {session_id}: HITL triggered (CRITICAL) — {hitl_trigger.reason.value}"
            )

        sessions_db[session_id] = session

        last_round = session.rounds[-1] if session.rounds else None

        return {
            "session_id": session_id,
            "status": session.status,
            "message": session.status_message,
            "last_round": last_round.dict() if last_round else None,
            "hitl_trigger": hitl_trigger.dict() if hitl_trigger else None,
            "can_continue": session.status in [
                SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING
            ],
            "session": session.dict(),
        }

    # ── Human intervention ──────────────────────────────────────────────────

    @app.post("/api/negotiations/{session_id}/intervene")
    def human_intervention(session_id: str, body: InterventionBody) -> dict:
        """
        Human intervention during a HITL pause.

        Actions
        -------
        continue           Resume negotiation without changes.
        adjust_constraints Update one party's limits, then resume.
        pause              Keep session paused for manual review.
        abort              Terminate the negotiation.

        When action is ``adjust_constraints``, provide ``role`` and
        ``new_limits`` in the request body.  The orchestrator will
        re-check the ZOPA and restart negotiation automatically.
        """
        session = sessions_db.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        logger.info(
            f"Session {session_id}: Human intervention — action={body.action}"
        )

        try:
            action = InterventionAction(body.action.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action '{body.action}'. "
                       f"Valid: continue, adjust_constraints, pause, abort"
            )

        if action == InterventionAction.CONTINUE:
            if session.status == SessionStatus.HITL_REQUIRED:
                session.status = SessionStatus.NEGOTIATING
                session.status_message = "Resumed after human review"
            logger.info(f"Session {session_id}: Resumed by human")

        elif action == InterventionAction.ADJUST_CONSTRAINTS:
            if not body.new_limits:
                raise HTTPException(
                    status_code=400,
                    detail="'new_limits' is required when action is adjust_constraints"
                )
            if not body.role:
                raise HTTPException(
                    status_code=400,
                    detail="'role' is required when action is adjust_constraints"
                )

            try:
                role = AgentRole(body.role.lower())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid role '{body.role}'. Valid: supplier, retailer"
                )

            limits = PartyLimits(**body.new_limits)
            if role == AgentRole.SUPPLIER:
                session.supplier_limits = limits
            else:
                session.retailer_limits = limits

            # Re-check ZOPA with updated limits and restart if possible
            session = orchestrator.start_negotiation(session)
            logger.info(
                f"Session {session_id}: Constraints adjusted for {role.value}, "
                f"ZOPA re-evaluated"
            )

        elif action == InterventionAction.PAUSE:
            session.status = SessionStatus.PAUSED
            session.status_message = "Paused for manual review"
            logger.info(f"Session {session_id}: Paused by human")

        elif action == InterventionAction.ABORT:
            session.status = SessionStatus.REJECTED
            session.status_message = (
                f"Negotiation aborted: {body.notes or 'No reason provided'}"
            )
            logger.info(f"Session {session_id}: Aborted by human")

        session.updated_at = datetime.now().isoformat()
        sessions_db[session_id] = session

        return {
            "session_id": session_id,
            "status": session.status,
            "message": session.status_message,
            "zopa_exists": session.zopa_exists,
            "zopa_min": session.zopa_min,
            "zopa_max": session.zopa_max,
        }

    # ── Renegotiate after rejection ─────────────────────────────────────────

    @app.post("/api/negotiations/{session_id}/renegotiate")
    def renegotiate_after_rejection(
        session_id: str,
        body: RenegotiateBody,
    ) -> dict:
        """
        Restart negotiation with updated constraints after rejection or no-ZOPA.

        The agent will receive context about the previous negotiation so it can
        calibrate its strategy accordingly.
        """
        session = sessions_db.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            role = AgentRole(body.role.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role '{body.role}'. Valid: supplier, retailer"
            )

        logger.info(
            f"Session {session_id}: Renegotiation requested by {role.value}"
        )

        # Build context from current session history
        if not body.rejection_context and session.rounds:
            last_round = session.rounds[-1]
            sticking_points = []
            if session.status == SessionStatus.REJECTED:
                sticking_points.append("deal_rejected")
            elif session.status == SessionStatus.NO_ZOPA:
                sticking_points.append("no_zopa")

            context = RenegotiationContext(
                previous_session_id=session_id,
                rejection_reason=session.status_message,
                final_offer_price=last_round.offer.unit_price,
                final_offer_terms={
                    "volume": last_round.offer.volume,
                    "delivery_days": last_round.offer.delivery_days,
                    "payment_terms": last_round.offer.payment_terms,
                },
                rounds_completed=session.current_round,
                key_sticking_points=sticking_points,
            )
        else:
            context = (
                RenegotiationContext(**body.rejection_context)
                if body.rejection_context else None
            )

        # Update the requesting party's limits
        limits = PartyLimits(**body.new_limits)
        if role == AgentRole.SUPPLIER:
            session.supplier_limits = limits
        else:
            session.retailer_limits = limits

        # Reset negotiation state
        session.rounds = []
        session.current_round = 0
        session.supplier_approved = False
        session.retailer_approved = False
        session.status = SessionStatus.RENEGOTIATING
        session.status_message = "Renegotiating with updated constraints"
        session.updated_at = datetime.now().isoformat()

        # Re-check ZOPA
        session = orchestrator.start_negotiation(session)
        sessions_db[session_id] = session

        logger.info(
            f"Session {session_id}: Renegotiation started. "
            f"ZOPA exists: {session.zopa_exists}"
        )

        return {
            "session_id": session_id,
            "status": session.status,
            "message": session.status_message,
            "zopa_exists": session.zopa_exists,
            "zopa_range": (
                f"{session.zopa_min:.2f} - {session.zopa_max:.2f} EUR"
                if session.zopa_exists else None
            ),
            "renegotiation_context": context.dict() if context else None,
        }