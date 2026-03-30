"""
api/main.py
───────────
FastAPI backend for TradeBridge 2.0.
REST API for agent-to-agent B2B negotiations.
"""

import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Add parent directory to Python path
parent_dir = Path(__file__).resolve().parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents.request_agent import RequestAgent
from config.settings import settings
from llm.ai_core_client import AICoreClient
from models.negotiation_models import (
    AgentRole,
    NegotiationOffer,
    NegotiationSession,
    PartyLimits,
    ProductRequest,
    SessionStatus,
)
from orchestration.simple_orchestrator import SimpleOrchestrator
from api.live_negotiation_endpoints import add_live_negotiation_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TradeBridge 2.0 API",
    description="Agent-to-agent B2B negotiation platform",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory state (replace with Redis/PostgreSQL in production)
sessions_db: dict[str, NegotiationSession] = {}
requests_db: dict[str, ProductRequest] = {}

llm_client = AICoreClient()
orchestrator = SimpleOrchestrator(llm_client)
request_agent = RequestAgent(llm_client)

add_live_negotiation_routes(app, sessions_db, orchestrator)


# ─── Request / Response Models ────────────────────────────────────────────────

class InitiateNegotiationRequest(BaseModel):
    initiator: AgentRole
    product_id: str
    product_name: str
    initial_offer: NegotiationOffer
    supplier_id: Optional[str] = None
    retailer_id: Optional[str] = None


class SetLimitsRequest(BaseModel):
    session_id: str
    role: AgentRole
    limits: PartyLimits


class ApprovalRequest(BaseModel):
    session_id: str
    role: AgentRole
    approved: bool
    reason: Optional[str] = None


class CatalogResponse(BaseModel):
    products: list[dict]
    suppliers: list[dict]
    retailers: list[dict]


class CreateRequestModel(BaseModel):
    retailer_id: str
    retailer_name: str
    raw_request: str


class CreateOfferFromRequestModel(BaseModel):
    product_id: str
    supplier_id: str
    unit_price: float
    volume: int
    delivery_days: int
    payment_terms: str
    notes: Optional[str] = None
    supplier_constraints: Optional[dict] = None  # legacy, ignored


class DirectOfferRequest(BaseModel):
    supplier_id: str
    supplier_name: str
    retailer_id: str
    retailer_name: str
    product_id: str
    offer_details: NegotiationOffer
    supplier_constraints: dict


class CreateTargetedRequestModel(BaseModel):
    retailer_id: str
    retailer_name: str
    raw_request: str
    target_supplier_ids: Optional[list[str]] = None


class RoleActionBody(BaseModel):
    role: str


class RejectBody(BaseModel):
    role: str
    reason: Optional[str] = ""


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "TradeBridge 2.0", "status": "operational", "version": "2.0.0"}


# ─── Catalog & Partners ───────────────────────────────────────────────────────

@app.get("/api/catalog")
def get_catalog() -> CatalogResponse:
    """Return all products and partner lists."""
    with open(parent_dir / "data" / "products_catalog.json") as f:
        products_data = json.load(f)
    with open(parent_dir / "data" / "partners_directory.json") as f:
        partners_data = json.load(f)
    return CatalogResponse(
        products=products_data["products"],
        suppliers=partners_data["suppliers"],
        retailers=partners_data["retailers"],
    )


@app.get("/api/partners/suppliers")
def get_suppliers() -> dict:
    with open(parent_dir / "data" / "partners_directory.json") as f:
        data = json.load(f)
    return {"suppliers": data["suppliers"], "total": len(data["suppliers"])}


@app.get("/api/partners/retailers")
def get_retailers() -> dict:
    with open(parent_dir / "data" / "partners_directory.json") as f:
        data = json.load(f)
    return {"retailers": data["retailers"], "total": len(data["retailers"])}


@app.get("/api/partners/suppliers/{supplier_id}")
def get_supplier(supplier_id: str) -> dict:
    with open(parent_dir / "data" / "partners_directory.json") as f:
        data = json.load(f)
    supplier = next((s for s in data["suppliers"] if s["supplier_id"] == supplier_id), None)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@app.get("/api/partners/retailers/{retailer_id}")
def get_retailer(retailer_id: str) -> dict:
    with open(parent_dir / "data" / "partners_directory.json") as f:
        data = json.load(f)
    retailer = next((r for r in data["retailers"] if r["retailer_id"] == retailer_id), None)
    if not retailer:
        raise HTTPException(status_code=404, detail="Retailer not found")
    return retailer


# ─── Sessions ─────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions() -> dict:
    return {
        "total": len(sessions_db),
        "sessions": [
            {
                "session_id": s.session_id,
                "product_name": s.product_name,
                "status": s.status,
                "initiator": s.initiator,
                "created_at": s.created_at,
            }
            for s in sessions_db.values()
        ],
    }


@app.get("/api/sessions/supplier/{supplier_id}")
def get_supplier_sessions(supplier_id: str) -> dict:
    rows = [s for s in sessions_db.values() if getattr(s, "supplier_id", None) == supplier_id]
    return {"supplier_id": supplier_id, "total": len(rows), "sessions": [s.dict() for s in rows]}


@app.get("/api/sessions/retailer/{retailer_id}")
def get_retailer_sessions(retailer_id: str) -> dict:
    rows = [s for s in sessions_db.values() if getattr(s, "retailer_id", None) == retailer_id]
    return {"retailer_id": retailer_id, "total": len(rows), "sessions": [s.dict() for s in rows]}


@app.get("/api/negotiations/{session_id}")
def get_session(session_id: str) -> NegotiationSession:
    session = sessions_db.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# ─── Negotiation Flow ─────────────────────────────────────────────────────────

@app.post("/api/negotiations/initiate")
def initiate_negotiation(request: InitiateNegotiationRequest) -> dict:
    """Start a new negotiation session."""
    session_id = str(uuid.uuid4())
    other_role = "retailer" if request.initiator == AgentRole.SUPPLIER else "supplier"
    session = NegotiationSession(
        session_id=session_id,
        product_id=request.product_id,
        product_name=request.product_name,
        initiator=request.initiator,
        initial_offer=request.initial_offer,
        supplier_id=request.supplier_id,
        retailer_id=request.retailer_id,
        status=SessionStatus.PENDING_LIMITS,
        status_message=f"Waiting for {other_role} to set limits",
    )
    sessions_db[session_id] = session
    logger.info(f"Negotiation initiated: {session_id} by {request.initiator.value} for {request.product_name}")
    return session.dict()


@app.post("/api/negotiations/set-limits")
def set_limits(request: SetLimitsRequest) -> dict:
    """Set limits for one party; starts negotiation once both are set."""
    session = sessions_db.get(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.role == AgentRole.SUPPLIER:
        session.supplier_limits = request.limits
    else:
        session.retailer_limits = request.limits

    session.updated_at = datetime.now().isoformat()

    if session.supplier_limits and session.retailer_limits:
        session = orchestrator.start_negotiation(session)
        sessions_db[request.session_id] = session

    return {
        "session_id": request.session_id,
        "status": session.status,
        "message": session.status_message,
        "zopa_exists": session.zopa_exists,
        "zopa_range": (
            f"{session.zopa_min:.2f} - {session.zopa_max:.2f} EUR"
            if session.zopa_exists else None
        ),
    }


@app.post("/api/negotiations/{session_id}/negotiate")
def run_negotiation(session_id: str, max_rounds: int = 10) -> dict:
    """Run autonomous negotiation rounds until convergence or max rounds."""
    session = sessions_db.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.NEGOTIATING:
        raise HTTPException(status_code=400, detail=f"Cannot negotiate: status is {session.status}")

    rounds_executed = 0
    while session.status == SessionStatus.NEGOTIATING and rounds_executed < max_rounds:
        session = orchestrator.run_negotiation_round(session)
        sessions_db[session_id] = session
        rounds_executed += 1

    return {
        "session_id": session_id,
        "status": session.status,
        "message": session.status_message,
        "rounds_completed": rounds_executed,
        "total_rounds": session.current_round,
    }


@app.post("/api/negotiations/approve")
def approve_deal(request: ApprovalRequest) -> dict:
    """Approve or reject a deal (legacy endpoint)."""
    session = sessions_db.get(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.approved:
        session = orchestrator.approve_deal(session, request.role)
    else:
        session = orchestrator.reject_deal(session, request.role, request.reason or "No reason provided")

    sessions_db[request.session_id] = session
    return {
        "session_id": request.session_id,
        "status": session.status,
        "message": session.status_message,
        "supplier_approved": session.supplier_approved,
        "retailer_approved": session.retailer_approved,
    }


@app.post("/api/negotiations/{session_id}/set-supplier-constraints")
def set_supplier_constraints_endpoint(session_id: str, limits: PartyLimits) -> dict:
    """Supplier sets negotiation constraints (floor price, min volume, etc.)."""
    session = sessions_db.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.supplier_limits = limits
    session.updated_at = datetime.now().isoformat()

    if session.supplier_limits and session.retailer_limits:
        session = orchestrator.start_negotiation(session)

    sessions_db[session_id] = session
    logger.info(f"Session {session_id}: Supplier constraints set")
    return session.dict()


@app.post("/api/negotiations/{session_id}/set-retailer-constraints")
def set_retailer_constraints(session_id: str, limits: PartyLimits) -> dict:
    """Retailer sets constraints; triggers ZOPA analysis."""
    session = sessions_db.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.retailer_limits = limits
    session.status = SessionStatus.CONSTRAINTS_SET
    session.updated_at = datetime.now().isoformat()

    zopa_analysis = orchestrator.check_zopa_with_recommendations(session)

    if zopa_analysis.zopa_exists:
        session.zopa_min = zopa_analysis.zopa_min
        session.zopa_max = zopa_analysis.zopa_max
        session.zopa_exists = True
        session.status = SessionStatus.NEGOTIATING
        session.status_message = (
            f"ZOPA exists: {zopa_analysis.zopa_min:.2f} - {zopa_analysis.zopa_max:.2f} EUR. Ready to negotiate."
        )
    else:
        session.status = SessionStatus.NO_ZOPA
        gap = zopa_analysis.gap_amount or 0.0
        session.status_message = (
            f"No ZOPA. Gap: {gap:.2f} EUR. {zopa_analysis.recommendation or 'No recommendation'}"
        )

    sessions_db[session_id] = session
    logger.info(f"Session {session_id}: Retailer constraints set, ZOPA={zopa_analysis.zopa_exists}")
    return {"session": session.dict(), "zopa_analysis": zopa_analysis.dict()}


@app.post("/api/negotiations/{session_id}/approve")
def approve_deal_by_session(session_id: str, body: RoleActionBody) -> dict:
    """Approve the negotiated deal for this session."""
    session = sessions_db.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        role = AgentRole(body.role.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role '{body.role}'")
    session = orchestrator.approve_deal(session, role)
    sessions_db[session_id] = session
    logger.info(f"Session {session_id}: Approved by {role.value}")
    return session.dict()


@app.post("/api/negotiations/{session_id}/reject")
def reject_deal_by_session(session_id: str, body: RejectBody) -> dict:
    """Reject the negotiated deal for this session."""
    session = sessions_db.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        role = AgentRole(body.role.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role '{body.role}'")
    session = orchestrator.reject_deal(session, role, body.reason or "No reason provided")
    sessions_db[session_id] = session
    logger.info(f"Session {session_id}: Rejected by {role.value}")
    return session.dict()


# ─── Requests (Retailer → Supplier) ──────────────────────────────────────────

@app.get("/api/requests")
def list_requests() -> dict:
    return {
        "total": len(requests_db),
        "requests": [
            {
                "request_id": r.request_id,
                "retailer_name": r.retailer_name,
                "raw_request": r.raw_request,
                "category": r.product_category,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in requests_db.values()
        ],
    }


@app.get("/api/requests/{request_id}")
def get_request(request_id: str) -> ProductRequest:
    request = requests_db.get(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return request


@app.get("/api/requests/for-supplier/{supplier_id}")
def get_requests_for_supplier(supplier_id: str) -> dict:
    """Return all requests visible to a supplier (broadcast + targeted)."""
    visible = [
        r for r in requests_db.values()
        if not r.target_supplier_ids or supplier_id in r.target_supplier_ids
    ]
    return {"supplier_id": supplier_id, "total": len(visible), "requests": [r.dict() for r in visible]}


@app.post("/api/requests/create")
def create_retailer_request(request: CreateRequestModel) -> dict:
    """Retailer submits a free-text product request; agent structures it."""
    try:
        product_request = request_agent.process_retailer_request(
            raw_request=request.raw_request,
            retailer_id=request.retailer_id,
            retailer_name=request.retailer_name,
        )
        requests_db[product_request.request_id] = product_request
        logger.info(f"Request created: {product_request.request_id} by {request.retailer_name}")
        return {
            "request_id": product_request.request_id,
            "status": "created",
            "structured_data": {
                "category": product_request.product_category,
                "product_description": product_request.product_description,
                "volume": product_request.estimated_volume,
                "timeframe": product_request.timeframe,
                "budget_range": product_request.budget_range,
                "quality_tier": product_request.quality_tier,
                "preferred_payment_terms": product_request.preferred_payment_terms,
                "special_requirements": product_request.special_requirements,
                "market_context": product_request.market_context,
            },
        }
    except Exception as e:
        logger.error(f"Failed to create request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/requests/create-targeted")
def create_targeted_request(request: CreateTargetedRequestModel) -> dict:
    """Retailer request with optional supplier targeting."""
    try:
        product_request = request_agent.process_retailer_request(
            raw_request=request.raw_request,
            retailer_id=request.retailer_id,
            retailer_name=request.retailer_name,
        )
        product_request.target_supplier_ids = request.target_supplier_ids
        requests_db[product_request.request_id] = product_request

        target_info = (
            "all suppliers" if not request.target_supplier_ids
            else f"{len(request.target_supplier_ids)} specific supplier(s)"
        )
        logger.info(f"Targeted request created: {product_request.request_id} by {request.retailer_name} for {target_info}")
        return {
            "request_id": product_request.request_id,
            "status": "created",
            "target": target_info,
            "structured_data": {
                "category": product_request.product_category,
                "product_description": product_request.product_description,
                "volume": product_request.estimated_volume,
                "timeframe": product_request.timeframe,
                "budget_range": product_request.budget_range,
                "quality_tier": product_request.quality_tier,
                "preferred_payment_terms": product_request.preferred_payment_terms,
                "special_requirements": product_request.special_requirements,
                "market_context": product_request.market_context,
            },
        }
    except Exception as e:
        logger.error(f"Failed to create targeted request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/requests/{request_id}/match-products")
def match_products_for_request(request_id: str, supplier_id: str) -> dict:
    """Supplier agent semantically matches products to a retailer request."""
    product_request = requests_db.get(request_id)
    if not product_request:
        raise HTTPException(status_code=404, detail="Request not found")

    try:
        with open(parent_dir / "data" / "products_catalog.json") as f:
            catalog = json.load(f)

        supplier_products = [p for p in catalog["products"] if p.get("supplier_id") == supplier_id]
        if not supplier_products:
            return {"request_id": request_id, "matches": [], "message": "No products found for this supplier"}

        matches = request_agent.match_products(request=product_request, available_products=supplier_products)

        product_request.matched_products = [m.dict() for m in matches]
        product_request.status = "products_matched"
        product_request.updated_at = datetime.now().isoformat()
        requests_db[request_id] = product_request

        logger.info(f"Matched {len(matches)} products for request {request_id}")
        return {"request_id": request_id, "matches": [m.dict() for m in matches], "total_matches": len(matches)}

    except Exception as e:
        logger.error(f"Failed to match products: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/requests/{request_id}/create-offer")
def create_offer_from_request(request_id: str, data: CreateOfferFromRequestModel) -> dict:
    """Supplier creates an offer from a matched product."""
    product_request = requests_db.get(request_id)
    if not product_request:
        raise HTTPException(status_code=404, detail="Request not found")

    try:
        with open(parent_dir / "data" / "products_catalog.json") as f:
            catalog = json.load(f)

        product = next((p for p in catalog["products"] if p["product_id"] == data.product_id), None)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        initial_offer = NegotiationOffer(
            unit_price=data.unit_price,
            volume=data.volume,
            delivery_days=data.delivery_days,
            payment_terms=data.payment_terms,
            justification=data.notes or f"Offer for {product['name']}",
        )
        supplier_limits = PartyLimits(
            min_price=product.get("min_price_eur", data.unit_price * 0.8),
            min_volume=product.get("min_order_quantity", 1),
            max_volume=product.get("max_monthly_capacity", 100_000),
            acceptable_payment_terms=[product.get("default_payment_terms", data.payment_terms)],
        )
        session_id = str(uuid.uuid4())
        session = NegotiationSession(
            session_id=session_id,
            product_id=data.product_id,
            product_name=product["name"],
            initiator=AgentRole.SUPPLIER,
            initial_offer=initial_offer,
            supplier_limits=supplier_limits,
            status=SessionStatus.PENDING_LIMITS,
            status_message="Offer created — waiting for retailer to set limits",
            supplier_id=data.supplier_id,
            retailer_id=product_request.retailer_id,
        )
        sessions_db[session_id] = session

        product_request.status = "offer_created"
        product_request.updated_at = datetime.now().isoformat()
        requests_db[request_id] = product_request

        logger.info(f"Offer created from request {request_id}: session {session_id}")
        return {"session_id": session_id, "request_id": request_id, "offer": initial_offer.dict(), "status": "offer_sent"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create offer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── Direct Offers (Supplier → Retailer, no prior request) ───────────────────

@app.post("/api/offers/create-direct")
def create_direct_offer(request: DirectOfferRequest) -> dict:
    """Supplier pushes a direct offer to a retailer without a prior request."""
    try:
        with open(parent_dir / "data" / "products_catalog.json") as f:
            catalog = json.load(f)

        product = next((p for p in catalog["products"] if p["product_id"] == request.product_id), None)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        if product.get("supplier_id") != request.supplier_id:
            raise HTTPException(status_code=403, detail="Product does not belong to this supplier")

        session_id = str(uuid.uuid4())
        supplier_limits = PartyLimits(
            min_price=request.supplier_constraints.get("min_price", product["min_price_eur"]),
            min_volume=product["min_order_quantity"],
            max_volume=product["max_monthly_capacity"],
            acceptable_payment_terms=request.supplier_constraints.get(
                "payment_terms", [product["default_payment_terms"]]
            ),
        )
        session = NegotiationSession(
            session_id=session_id,
            product_id=request.product_id,
            product_name=product["name"],
            initiator=AgentRole.SUPPLIER,
            initial_offer=request.offer_details,
            supplier_limits=supplier_limits,
            status=SessionStatus.OFFER_SENT,
            status_message=f"Direct offer from {request.supplier_name} to {request.retailer_name}",
            supplier_id=request.supplier_id,
            retailer_id=request.retailer_id,
        )
        sessions_db[session_id] = session

        logger.info(f"Direct offer: {session_id} from {request.supplier_name} to {request.retailer_name} for {product['name']}")
        return {
            "session_id": session_id,
            "status": "direct_offer_sent",
            "message": f"Offer sent to {request.retailer_name}",
            "offer": request.offer_details.dict(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create direct offer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/offers/for-retailer/{retailer_id}")
def get_offers_for_retailer(retailer_id: str) -> dict:
    """Return all pending direct offers for a specific retailer."""
    offer_sessions = [
        s for s in sessions_db.values()
        if getattr(s, "retailer_id", None) == retailer_id
        and getattr(s, "initiator", None) == AgentRole.SUPPLIER
        and getattr(s, "status", None) == SessionStatus.OFFER_SENT
    ]
    offers = [
        {
            "offer_id": s.session_id,
            "supplier_id": s.supplier_id,
            "retailer_id": s.retailer_id,
            "product_id": getattr(s, "product_id", ""),
            "product_name": getattr(s, "product_name", ""),
            "unit_price": s.initial_offer.unit_price,
            "volume": s.initial_offer.volume,
            "delivery_days": s.initial_offer.delivery_days,
            "payment_terms": s.initial_offer.payment_terms,
            "notes": getattr(s.initial_offer, "notes", None) or getattr(s.initial_offer, "justification", None),
            "status": "pending",
            "created_at": getattr(s, "created_at", datetime.now().isoformat()),
        }
        for s in offer_sessions
    ]
    return {"retailer_id": retailer_id, "total": len(offers), "offers": offers}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)