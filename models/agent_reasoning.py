"""
models/agent_reasoning.py
─────────────────────────
Agent Transparency & Reasoning Models

Captures what an agent "thinks" during negotiation for frontend transparency.
Enables Agentic 2.0 explainability.
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class ReasoningStep(BaseModel):
    """Single step in agent's reasoning process."""
    
    phase: str = Field(
        ...,
        description="Phase name: THINK, STRATEGIZE, CALCULATE, GENERATE, VALIDATE"
    )
    observation: str = Field(
        ...,
        description="What the agent observed/analyzed in this step"
    )
    reasoning: str = Field(
        ...,
        description="Agent's internal reasoning/logic"
    )
    conclusion: Optional[str] = Field(
        None,
        description="What the agent concluded from this step"
    )


class AgentReasoning(BaseModel):
    """
    Complete reasoning data from an agent for one negotiation round.
    
    This enables transparency: frontend can show what the agent thought,
    which strategy it chose, why it made certain concessions, etc.
    """
    
    # Strategy & Tactic
    strategy_used: str = Field(
        ...,
        description="Strategy type: BOULWARE, LINEAR, CONCEDER"
    )
    tactic: str = Field(
        ...,
        description="Tactic used: concession, leverage, hold_firm, propose_alternative"
    )
    
    # Utility & Scoring
    own_utility: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Utility score of own offer (0.0-1.0)"
    )
    estimated_counterparty_utility: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Estimated utility of counterparty (if calculable)"
    )
    
    # Concession Analysis
    concession_amount_eur: Optional[float] = Field(
        None,
        description="How much the agent conceded in EUR vs. previous round"
    )
    concession_percentage: Optional[float] = Field(
        None,
        description="Concession as % of gap"
    )
    
    # Convergence
    convergence_progress: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Progress toward deal (0-100%)"
    )
    gap_remaining_eur: Optional[float] = Field(
        None,
        description="Price gap remaining between parties"
    )
    
    # Leverage & Context
    leverage_used: Optional[str] = Field(
        None,
        description="Type of leverage: volume_leverage, timing_pressure, alternative_terms, quality_premium, etc."
    )
    context_factors: List[str] = Field(
        default_factory=list,
        description="Contextual factors considered: market_pressure, volume_incentive, time_constraint, etc."
    )
    
    # Detailed Reasoning Steps
    reasoning_steps: List[ReasoningStep] = Field(
        default_factory=list,
        description="Step-by-step reasoning process (THINK → STRATEGIZE → CALCULATE → GENERATE → VALIDATE)"
    )
    
    # Summary
    summary: str = Field(
        ...,
        description="One-sentence summary of agent's reasoning for this round"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "strategy_used": "LINEAR",
                "tactic": "concession",
                "own_utility": 0.72,
                "estimated_counterparty_utility": 0.68,
                "concession_amount_eur": -1.20,
                "concession_percentage": 37.5,
                "convergence_progress": 68.0,
                "gap_remaining_eur": 2.00,
                "leverage_used": "volume_leverage",
                "context_factors": ["volume_incentive", "market_pressure"],
                "reasoning_steps": [
                    {
                        "phase": "THINK",
                        "observation": "Counterparty offered €46.00, gap is €3.20",
                        "reasoning": "Need to close gap but maintain minimum margin",
                        "conclusion": "Can concede 30-40% of gap"
                    },
                    {
                        "phase": "STRATEGIZE",
                        "observation": "Round 3 of 10, early stage",
                        "reasoning": "LINEAR strategy suggests steady concessions",
                        "conclusion": "Use moderate concession with volume leverage"
                    }
                ],
                "summary": "Reduced price by €1.20 using LINEAR strategy, leveraging higher volume to justify concession"
            }
        }