"""
validation/validator.py
───────────────────────
Rule-based, deterministic constraint validator.

IMPORTANT: This module contains ZERO LLM calls.

All validation is purely algorithmic.  This is a core architectural
requirement: constraint enforcement must be reproducible, auditable,
and independent of model behaviour.

Validation pipeline
-------------------
For each offer, the validator runs three sequential checks:

  1. Schema check    – handled implicitly by Pydantic at parse time.
                       The validator receives an already-parsed
                       ``NegotiationOffer``, so structural correctness
                       is guaranteed.  This step re-checks logical
                       consistency (positive price/volume, etc.).

  2. Constraint check – enforces business rules specific to the agent
                        role (supplier floor, retail margin, budget).

  3. Delivery window check – both roles must use an allowed window.

Each check returns a ``ValidationResult``.  The first failure short-
circuits the pipeline.

Design notes
------------
- ``ValidationResult`` is a lightweight immutable value object.
- ``Validator`` is stateless: the same instance can validate any offer.
- The validator is the *single source of truth* for constraint compliance.
  Neither agents nor the orchestrator duplicate this logic.
"""

import logging
from dataclasses import dataclass

from models.constraints import ConstraintModel
from models.negotiation_models import AgentRole, NegotiationOffer

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# RESULT VALUE OBJECT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidationResult:
    """
    Immutable result of a single validation run.

    Attributes
    ----------
    is_valid        : True if the offer passes all checks.
    error_message   : Empty string if valid; human-readable error otherwise.
    """

    is_valid: bool
    error_message: str = ""

    @classmethod
    def ok(cls) -> "ValidationResult":
        """Convenience constructor for a passing result."""
        return cls(is_valid=True, error_message="")

    @classmethod
    def fail(cls, message: str) -> "ValidationResult":
        """Convenience constructor for a failing result."""
        return cls(is_valid=False, error_message=message)

    def __bool__(self) -> bool:
        """Allow ``if validation_result:`` usage."""
        return self.is_valid

    def __repr__(self) -> str:
        if self.is_valid:
            return "ValidationResult(PASS)"
        return f"ValidationResult(FAIL: {self.error_message!r})"


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATOR
# ─────────────────────────────────────────────────────────────────────────────

class Validator:
    """
    Deterministic, rule-based negotiation offer validator.

    Parameters
    ----------
    constraints : ``ConstraintModel`` for the current experiment.
                  Immutable; set once at construction time.

    Usage
    -----
    >>> validator = Validator(constraints)
    >>> result = validator.validate(offer, role="supplier")
    >>> if not result:
    ...     logger.warning(result.error_message)
    """

    def __init__(self, constraints: ConstraintModel) -> None:
        self.constraints = constraints

    # ── Public API ────────────────────────────────────────────────────────────

    def validate(
        self,
        offer: NegotiationOffer,
        role: str,
    ) -> ValidationResult:
        """
        Run the full validation pipeline on a single offer.

        Parameters
        ----------
        offer : Parsed ``NegotiationOffer`` from an agent.
        role  : "supplier" or "retail" (case-insensitive).

        Returns
        -------
        ValidationResult
        """
        role_normalised = role.lower().strip()
        if role_normalised not in (AgentRole.SUPPLIER.value, AgentRole.RETAIL.value):
            return ValidationResult.fail(
                f"Unknown role '{role}'. Expected 'supplier' or 'retail'."
            )

        # Step 1: Logical consistency (role-agnostic)
        result = self._check_logical_consistency(offer)
        if not result:
            logger.debug("Logical consistency check FAILED: %s", result.error_message)
            return result

        # Step 2: Delivery window (both roles)
        result = self._check_delivery_window(offer)
        if not result:
            logger.debug("Delivery window check FAILED: %s", result.error_message)
            return result

        # Step 3: Role-specific business constraints
        if role_normalised == AgentRole.SUPPLIER.value:
            result = self._check_supplier_constraints(offer)
        else:
            result = self._check_retail_constraints(offer)

        if not result:
            logger.debug(
                "%s constraint check FAILED: %s", role_normalised, result.error_message
            )

        return result

    def validate_feasibility(self) -> ValidationResult:
        """
        Validate that the constraint configuration itself permits agreement.

        Call this once at experiment start to catch mis-configured
        scenarios before any LLM calls are made.
        """
        min_s = self.constraints.min_supplier_price
        max_r = self.constraints.max_acceptable_unit_price()
        if min_s > max_r:
            return ValidationResult.fail(
                f"Constraint deadlock detected before negotiation started. "
                f"min_supplier_price ({min_s:.2f}) > max_acceptable_unit_price ({max_r:.2f}). "
                f"No agreement is possible with these constraints."
            )
        return ValidationResult.ok()

    # ── Private checks ────────────────────────────────────────────────────────

    def _check_logical_consistency(self, offer: NegotiationOffer) -> ValidationResult:
        """
        Verify basic numeric sanity (positive price, positive volume, etc.).

        Pydantic already enforces ``gt=0`` on the model fields, but these
        checks guard against any future relaxation of those constraints.
        """
        if offer.unit_price <= 0:
            return ValidationResult.fail(
                f"unit_price must be > 0, got {offer.unit_price}."
            )
        if offer.volume <= 0:
            return ValidationResult.fail(
                f"volume must be > 0, got {offer.volume}."
            )
        if not offer.payment_terms.strip():
            return ValidationResult.fail("payment_terms must not be empty.")
        if len(offer.justification.strip()) < 10:
            return ValidationResult.fail(
                "justification is too short (< 10 characters)."
            )
        return ValidationResult.ok()

    def _check_delivery_window(self, offer: NegotiationOffer) -> ValidationResult:
        """Verify delivery window is in the allowed list."""
        if not self.constraints.is_delivery_window_allowed(offer.delivery_window):
            return ValidationResult.fail(
                f"delivery_window '{offer.delivery_window}' is not allowed. "
                f"Permitted: {self.constraints.allowed_delivery_windows}."
            )
        return ValidationResult.ok()

    def _check_supplier_constraints(self, offer: NegotiationOffer) -> ValidationResult:
        """
        Enforce supplier-side business rules.

        Rule: unit_price must be ≥ min_supplier_price.
        """
        min_price = self.constraints.min_supplier_price
        if offer.unit_price < min_price:
            return ValidationResult.fail(
                f"Supplier unit_price {offer.unit_price:.4f} EUR is below "
                f"the minimum floor price {min_price:.4f} EUR."
            )
        return ValidationResult.ok()

    def _check_retail_constraints(self, offer: NegotiationOffer) -> ValidationResult:
        """
        Enforce retail-side business rules.

        Rules:
          1. Gross margin must be ≥ min_margin.
          2. Total spend (price × volume) must be ≤ max_budget.
        """
        c = self.constraints

        # ── Margin check ──────────────────────────────────────────────────────
        margin = c.calculate_margin(offer.unit_price)
        if margin < c.min_margin:
            return ValidationResult.fail(
                f"Retail offer unit_price {offer.unit_price:.4f} EUR yields "
                f"margin {margin:.2%}, which is below the required "
                f"minimum {c.min_margin:.2%}. "
                f"Maximum acceptable unit price: {c.max_acceptable_unit_price():.4f} EUR."
            )

        # ── Budget check ──────────────────────────────────────────────────────
        total_cost = round(offer.unit_price * offer.volume, 2)
        if total_cost > c.max_budget:
            return ValidationResult.fail(
                f"Total procurement cost {total_cost:.2f} EUR "
                f"(unit_price={offer.unit_price:.4f} × volume={offer.volume}) "
                f"exceeds max_budget {c.max_budget:.2f} EUR."
            )

        return ValidationResult.ok()
