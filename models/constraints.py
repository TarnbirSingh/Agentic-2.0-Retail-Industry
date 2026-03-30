"""
models/constraints.py
────────────────────
Simplified constraint validation for B2B negotiations.

Only essential business rules:
- Price floors/ceilings
- Volume min/max
- Delivery time limits
- Payment terms acceptance
- Margin validation (if retail price known)

EXTENDED: Multi-attribute utility integration
"""

from typing import Optional
from pydantic import BaseModel

# Import utility models
try:
    from models.utility import (
        AttributeWeights,
        NegotiationPreferences,
        calculate_multi_attribute_zopa,
        MultiAttributeZOPA,
    )
except ImportError:
    # Graceful fallback
    AttributeWeights = None
    NegotiationPreferences = None
    calculate_multi_attribute_zopa = None
    MultiAttributeZOPA = None


class ConstraintViolation(BaseModel):
    """Single constraint violation."""
    field: str
    violation_type: str
    message: str
    current_value: float | int | str
    limit_value: Optional[float | int | str] = None


class ValidationResult(BaseModel):
    """Result of constraint validation."""
    is_valid: bool
    violations: list[ConstraintViolation] = []
    
    @property
    def message(self) -> str:
        """Human-readable validation message."""
        if self.is_valid:
            return "All constraints satisfied"
        return "; ".join(v.message for v in self.violations)


def validate_offer_against_supplier_limits(
    unit_price: float,
    volume: int,
    delivery_days: int,
    payment_terms: str,
    supplier_min_price: Optional[float] = None,
    supplier_min_volume: Optional[int] = None,
    supplier_max_volume: Optional[int] = None,
    supplier_acceptable_payment_terms: list[str] = None,
) -> ValidationResult:
    """Validate offer against supplier constraints."""
    violations = []
    
    if supplier_min_price and unit_price < supplier_min_price:
        violations.append(ConstraintViolation(
            field="unit_price",
            violation_type="below_floor",
            message=f"Price {unit_price:.2f} EUR is below supplier floor {supplier_min_price:.2f} EUR",
            current_value=unit_price,
            limit_value=supplier_min_price,
        ))
    
    if supplier_min_volume and volume < supplier_min_volume:
        violations.append(ConstraintViolation(
            field="volume",
            violation_type="below_min",
            message=f"Volume {volume} is below supplier minimum {supplier_min_volume}",
            current_value=volume,
            limit_value=supplier_min_volume,
        ))
    
    if supplier_max_volume and volume > supplier_max_volume:
        violations.append(ConstraintViolation(
            field="volume",
            violation_type="above_max",
            message=f"Volume {volume} exceeds supplier capacity {supplier_max_volume}",
            current_value=volume,
            limit_value=supplier_max_volume,
        ))
    
    if supplier_acceptable_payment_terms and payment_terms not in supplier_acceptable_payment_terms:
        violations.append(ConstraintViolation(
            field="payment_terms",
            violation_type="not_acceptable",
            message=f"Payment terms '{payment_terms}' not acceptable to supplier",
            current_value=payment_terms,
            limit_value=", ".join(supplier_acceptable_payment_terms),
        ))
    
    return ValidationResult(is_valid=len(violations) == 0, violations=violations)


def validate_offer_against_retailer_limits(
    unit_price: float,
    volume: int,
    delivery_days: int,
    payment_terms: str,
    retailer_max_price: Optional[float] = None,
    retailer_min_volume: Optional[int] = None,
    retailer_max_volume: Optional[int] = None,
    retailer_max_delivery_days: Optional[int] = None,
    retailer_acceptable_payment_terms: list[str] = None,
    retailer_target_margin: Optional[float] = None,
    retailer_retail_price: Optional[float] = None,
) -> ValidationResult:
    """Validate offer against retailer constraints."""
    violations = []
    
    if retailer_max_price and unit_price > retailer_max_price:
        violations.append(ConstraintViolation(
            field="unit_price",
            violation_type="above_ceiling",
            message=f"Price {unit_price:.2f} EUR exceeds retailer ceiling {retailer_max_price:.2f} EUR",
            current_value=unit_price,
            limit_value=retailer_max_price,
        ))
    
    if retailer_min_volume and volume < retailer_min_volume:
        violations.append(ConstraintViolation(
            field="volume",
            violation_type="below_min",
            message=f"Volume {volume} is below retailer minimum {retailer_min_volume}",
            current_value=volume,
            limit_value=retailer_min_volume,
        ))
    
    if retailer_max_volume and volume > retailer_max_volume:
        violations.append(ConstraintViolation(
            field="volume",
            violation_type="above_max",
            message=f"Volume {volume} exceeds retailer maximum {retailer_max_volume}",
            current_value=volume,
            limit_value=retailer_max_volume,
        ))
    
    if retailer_max_delivery_days and delivery_days > retailer_max_delivery_days:
        violations.append(ConstraintViolation(
            field="delivery_days",
            violation_type="too_long",
            message=f"Delivery time {delivery_days} days exceeds retailer limit {retailer_max_delivery_days} days",
            current_value=delivery_days,
            limit_value=retailer_max_delivery_days,
        ))
    
    if retailer_acceptable_payment_terms and payment_terms not in retailer_acceptable_payment_terms:
        violations.append(ConstraintViolation(
            field="payment_terms",
            violation_type="not_acceptable",
            message=f"Payment terms '{payment_terms}' not acceptable to retailer",
            current_value=payment_terms,
            limit_value=", ".join(retailer_acceptable_payment_terms),
        ))
    
    # Check margin if retail price is known
    if retailer_target_margin and retailer_retail_price:
        actual_margin = (retailer_retail_price - unit_price) / retailer_retail_price
        if actual_margin < retailer_target_margin:
            violations.append(ConstraintViolation(
                field="margin",
                violation_type="below_target",
                message=f"Margin {actual_margin:.1%} is below target {retailer_target_margin:.1%}",
                current_value=actual_margin,
                limit_value=retailer_target_margin,
            ))
    
    return ValidationResult(is_valid=len(violations) == 0, violations=violations)


def calculate_zopa(
    supplier_min_price: Optional[float],
    retailer_max_price: Optional[float],
) -> tuple[Optional[float], Optional[float], bool]:
    """
    Calculate Zone of Possible Agreement (simple price-based).
    
    Returns:
        (zopa_min, zopa_max, zopa_exists)
    """
    if supplier_min_price is None or retailer_max_price is None:
        return None, None, False
    
    zopa_exists = supplier_min_price <= retailer_max_price
    return supplier_min_price, retailer_max_price, zopa_exists


def convert_limits_to_preferences(
    limits: "PartyLimits",
    is_supplier: bool,
    attribute_weights: Optional[AttributeWeights] = None,
) -> Optional["NegotiationPreferences"]:
    """
    Convert PartyLimits to NegotiationPreferences for utility-based negotiation.
    
    This is a helper to enable multi-attribute negotiation with the existing
    PartyLimits model. In production, preferences should be set explicitly.
    
    Args:
        limits: Party's constraints
        is_supplier: True for supplier, False for retailer
        attribute_weights: Custom weights, or use role-based defaults
    
    Returns:
        NegotiationPreferences object or None if utility module not available
    """
    if NegotiationPreferences is None or AttributeWeights is None:
        return None
    
    # Default weights based on role
    if attribute_weights is None:
        if is_supplier:
            # Supplier prioritizes: price (50%) > volume (30%) > payment (15%) > delivery (5%)
            attribute_weights = AttributeWeights(
                price=0.50,
                volume=0.30,
                payment_terms=0.15,
                delivery_days=0.05,
            )
        else:
            # Retailer prioritizes: price (40%) > delivery (25%) > volume (20%) > payment (15%)
            attribute_weights = AttributeWeights(
                price=0.40,
                delivery_days=0.25,
                volume=0.20,
                payment_terms=0.15,
            )
    
    # Build preferences from limits
    prefs = NegotiationPreferences(
        weights=attribute_weights,
        min_price=limits.min_price,
        max_price=limits.max_price,
        min_volume=limits.min_volume,
        max_volume=limits.max_volume,
        max_delivery_days=limits.max_delivery_days,
        acceptable_payment_terms=limits.acceptable_payment_terms or [],
        batna_utility=0.0,  # Default: no BATNA (accept any deal within limits)
    )
    
    # Set aspirational targets
    if is_supplier and limits.min_price:
        # Supplier aspires to 20% above minimum
        prefs.target_price = limits.min_price * 1.20
    elif not is_supplier and limits.max_price:
        # Retailer aspires to 15% below maximum
        prefs.target_price = limits.max_price * 0.85
    
    if limits.min_volume and limits.max_volume:
        # Target middle of volume range
        prefs.target_volume = (limits.min_volume + limits.max_volume) // 2
    
    if limits.max_delivery_days:
        # Target 60% of max delivery time
        prefs.target_delivery_days = int(limits.max_delivery_days * 0.6)
    
    if limits.acceptable_payment_terms:
        # Prefer first acceptable term
        prefs.target_payment_terms = limits.acceptable_payment_terms[0]
    
    return prefs
