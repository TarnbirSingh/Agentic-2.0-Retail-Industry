"""
models/constraints.py
─────────────────────
Business constraint model for the negotiation framework.

The ``ConstraintModel`` encodes all hard business rules that must be
satisfied by every offer.  These are enforced exclusively by the
``Validator`` – the LLM *never* enforces constraints directly.

Design principle
----------------
  Constraints are immutable per experiment.  They are constructed once,
  passed to the orchestrator, and forwarded (read-only) to agents and
  the validator.  No component may mutate constraints at runtime.
"""

from typing import List

from pydantic import BaseModel, Field, field_validator, model_validator


class ConstraintModel(BaseModel):
    """
    Hard business constraints for a negotiation experiment.

    All fields are validated on construction.  Any invalid constraint
    configuration raises a ``ValidationError`` before the experiment
    begins, giving fast-fail behaviour.

    Fields
    ------
    min_margin                : Minimum acceptable gross margin for the
                                retailer (0 < min_margin < 1).
                                Example: 0.25 = 25 %.
    min_supplier_price        : Floor price for the supplier.  The supplier
                                must never quote below this value (EUR).
    max_budget                : Maximum total spend allowed for the retailer
                                (unit_price × volume ≤ max_budget, EUR).
    allowed_delivery_windows  : Set of permissible delivery window codes.
                                Both agents must pick from this list.
    retail_selling_price      : The price at which the retailer sells to end
                                customers.  Used to compute gross margin.

    Derived quantities (computed methods, not stored fields)
    ---------------------------------------------------------
    max_acceptable_unit_price : Highest price the retailer can accept while
                                still meeting min_margin.
                                = retail_selling_price × (1 − min_margin)
    """

    min_margin: float = Field(
        ...,
        gt=0.0,
        lt=1.0,
        description="Retailer minimum gross margin (0–1 exclusive).",
    )
    min_supplier_price: float = Field(
        ...,
        gt=0.0,
        description="Supplier floor price per unit in EUR.",
    )
    max_budget: float = Field(
        ...,
        gt=0.0,
        description="Retailer maximum total procurement budget in EUR.",
    )
    allowed_delivery_windows: List[str] = Field(
        ...,
        min_length=1,
        description="Permitted delivery window codes, e.g. ['Q3', 'Q4'].",
    )
    retail_selling_price: float = Field(
        ...,
        gt=0.0,
        description="Retailer's end-customer selling price in EUR.",
    )

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("allowed_delivery_windows")
    @classmethod
    def normalise_windows(cls, v: List[str]) -> List[str]:
        """Strip whitespace and upper-case all window codes."""
        normalised = [w.strip().upper() for w in v]
        if not normalised:
            raise ValueError("allowed_delivery_windows must not be empty")
        return normalised

    @field_validator("min_supplier_price")
    @classmethod
    def round_min_supplier_price(cls, v: float) -> float:
        return round(v, 4)

    @field_validator("retail_selling_price")
    @classmethod
    def round_retail_selling_price(cls, v: float) -> float:
        return round(v, 4)

    @model_validator(mode="after")
    def check_feasibility(self) -> "ConstraintModel":
        """
        Validate that a non-empty agreement zone exists.

        Agreement is only possible if:
            min_supplier_price  ≤  max_acceptable_unit_price
        i.e. the supplier's floor is reachable from within the retailer's
        margin constraint.  If not, raise immediately so the researcher
        knows the scenario is infeasible before wasting LLM calls.
        """
        max_price = self.max_acceptable_unit_price()
        if self.min_supplier_price > max_price:
            raise ValueError(
                f"Infeasible constraint configuration: "
                f"min_supplier_price ({self.min_supplier_price}) > "
                f"max_acceptable_unit_price ({max_price:.4f}). "
                f"No agreement zone exists. "
                f"Adjust min_margin, min_supplier_price, or retail_selling_price."
            )
        return self

    # ── Derived / computed methods ────────────────────────────────────────────

    def max_acceptable_unit_price(self) -> float:
        """
        Maximum unit price the retailer can accept while respecting min_margin.

        Formula: retail_selling_price × (1 − min_margin)

        Example
        -------
        retail_selling_price = 60, min_margin = 0.25
        → max acceptable = 60 × 0.75 = 45 EUR
        """
        return round(self.retail_selling_price * (1.0 - self.min_margin), 4)

    def calculate_margin(self, unit_price: float) -> float:
        """
        Compute gross margin at a given unit price.

        Formula: (retail_selling_price − unit_price) / retail_selling_price

        Parameters
        ----------
        unit_price : The purchase price per unit in EUR.

        Returns
        -------
        float : Gross margin as a fraction (e.g. 0.25 = 25 %).
                Returns 0.0 if retail_selling_price is zero (guard).
        """
        if self.retail_selling_price <= 0:
            return 0.0
        return round(
            (self.retail_selling_price - unit_price) / self.retail_selling_price,
            6,
        )

    def is_delivery_window_allowed(self, window: str) -> bool:
        """Return True if the normalised window code is in the allowed list."""
        return window.strip().upper() in self.allowed_delivery_windows

    def agreement_zone_str(self) -> str:
        """Human-readable description of the feasible price range."""
        return (
            f"[{self.min_supplier_price:.2f}, "
            f"{self.max_acceptable_unit_price():.2f}] EUR"
        )

    # ── Serialisation helper ──────────────────────────────────────────────────

    def to_prompt_dict(self) -> dict:
        """
        Return a flat dict suitable for injection into LLM prompts.
        Rounds all floats for readability.
        """
        return {
            "min_margin":               f"{self.min_margin:.0%}",
            "min_supplier_price":       self.min_supplier_price,
            "max_budget":               self.max_budget,
            "allowed_delivery_windows": self.allowed_delivery_windows,
            "retail_selling_price":     self.retail_selling_price,
            "max_acceptable_unit_price": self.max_acceptable_unit_price(),
        }

    def __repr__(self) -> str:
        return (
            f"ConstraintModel("
            f"min_margin={self.min_margin:.0%}, "
            f"min_supplier_price={self.min_supplier_price}, "
            f"max_budget={self.max_budget}, "
            f"retail_selling_price={self.retail_selling_price}, "
            f"agreement_zone={self.agreement_zone_str()}"
            f")"
        )
