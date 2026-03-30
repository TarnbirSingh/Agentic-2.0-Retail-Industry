"""
models/utility.py
─────────────────
Multi-Attribute Utility Model for integrative B2B negotiations.

Theoretical Foundation (Bachelorarbeit Grundlagen):
────────────────────────────────────────────────────
- Multi-Attribute Negotiation (O'Brien 2024, Okunev 2022)
- Trade-offs und Nutzenabwägung (Fujita, Monczka 2009)
- Logrolling: strategische Zugeständnisse bei weniger kritischen Aspekten

This module implements the mathematical foundation for calculating the
utility (value) of multi-dimensional offers, enabling agents to:
1. Evaluate offers across price, volume, delivery, and payment terms
2. Make intelligent trade-offs (e.g., accept higher price for faster delivery)
3. Calculate multi-attribute ZOPA beyond simple price ranges
"""

import logging
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class NegotiationAttribute(str, Enum):
    """Core negotiation attributes."""
    PRICE = "price"
    VOLUME = "volume"
    DELIVERY_DAYS = "delivery_days"
    PAYMENT_TERMS = "payment_terms"


class AttributeWeights(BaseModel):
    """
    Relative importance of each negotiation attribute (must sum to 1.0).
    
    Example Supplier:
        price: 0.50 (most important - revenue)
        volume: 0.30 (important - capacity utilization)
        payment_terms: 0.15 (moderate - cash flow)
        delivery_days: 0.05 (flexible - can adjust logistics)
    
    Example Retailer:
        price: 0.40 (important - margin)
        delivery_days: 0.25 (important - inventory turnover)
        volume: 0.20 (moderate - storage constraints)
        payment_terms: 0.15 (moderate - working capital)
    """
    price: float = Field(0.40, ge=0.0, le=1.0)
    volume: float = Field(0.25, ge=0.0, le=1.0)
    delivery_days: float = Field(0.20, ge=0.0, le=1.0)
    payment_terms: float = Field(0.15, ge=0.0, le=1.0)
    
    def validate_sum(self) -> bool:
        """Ensure weights sum to 1.0 (with small tolerance for floating point)."""
        total = self.price + self.volume + self.delivery_days + self.payment_terms
        return abs(total - 1.0) < 0.01
    
    def normalize(self) -> "AttributeWeights":
        """Normalize weights to sum to exactly 1.0."""
        total = self.price + self.volume + self.delivery_days + self.payment_terms
        if total == 0:
            return AttributeWeights()  # Return default
        return AttributeWeights(
            price=self.price / total,
            volume=self.volume / total,
            delivery_days=self.delivery_days / total,
            payment_terms=self.payment_terms / total,
        )


class NegotiationPreferences(BaseModel):
    """
    Complete preference profile for one negotiation party.
    
    Includes:
    - Attribute weights (relative importance)
    - Hard constraints (limits from PartyLimits)
    - BATNA value (best alternative utility)
    """
    weights: AttributeWeights
    
    # Hard constraints (from PartyLimits)
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_volume: Optional[int] = None
    max_volume: Optional[int] = None
    max_delivery_days: Optional[int] = None
    acceptable_payment_terms: list[str] = Field(default_factory=list)
    
    # BATNA
    batna_utility: float = Field(
        0.0,
        description="Utility of best alternative (0-1 scale). Agent should reject offers below this."
    )
    
    # Target values (aspirations)
    target_price: Optional[float] = None
    target_volume: Optional[int] = None
    target_delivery_days: Optional[int] = None
    target_payment_terms: Optional[str] = None


class UtilityResult(BaseModel):
    """Result of utility calculation for an offer."""
    total_utility: float = Field(..., description="Overall utility score (0-1)")
    
    # Breakdown by attribute
    price_utility: float
    volume_utility: float
    delivery_utility: float
    payment_utility: float
    
    # Metadata
    is_acceptable: bool = Field(..., description="Above BATNA threshold")
    exceeds_constraints: bool = Field(..., description="Violates hard limits")
    explanation: str = Field(default="", description="Human-readable breakdown")


def normalize_value(
    value: float,
    min_val: float,
    max_val: float,
    reverse: bool = False,
) -> float:
    """
    Normalize a value to 0-1 scale.
    
    Args:
        value: The value to normalize
        min_val: Minimum value (maps to 0 or 1 depending on reverse)
        max_val: Maximum value (maps to 1 or 0 depending on reverse)
        reverse: If True, higher values = lower utility (e.g., for price, delivery time)
    
    Returns:
        Normalized value between 0 and 1
    """
    if max_val == min_val:
        return 0.5  # Neutral if no range
    
    normalized = (value - min_val) / (max_val - min_val)
    normalized = max(0.0, min(1.0, normalized))  # Clamp to [0, 1]
    
    if reverse:
        normalized = 1.0 - normalized
    
    return normalized


def calculate_price_utility(
    price: float,
    preferences: NegotiationPreferences,
    is_supplier: bool,
) -> float:
    """
    Calculate utility for price attribute.
    
    Supplier: Higher price = higher utility (revenue)
    Retailer: Lower price = higher utility (cost)
    """
    if is_supplier:
        # Supplier: min_price (worst) → max_price or target (best)
        min_val = preferences.min_price or 0
        max_val = preferences.target_price or preferences.max_price or (min_val * 1.5)
        return normalize_value(price, min_val, max_val, reverse=False)
    else:
        # Retailer: max_price (worst) → min_price or target (best)
        max_val = preferences.max_price or 100
        min_val = preferences.target_price or preferences.min_price or (max_val * 0.5)
        return normalize_value(price, min_val, max_val, reverse=True)


def calculate_volume_utility(
    volume: int,
    preferences: NegotiationPreferences,
) -> float:
    """
    Calculate utility for volume attribute.
    
    Generally: closer to target = higher utility.
    Between min and max = acceptable range.
    """
    target = preferences.target_volume
    min_vol = preferences.min_volume or 0
    max_vol = preferences.max_volume or (min_vol * 10)
    
    if target:
        # Peak utility at target, decreases towards boundaries
        if volume == target:
            return 1.0
        elif volume < target:
            return normalize_value(volume, min_vol, target, reverse=False)
        else:
            return normalize_value(volume, target, max_vol, reverse=True)
    else:
        # No target: higher volume = better (economies of scale)
        return normalize_value(volume, min_vol, max_vol, reverse=False)


def calculate_delivery_utility(
    delivery_days: int,
    preferences: NegotiationPreferences,
) -> float:
    """
    Calculate utility for delivery time.
    
    Generally: shorter delivery = higher utility (faster to market).
    """
    max_days = preferences.max_delivery_days or 30
    target = preferences.target_delivery_days or 0
    
    # Shorter is better (reverse normalization)
    return normalize_value(delivery_days, target, max_days, reverse=True)


def calculate_payment_terms_utility(
    payment_terms: str,
    preferences: NegotiationPreferences,
) -> float:
    """
    Calculate utility for payment terms.
    
    Simple mapping: acceptable terms get 1.0, others get 0.0.
    Could be refined with more granular scoring (Net 30 vs Net 60, etc.)
    """
    if not preferences.acceptable_payment_terms:
        return 1.0  # No preference = all terms acceptable
    
    if payment_terms in preferences.acceptable_payment_terms:
        # Could refine: check if it matches target exactly
        if preferences.target_payment_terms == payment_terms:
            return 1.0
        else:
            return 0.8  # Acceptable but not ideal
    else:
        return 0.0  # Unacceptable


def calculate_utility(
    unit_price: float,
    volume: int,
    delivery_days: int,
    payment_terms: str,
    preferences: NegotiationPreferences,
    is_supplier: bool,
) -> UtilityResult:
    """
    Calculate overall utility of an offer for one party.
    
    Uses weighted sum of normalized attribute utilities:
    U(offer) = Σ weight_i × utility_i
    
    Args:
        unit_price: Price per unit
        volume: Order quantity
        delivery_days: Lead time
        payment_terms: Payment terms string (e.g., "Net 30")
        preferences: Party's preference profile
        is_supplier: True if calculating for supplier, False for retailer
    
    Returns:
        UtilityResult with total utility and breakdown
    """
    # Normalize weights (safety check)
    weights = preferences.weights.normalize()
    
    # Calculate individual utilities
    price_util = calculate_price_utility(unit_price, preferences, is_supplier)
    volume_util = calculate_volume_utility(volume, preferences)
    delivery_util = calculate_delivery_utility(delivery_days, preferences)
    payment_util = calculate_payment_terms_utility(payment_terms, preferences)
    
    # Weighted sum
    total = (
        weights.price * price_util +
        weights.volume * volume_util +
        weights.delivery_days * delivery_util +
        weights.payment_terms * payment_util
    )
    
    # Check constraints
    exceeds_constraints = False
    if is_supplier:
        if preferences.min_price and unit_price < preferences.min_price:
            exceeds_constraints = True
        if preferences.min_volume and volume < preferences.min_volume:
            exceeds_constraints = True
        if preferences.max_volume and volume > preferences.max_volume:
            exceeds_constraints = True
    else:
        if preferences.max_price and unit_price > preferences.max_price:
            exceeds_constraints = True
        if preferences.max_delivery_days and delivery_days > preferences.max_delivery_days:
            exceeds_constraints = True
    
    # Compare to BATNA
    is_acceptable = total >= preferences.batna_utility and not exceeds_constraints
    
    # Build explanation
    explanation = (
        f"Utility: {total:.3f} = "
        f"Price({price_util:.2f}×{weights.price:.2f}) + "
        f"Volume({volume_util:.2f}×{weights.volume:.2f}) + "
        f"Delivery({delivery_util:.2f}×{weights.delivery_days:.2f}) + "
        f"Payment({payment_util:.2f}×{weights.payment_terms:.2f})"
    )
    
    if preferences.batna_utility > 0:
        explanation += f" | BATNA: {preferences.batna_utility:.3f}"
    
    return UtilityResult(
        total_utility=round(total, 4),
        price_utility=round(price_util, 3),
        volume_utility=round(volume_util, 3),
        delivery_utility=round(delivery_util, 3),
        payment_utility=round(payment_util, 3),
        is_acceptable=is_acceptable,
        exceeds_constraints=exceeds_constraints,
        explanation=explanation,
    )


class MultiAttributeZOPA(BaseModel):
    """
    Multi-dimensional Zone of Possible Agreement.
    
    Extends simple price-based ZOPA to consider all attributes.
    A deal is possible if there exists an offer that both parties
    find acceptable (utility >= BATNA).
    """
    zopa_exists: bool
    
    # Traditional price ZOPA
    price_zopa_min: Optional[float] = None
    price_zopa_max: Optional[float] = None
    
    # Volume overlap
    volume_overlap_min: Optional[int] = None
    volume_overlap_max: Optional[int] = None
    
    # Delivery compatibility
    delivery_compatible: bool = False
    
    # Payment terms overlap
    payment_terms_overlap: list[str] = Field(default_factory=list)
    
    # Utility-based ZOPA
    supplier_min_utility: float = 0.0
    retailer_min_utility: float = 0.0
    
    # Explanation
    recommendation: str = ""
    blocking_factors: list[str] = Field(default_factory=list)


def calculate_multi_attribute_zopa(
    supplier_prefs: NegotiationPreferences,
    retailer_prefs: NegotiationPreferences,
) -> MultiAttributeZOPA:
    """
    Calculate multi-dimensional ZOPA considering all attributes.
    
    A multi-attribute ZOPA exists if:
    1. Price ranges overlap (supplier min ≤ retailer max)
    2. Volume ranges overlap
    3. Delivery times compatible
    4. Payment terms have overlap
    
    Returns comprehensive analysis of negotiation space.
    """
    blocking_factors = []
    
    # 1. Price ZOPA
    supplier_min = supplier_prefs.min_price or 0
    retailer_max = retailer_prefs.max_price or float('inf')
    
    price_zopa_exists = supplier_min <= retailer_max
    if not price_zopa_exists:
        gap = supplier_min - retailer_max
        blocking_factors.append(
            f"Price gap: supplier min ({supplier_min:.2f}) exceeds "
            f"retailer max ({retailer_max:.2f}) by {gap:.2f} EUR"
        )
    
    # 2. Volume overlap
    supplier_min_vol = supplier_prefs.min_volume or 0
    supplier_max_vol = supplier_prefs.max_volume or float('inf')
    retailer_min_vol = retailer_prefs.min_volume or 0
    retailer_max_vol = retailer_prefs.max_volume or float('inf')
    
    volume_overlap_min = max(supplier_min_vol, retailer_min_vol)
    volume_overlap_max = min(supplier_max_vol, retailer_max_vol)
    volume_compatible = volume_overlap_min <= volume_overlap_max
    
    if not volume_compatible:
        blocking_factors.append(
            f"Volume incompatible: supplier [{supplier_min_vol}-{supplier_max_vol}] "
            f"vs retailer [{retailer_min_vol}-{retailer_max_vol}]"
        )
    
    # 3. Delivery compatibility
    supplier_max_delivery = 30  # Assume reasonable default
    retailer_max_delivery = retailer_prefs.max_delivery_days or 30
    delivery_compatible = supplier_max_delivery <= retailer_max_delivery
    
    if not delivery_compatible:
        blocking_factors.append(
            f"Delivery incompatible: retailer requires ≤ {retailer_max_delivery} days"
        )
    
    # 4. Payment terms overlap
    supplier_terms = set(supplier_prefs.acceptable_payment_terms or ["Net 30"])
    retailer_terms = set(retailer_prefs.acceptable_payment_terms or ["Net 30"])
    payment_overlap = list(supplier_terms & retailer_terms)
    
    payment_compatible = len(payment_overlap) > 0
    if not payment_compatible:
        blocking_factors.append(
            f"Payment terms incompatible: supplier {supplier_terms} vs retailer {retailer_terms}"
        )
    
    # Overall ZOPA
    zopa_exists = (
        price_zopa_exists and
        volume_compatible and
        delivery_compatible and
        payment_compatible
    )
    
    # Generate recommendation
    if zopa_exists:
        recommendation = (
            f"Multi-attribute ZOPA exists! "
            f"Price: {supplier_min:.2f}-{retailer_max:.2f} EUR, "
            f"Volume: {volume_overlap_min}-{volume_overlap_max} units, "
            f"Payment: {', '.join(payment_overlap)}"
        )
    else:
        recommendation = f"No ZOPA. Blocking factors: {'; '.join(blocking_factors)}"
    
    return MultiAttributeZOPA(
        zopa_exists=zopa_exists,
        price_zopa_min=supplier_min if price_zopa_exists else None,
        price_zopa_max=retailer_max if price_zopa_exists else None,
        volume_overlap_min=volume_overlap_min if volume_compatible else None,
        volume_overlap_max=volume_overlap_max if volume_compatible else None,
        delivery_compatible=delivery_compatible,
        payment_terms_overlap=payment_overlap,
        supplier_min_utility=supplier_prefs.batna_utility,
        retailer_min_utility=retailer_prefs.batna_utility,
        recommendation=recommendation,
        blocking_factors=blocking_factors,
    )