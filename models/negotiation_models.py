"""
models/negotiation_models.py
────────────────────────────
Simplified negotiation models for agent-to-agent B2B negotiations.

Core entities:
- NegotiationOffer: Single offer in a negotiation round
- NegotiationRound: Complete round with offer + validation
- NegotiationSession: Full negotiation state
- NegotiationResult: Final outcome
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

# Import utility models for multi-attribute negotiation
try:
    from models.utility import AttributeWeights, NegotiationPreferences
except ImportError:
    # Fallback if utility.py not loaded yet
    AttributeWeights = None
    NegotiationPreferences = None

# Import agent reasoning models for transparency
try:
    from models.agent_reasoning import AgentReasoning
except ImportError:
    # Fallback if agent_reasoning.py not loaded yet
    AgentReasoning = None


class AgentRole(str, Enum):
    """Who is making the offer."""
    SUPPLIER = "supplier"
    RETAILER = "retailer"


class SessionStatus(str, Enum):
    """Current state of the negotiation."""
    # New flow states
    REQUEST_CREATED = "request_created"  # Retailer created request
    PRODUCTS_MATCHED = "products_matched"  # Agent matched products for supplier
    OFFER_SENT = "offer_sent"  # Supplier sent offer to retailer
    CONSTRAINTS_SET = "constraints_set"  # Retailer set negotiation constraints
    ZOPA_CHECK = "zopa_check"  # Checking if ZOPA exists
    
    # Old flow states (kept for compatibility)
    PENDING_LIMITS = "pending_limits"  # Waiting for counterparty to set limits
    NO_ZOPA = "no_zopa"  # No zone of possible agreement
    NEGOTIATING = "negotiating"  # Agents are negotiating autonomously
    PENDING_APPROVAL = "pending_approval"  # Final offer needs human approval
    ACCEPTED = "accepted"  # Deal accepted by both parties
    REJECTED = "rejected"  # Deal rejected
    FAILED = "failed"  # Technical failure
    MAX_ROUNDS = "max_rounds_reached"  # No agreement after max rounds
    
    # NEW: HITL & Renegotiate states
    HITL_REQUIRED = "hitl_required"  # Human intervention required (ZOPA breach, etc.)
    PAUSED = "paused"  # Human paused negotiation to review
    RENEGOTIATING = "renegotiating"  # Restarting negotiation with new constraints after rejection


class NegotiationOffer(BaseModel):
    """Single offer in a negotiation round."""
    unit_price: float = Field(..., description="Price per unit in EUR")
    volume: int = Field(..., description="Number of units")
    delivery_days: int = Field(..., description="Delivery lead time in days")
    payment_terms: str = Field(..., description="Payment terms (e.g., 'Net 30')")
    justification: str = Field(default="", description="Agent's reasoning for this offer")
    leverage_used: Optional[str] = Field(default=None, description="Negotiation leverage used (e.g., 'volume discount', 'fast delivery')")


class NegotiationRound(BaseModel):
    """Complete round with offer and validation."""
    round_number: int
    role: AgentRole
    offer: NegotiationOffer          # Clamped offer used for negotiation
    raw_offer: Optional[NegotiationOffer] = Field(
        default=None,
        description=(
            "Unmodified LLM output before clamping. "
            "CSR evaluation MUST use this field to measure true constraint adherence. "
            "None if no clamping occurred (offer == raw_offer)."
        ),
    )
    is_valid: bool
    validation_message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    # Transparency: Agent's reasoning for this round (NEW - Agentic 2.0)
    agent_reasoning: Optional[dict] = Field(
        default=None,
        description="Agent's complete reasoning data for transparency (AgentReasoning as dict)"
    )


class PartyLimits(BaseModel):
    """Constraints/limits set by one party."""
    min_price: Optional[float] = None  # Supplier's floor price
    max_price: Optional[float] = None  # Retailer's ceiling price
    min_volume: Optional[int] = None
    max_volume: Optional[int] = None
    max_delivery_days: Optional[int] = None
    acceptable_payment_terms: list[str] = Field(default_factory=list)
    target_margin: Optional[float] = None  # Retailer's target margin (0-1)
    retail_price: Optional[float] = None  # Retailer's planned retail price


class NegotiationSession(BaseModel):
    """Complete negotiation session state."""
    session_id: str
    product_id: str
    product_name: str
    
    # Who initiated
    initiator: AgentRole
    
    # Partner IDs (for filtering in multi-partner SaaS)
    supplier_id: Optional[str] = None
    retailer_id: Optional[str] = None
    
    # Initial offer/request
    initial_offer: NegotiationOffer
    
    # Limits set by both parties
    supplier_limits: Optional[PartyLimits] = None
    retailer_limits: Optional[PartyLimits] = None
    
    # Product catalog data (injected at session creation for data-driven agents)
    product_data: Optional[dict] = None  # Full product entry from products_catalog.json

    # Multi-attribute preferences (NEW - for utility-based negotiation)
    supplier_preferences: Optional[dict] = None  # NegotiationPreferences as dict
    retailer_preferences: Optional[dict] = None  # NegotiationPreferences as dict
    
    # ZOPA analysis
    zopa_min: Optional[float] = None  # Lowest acceptable price
    zopa_max: Optional[float] = None  # Highest acceptable price
    zopa_exists: bool = False
    
    # Negotiation history
    rounds: list[NegotiationRound] = Field(default_factory=list)
    current_round: int = 0
    max_rounds: int = 50
    
    # Status
    status: SessionStatus = SessionStatus.PENDING_LIMITS
    status_message: str = ""
    
    # Approvals for final offer
    supplier_approved: bool = False
    retailer_approved: bool = False
    
    # Timestamps
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ProductRequest(BaseModel):
    """Retailer's product request (free-text input processed by agent)."""
    request_id: str
    retailer_id: str
    retailer_name: str
    
    # Raw input from human
    raw_request: str = Field(..., description="Free-text request from retailer")
    
    # Agent-structured fields
    product_category: Optional[str] = None
    estimated_volume: Optional[int] = None
    timeframe: Optional[str] = None
    special_requirements: Optional[str] = None
    
    # NEW: Extended structured fields extracted by LLM
    product_description: Optional[str] = None  # More detailed product description
    budget_range: Optional[str] = None  # e.g., "30-50€ pro Stück" or "EUR 25,000 total"
    quality_tier: Optional[str] = None  # e.g., "Mittelklasse", "Premium", "Economy"
    preferred_payment_terms: Optional[str] = None  # e.g., "Net30", "Net60", "Prepayment"
    
    # Agent enrichment
    market_context: Optional[str] = None  # e.g., "Typical price range: 35-60€"
    
    # Matched products (filled after supplier agent processes)
    matched_products: list[dict] = Field(default_factory=list)
    
    # Request routing (for multi-partner SaaS)
    target_supplier_ids: Optional[list[str]] = None  # None = broadcast to all, or specific supplier IDs
    
    # Status
    status: str = "pending_supplier"  # pending_supplier, products_matched, offer_created
    
    # Timestamps
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ProductMatch(BaseModel):
    """A product matched by the supplier agent for a request."""
    product_id: str
    product_name: str
    relevance_score: float = Field(..., description="How well product matches request (0-1)")
    reasoning: str = Field(..., description="Agent's reasoning for the match")
    
    # Product details (from DB)
    base_price: float
    min_price: float
    typical_retail_price: float
    min_order_quantity: int
    max_monthly_capacity: int
    lead_time_days: int
    default_payment_terms: str


class ZOPAAnalysis(BaseModel):
    """Analysis of Zone of Possible Agreement with recommendations."""
    zopa_exists: bool
    zopa_min: Optional[float] = None
    zopa_max: Optional[float] = None
    gap_amount: Optional[float] = None  # How much is missing if no ZOPA
    
    # Agent recommendations
    recommendation: str = ""  # "Increase retailer max_price by 3€" or "Both parties adjust"
    supplier_suggestion: Optional[str] = None  # Specific suggestion for supplier
    retailer_suggestion: Optional[str] = None  # Specific suggestion for retailer
    
    # Alternative solutions
    alternative_approaches: list[str] = Field(default_factory=list)  # e.g., ["Higher volume discount", "Extended payment terms"]


class HITLTriggerReason(str, Enum):
    """Why human intervention was triggered."""
    ZOPA_BREACH = "zopa_breach"  # Agent tried to go outside ZOPA
    LARGE_PRICE_JUMP = "large_price_jump"  # Price change >5% in one round
    MAX_ROUNDS_APPROACHING = "max_rounds_approaching"  # Only 1-2 rounds left
    NEGOTIATION_STALLED = "negotiation_stalled"  # No progress for 3+ rounds
    AGENT_UNCERTAINTY = "agent_uncertainty"  # Agent confidence score too low
    MANUAL_REVIEW = "manual_review"  # User explicitly requested pause


class HITLSeverity(str, Enum):
    """How critical is the intervention."""
    INFO = "info"  # FYI, can continue without intervention
    WARNING = "warning"  # Recommended to review, but optional
    CRITICAL = "critical"  # Must intervene, auto-pause negotiation


class HITLTrigger(BaseModel):
    """Details about why HITL was triggered."""
    reason: HITLTriggerReason
    severity: HITLSeverity
    message: str = Field(..., description="Human-readable explanation")
    recommended_action: str = Field(..., description="What human should do")

    # Context data — always populate both party prices for clean UI display
    current_price: Optional[float] = None       # last round's price (legacy, prefer below)
    supplier_last_price: Optional[float] = None  # most recent supplier offer
    retailer_last_price: Optional[float] = None  # most recent retailer offer
    price_gap: Optional[float] = None            # abs diff between the two latest prices
    zopa_min: Optional[float] = None
    zopa_max: Optional[float] = None
    rounds_remaining: Optional[int] = None


class InterventionAction(str, Enum):
    """What human wants to do when intervening."""
    CONTINUE = "continue"  # Continue negotiation as-is
    ADJUST_CONSTRAINTS = "adjust_constraints"  # Set new limits and continue
    PAUSE = "pause"  # Pause for manual review
    ABORT = "abort"  # Stop negotiation completely


class InterventionRequest(BaseModel):
    """Human intervention input."""
    session_id: str
    action: InterventionAction
    new_limits: Optional[PartyLimits] = None  # If adjusting constraints
    notes: Optional[str] = None  # Human's reasoning


class RenegotiationContext(BaseModel):
    """Context from previous negotiation for learning."""
    previous_session_id: str
    rejection_reason: str
    final_offer_price: float
    final_offer_terms: dict
    rounds_completed: int
    key_sticking_points: list[str] = Field(default_factory=list)  # e.g., ["price_too_high", "volume_too_low"]


class RenegotiateRequest(BaseModel):
    """Request to restart negotiation with new constraints."""
    session_id: str
    role: AgentRole  # Who is renegotiating
    new_limits: PartyLimits
    renegotiation_context: Optional[RenegotiationContext] = None


class NegotiationResult(BaseModel):
    """Final outcome of a negotiation."""
    session_id: str
    agreement_reached: bool
    
    # Final deal terms (if agreement reached)
    final_price: Optional[float] = None
    final_volume: Optional[int] = None
    final_delivery_days: Optional[int] = None
    final_payment_terms: Optional[str] = None
    
    # Metrics
    total_rounds: int
    runtime_seconds: float
    price_movement: Optional[float] = None  # How much price changed from initial
    
    # Termination reason
    termination_reason: str
    
    # KPIs
    supplier_margin: Optional[float] = None  # If calculable
    retailer_margin: Optional[float] = None  # If retail price known
