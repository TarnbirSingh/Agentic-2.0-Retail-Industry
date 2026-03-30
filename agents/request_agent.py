"""
agents/request_agent.py
──────────────────────
Agent für Request-Handling und Product-Matching.

Retailer Agent: Strukturiert Freitext-Anfragen
Supplier Agent: Matcht passende Produkte semantisch
"""

import json
import logging
from typing import Optional

from langchain_core.messages import HumanMessage

from llm.ai_core_client import AICoreClient
from models.negotiation_models import ProductMatch, ProductRequest

logger = logging.getLogger(__name__)


class RequestAgent:
    """Agent für Request-Processing und Product-Matching."""
    
    def __init__(self, llm_client: AICoreClient):
        self.llm_client = llm_client
    
    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from LLM response, stripping markdown code fences.
        
        Handles responses like:
        ```json
        {"key": "value"}
        ```
        
        Or plain JSON:
        {"key": "value"}
        """
        text = text.strip()
        
        # Remove markdown code fences
        if text.startswith("```"):
            # Remove first line (```json or ```)
            lines = text.split("\n", 1)
            if len(lines) > 1:
                text = lines[1]
        
        if text.endswith("```"):
            # Remove last line with closing fence
            text = text.rsplit("```", 1)[0]
        
        return text.strip()
    
    def process_retailer_request(
        self,
        raw_request: str,
        retailer_id: str,
        retailer_name: str,
    ) -> ProductRequest:
        """
        Retailer gibt Freitext ein → Agent strukturiert die Anfrage.
        
        Beispiel Input: "Ich brauche Bohrmaschinen, ca. 800 Stück für Q2"
        Output: Strukturierte ProductRequest mit Kategorie, Menge, Timeframe
        """
        prompt = f"""You are a B2B procurement assistant helping a retailer create a structured product request.

The retailer ({retailer_name}) has provided this free-text request:
"{raw_request}"

Your task:
1. Extract ALL key information from the request
2. Structure it into clear fields
3. Add light market context if applicable

Output ONLY valid JSON in this exact format:
{{
    "product_category": "category name (e.g., 'Power Tools', 'Drills')",
    "product_description": "concise product description capturing key details (1-2 sentences) or null",
    "estimated_volume": number or null,
    "timeframe": "timeframe string (e.g., 'Q2 2024', 'ASAP') or null",
    "budget_range": "price range if mentioned (e.g., '30-50€ per unit', '€25,000 total') or null",
    "quality_tier": "quality/segment if mentioned (e.g., 'Mittelklasse', 'Premium', 'Economy', 'Standard') or null",
    "preferred_payment_terms": "payment terms if mentioned (e.g., 'Net30', 'Net60', 'Prepayment') or null",
    "special_requirements": "any other special requirements not covered above or null",
    "market_context": "brief typical price range or market info (1 sentence) or null"
}}

Be concise and factual. Extract ALL details the retailer mentioned. If information is missing, use null."""

        try:
            llm = self.llm_client.get_llm()
            response = llm.invoke([HumanMessage(content=prompt)])
            response_text = response.content
            
            # Extract and parse JSON response (strip markdown fences)
            clean_json = self._extract_json(response_text)
            structured = json.loads(clean_json)
            
            # Create ProductRequest
            import uuid
            request = ProductRequest(
                request_id=str(uuid.uuid4()),
                retailer_id=retailer_id,
                retailer_name=retailer_name,
                raw_request=raw_request,
                product_category=structured.get("product_category"),
                product_description=structured.get("product_description"),
                estimated_volume=structured.get("estimated_volume"),
                timeframe=structured.get("timeframe"),
                budget_range=structured.get("budget_range"),
                quality_tier=structured.get("quality_tier"),
                preferred_payment_terms=structured.get("preferred_payment_terms"),
                special_requirements=structured.get("special_requirements"),
                market_context=structured.get("market_context"),
            )
            
            logger.info(
                f"Structured request: category={request.product_category}, "
                f"volume={request.estimated_volume}"
            )
            
            return request
            
        except Exception as e:
            logger.error(f"Failed to structure request: {e}", exc_info=True)
            # Fallback: create basic request
            import uuid
            return ProductRequest(
                request_id=str(uuid.uuid4()),
                retailer_id=retailer_id,
                retailer_name=retailer_name,
                raw_request=raw_request,
                product_category="Unknown",
            )
    
    def match_products(
        self,
        request: ProductRequest,
        available_products: list[dict],
    ) -> list[ProductMatch]:
        """
        Supplier Agent matcht semantisch passende Produkte.
        
        LLM-gestützt: Versteht "Bohrmaschine" und matched gegen Produktkatalog.
        """
        prompt = f"""You are a B2B product matching agent for a supplier.

A retailer ({request.retailer_name}) has requested:
"{request.raw_request}"

Structured analysis:
- Category: {request.product_category}
- Estimated Volume: {request.estimated_volume or 'not specified'}
- Timeframe: {request.timeframe or 'not specified'}
- Special Requirements: {request.special_requirements or 'none'}

Available products in your catalog:
{json.dumps(available_products, indent=2)}

Your task:
1. Match products semantically (understand intent, not just keywords)
2. Score each match by relevance (0.0-1.0)
3. Provide clear reasoning for each match

Output ONLY valid JSON array in this exact format:
[
    {{
        "product_id": "product ID from catalog",
        "relevance_score": 0.95,
        "reasoning": "Why this product matches the request (1-2 sentences)"
    }}
]

Only include products with relevance_score >= 0.5.
Sort by relevance_score descending.
Maximum 5 matches."""

        try:
            llm = self.llm_client.get_llm()
            response = llm.invoke([HumanMessage(content=prompt)])
            response_text = response.content
            
            # Extract and parse JSON response (strip markdown fences)
            clean_json = self._extract_json(response_text)
            matches_raw = json.loads(clean_json)
            
            # Create ProductMatch objects
            matches = []
            for match_data in matches_raw:
                # Find product details from catalog
                product = next(
                    (p for p in available_products if p["product_id"] == match_data["product_id"]),
                    None
                )
                
                if product:
                    matches.append(ProductMatch(
                        product_id=product["product_id"],
                        product_name=product["name"],
                        relevance_score=match_data["relevance_score"],
                        reasoning=match_data["reasoning"],
                        base_price=product["base_price_eur"],
                        min_price=product["min_price_eur"],
                        typical_retail_price=product["typical_retail_price_eur"],
                        min_order_quantity=product["min_order_quantity"],
                        max_monthly_capacity=product["max_monthly_capacity"],
                        lead_time_days=product["lead_time_days"],
                        default_payment_terms=product["default_payment_terms"],
                    ))
            
            logger.info(f"Matched {len(matches)} products for request {request.request_id}")
            return matches
            
        except Exception as e:
            logger.error(f"Failed to match products: {e}", exc_info=True)
            return []
    
    def create_initial_offer(
        self,
        product: dict,
        supplier_constraints: dict,
        request_context: Optional[ProductRequest] = None,
    ) -> dict:
        """
        Supplier Agent erstellt intelligentes Einstiegsangebot.
        
        Basiert auf:
        - Produktdaten (Preis, Kapazität)
        - Supplier Constraints (min_price, etc.)
        - Request Context (gewünschte Menge, Zeitrahmen)
        """
        # Base offer from product data
        base_offer = {
            "unit_price": product["base_price_eur"],
            "volume": request_context.estimated_volume if request_context and request_context.estimated_volume else product["min_order_quantity"],
            "delivery_days": product["lead_time_days"],
            "payment_terms": product["default_payment_terms"],
        }
        
        # Adjust based on constraints
        if supplier_constraints.get("min_price"):
            base_offer["unit_price"] = max(
                base_offer["unit_price"],
                supplier_constraints["min_price"]
            )
        
        # Volume validation
        base_offer["volume"] = min(
            max(base_offer["volume"], product["min_order_quantity"]),
            product["max_monthly_capacity"]
        )
        
        prompt = f"""You are a B2B supplier agent creating an initial offer.

Product: {product['name']}
Base Price: €{base_offer['unit_price']}
Requested Volume: {base_offer['volume']} units

Context from retailer request:
{request_context.raw_request if request_context else 'No specific context'}

Your task: Create a brief, professional justification for this offer (2-3 sentences).
Mention key value propositions (quality, reliability, delivery, etc.).

Output ONLY the justification text, no JSON."""

        try:
            llm = self.llm_client.get_llm()
            response = llm.invoke([HumanMessage(content=prompt)])
            justification = response.content
            base_offer["justification"] = justification.strip()
        except Exception as e:
            logger.warning(f"Failed to generate justification: {e}")
            base_offer["justification"] = f"Quality product with {product['lead_time_days']} days delivery."
        
        return base_offer