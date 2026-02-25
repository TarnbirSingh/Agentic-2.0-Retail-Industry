"""
models/negotiation_models.py
────────────────────────────
Core Pydantic data models for the negotiation framework.

These models are the single source of truth for the data structures
exchanged between agents, the orchestrator, the validator, and the
KPI tracker.

All models use Pydantic v2 syntax.  They are strictly typed, validated
on construction, and serialisable to/from JSON.

Model hierarchy
---------------
  NegotiationOffer    – one offer from one agent in one round
  RoundRecord         – wraps an offer with metadata (round, role, validity)
  NegotiationState    – the full running state passed through the system
  AgentRole           – enum identifying which party generated an offer
"""

import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# ENUMERATIONS
# ─────────────────────────────────────────────────────────────────────────────

class AgentRole(str, Enum):
    """Identifies which party is making an offer."""
    SUPPLIER = "supplier"
    RETAIL   = "retail"


# ─────────────────────────────────────────────────────────────────────────────
# CORE OFFER MODEL
# ─────────────────────────────────────────────────────────────────────────────

class NegotiationOffer(BaseModel):
    """
    Structured representation of a single negotiation offer.

    This is the *primary output type* for both agents.  The LLM must
    produce a JSON object that validates against this schema.  Any
    offer that does not parse into this model is rejected before it
    ever reaches the validation layer.

    Fields
    ------
    unit_price      : Proposed price per unit in EUR (must be > 0).
    volume          : Proposed order volume in units (must be > 0).
    delivery_window : Proposed delivery window, e.g. "Q3" or "Q4".
    payment_terms   : Payment terms, e.g. "Net30", "Net60".
    justification   : Business reasoning.  At least 10 characters.
    """

    unit_price: float = Field(
        ...,
        gt=0,
        description="Proposed unit price in EUR.",
    )
    volume: int = Field(
        ...,
        gt=0,
        description="Proposed order volume in units.",
    )
    delivery_window: str = Field(
        ...,
        min_length=1,
        description="Delivery window identifier, e.g. 'Q3', 'Q4'.",
    )
    payment_terms: str = Field(
        ...,
        min_length=1,
        description="Payment terms, e.g. 'Net30', 'Net60'.",
    )
    justification: str = Field(
        ...,
        min_length=10,
        description="Business justification for this offer (≥10 chars).",
    )

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("delivery_window")
    @classmethod
    def normalise_delivery_window(cls, v: str) -> str:
        """Strip whitespace and upper-case for consistent comparison."""
        return v.strip().upper()

    @field_validator("payment_terms")
    @classmethod
    def normalise_payment_terms(cls, v: str) -> str:
        return v.strip()

    @field_validator("justification")
    @classmethod
    def strip_justification(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 10:
            raise ValueError("justification must be at least 10 characters")
        return stripped

    @field_validator("unit_price")
    @classmethod
    def round_unit_price(cls, v: float) -> float:
        """Round to 4 decimal places to avoid floating-point noise."""
        return round(v, 4)

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def total_value(self) -> float:
        """Total contract value (unit_price × volume) in EUR."""
        return round(self.unit_price * self.volume, 2)

    # ── Serialisation helpers ─────────────────────────────────────────────────

    def to_log_dict(self) -> dict:
        """
        Compact dict suitable for logging and JSON export.
        Adds the computed ``total_value`` field.
        """
        return {
            "unit_price":       self.unit_price,
            "volume":           self.volume,
            "delivery_window":  self.delivery_window,
            "payment_terms":    self.payment_terms,
            "justification":    self.justification,
            "total_value":      self.total_value,
        }

    def to_prompt_str(self) -> str:
        """
        Human-readable one-line summary for injection into LLM prompts.
        Keeps prompt tokens minimal.
        """
        return (
            f"unit_price={self.unit_price} EUR, "
            f"volume={self.volume}, "
            f"delivery_window={self.delivery_window}, "
            f"payment_terms={self.payment_terms}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ROUND RECORD  (offer + metadata)
# ─────────────────────────────────────────────────────────────────────────────

class RoundRecord(BaseModel):
    """
    Immutable record of a single negotiation round.

    Wraps a ``NegotiationOffer`` with round metadata so the full
    negotiation history can be replayed or exported for analysis.

    Fields
    ------
    round_number        : 1-based round counter.
    role                : Which agent produced this offer.
    offer               : The actual offer.
    is_valid            : Whether the offer passed validation.
    validation_message  : Error detail if ``is_valid`` is False, else "".
    timestamp           : ISO-8601 creation timestamp (UTC).
    """

    round_number: int
    role: AgentRole
    offer: NegotiationOffer
    is_valid: bool
    validation_message: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.utcnow().isoformat()
    )

    def to_history_dict(self) -> dict:
        """Compact representation for LLM context injection."""
        return {
            "round":            self.round_number,
            "role":             self.role.value,
            "unit_price":       self.offer.unit_price,
            "volume":           self.offer.volume,
            "delivery_window":  self.offer.delivery_window,
            "payment_terms":    self.offer.payment_terms,
            "justification":    self.offer.justification,
            "valid":            self.is_valid,
        }


# ─────────────────────────────────────────────────────────────────────────────
# NEGOTIATION STATE
# ─────────────────────────────────────────────────────────────────────────────

class NegotiationState(BaseModel):
    """
    Complete, mutable negotiation state.

    Passed by reference through the orchestration loop.  Each round
    appends a ``RoundRecord`` to ``history``.  All agents receive the
    *same* state object – they must not mutate it directly.

    The orchestrator is the *only* component that updates this object.

    Fields
    ------
    current_round       : Round currently being executed (1-based).
    history             : Ordered list of all past rounds.
    is_agreement        : True once agreement conditions are met.
    termination_reason  : Human-readable reason for termination.
    """

    current_round: int = 0
    history: List[RoundRecord] = Field(default_factory=list)
    is_agreement: bool = False
    termination_reason: Optional[str] = None

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_last_offer_by_role(self, role: AgentRole) -> Optional[NegotiationOffer]:
        """
        Return the most recent *valid* offer from a specific role.

        Only valid offers are considered; invalid ones are ignored so
        that agents always respond to the last accepted position.
        """
        for record in reversed(self.history):
            if record.role == role and record.is_valid:
                return record.offer
        return None

    def get_last_offer(self) -> Optional[NegotiationOffer]:
        """Return the most recent offer regardless of role or validity."""
        return self.history[-1].offer if self.history else None

    def get_history_for_prompt(self, max_entries: int = 6) -> List[dict]:
        """
        Return a compact, JSON-serialisable history list for LLM prompts.

        Only valid offers are included.  Limited to ``max_entries``
        most-recent entries to keep token usage bounded.
        """
        valid_records = [r for r in self.history if r.is_valid]
        recent = valid_records[-max_entries:]
        return [r.to_history_dict() for r in reversed(recent)]  # newest first

    def get_price_gap(self) -> Optional[float]:
        """
        Return the absolute price gap between the last offers from each party.

        Returns None if at least one side has not yet made a valid offer.
        """
        last_supplier = self.get_last_offer_by_role(AgentRole.SUPPLIER)
        last_retail   = self.get_last_offer_by_role(AgentRole.RETAIL)
        if last_supplier is None or last_retail is None:
            return None
        return abs(last_supplier.unit_price - last_retail.unit_price)

    def to_summary_dict(self) -> dict:
        """Compact summary for logging at round end."""
        return {
            "current_round":        self.current_round,
            "is_agreement":         self.is_agreement,
            "termination_reason":   self.termination_reason,
            "rounds_recorded":      len(self.history),
            "price_gap":            self.get_price_gap(),
        }
