"""
Quick end-to-end test for NegotiationAgent (no LLM required).
Run: python3 test_agent_e2e.py
"""
import json
import sys
from unittest.mock import MagicMock

# Ensure project root is on sys.path when running from tests/
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from models.negotiation_models import AgentRole, PartyLimits, NegotiationOffer, NegotiationRound
from agents.simple_agent import NegotiationAgent

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✓  {label}")
        PASS += 1
    else:
        print(f"  ✗  {label}  {detail}")
        FAIL += 1

# ── Mock LLM ──────────────────────────────────────────────────────────────────
call_count = 0

def mock_generate(messages, temperature=0.5, **kw):
    global call_count
    call_count += 1
    content = messages[0]["content"]
    if "OUTPUT ONLY valid JSON" in content:
        return json.dumps({
            "tactic": "concede",
            "tactic_reason": "Standard concession",
            "use_leverage": "volume",
            "propose_tradeoff": False,
            "recommended_action": "concede",
        })
    return "We propose this pricing based on current market conditions."

llm = MagicMock()
llm.generate.side_effect = mock_generate

# ── Create agent ──────────────────────────────────────────────────────────────
supplier_limits = PartyLimits(
    min_price=42.0,
    max_price=60.0,
    min_volume=500,
    max_volume=2000,
    acceptable_payment_terms=["Net 30", "Net 60"],
)
agent = NegotiationAgent(
    role=AgentRole.SUPPLIER,
    llm_client=llm,
    limits=supplier_limits,
    product_name="Industrial Drill Bits",
    personality_seed=99,
)
check("Agent created", agent is not None)
check("Strategy assigned", agent.strategy is not None)
check("Personality assigned", agent._personality is not None)

# ── Round 1 (no history) ──────────────────────────────────────────────────────
offer1, r1 = agent.generate_counteroffer(1, [], None, max_rounds=10)
check("Round 1 returns offer", offer1 is not None)
check("Round 1 price > min", offer1.unit_price > 42.0, f"got {offer1.unit_price}")
check("Round 1 has tactic", bool(r1.tactic), f"got '{r1.tactic}'")
check("Round 1 has strategy", bool(r1.strategy_used))

# ── Round 2 (retailer counter) ────────────────────────────────────────────────
retailer_offer = NegotiationOffer(unit_price=44.0, volume=1000, delivery_days=14, payment_terms="Net 30")
rnd = NegotiationRound(round_number=1, role=AgentRole.RETAILER, offer=retailer_offer, is_valid=True)
offer2, r2 = agent.generate_counteroffer(2, [rnd], retailer_offer, max_rounds=10)
check("Round 2 returns offer", offer2 is not None)
check("Round 2 has strategy", bool(r2.strategy_used))

# ── Acceptance logic ──────────────────────────────────────────────────────────
# EUR43 = within 3% of min_price 42 — should accept (only own limits, no ZOPA)
ok1, reason1 = agent.should_accept_offer(
    NegotiationOffer(unit_price=43.0, volume=1000, delivery_days=14, payment_terms="Net 30"),
    current_round=8, max_rounds=10, history=[],
)
check("Accept EUR43 (within margin)", ok1, reason1[:60])

# EUR40 = below own min_price — must reject
ok2, reason2 = agent.should_accept_offer(
    NegotiationOffer(unit_price=40.0, volume=1000, delivery_days=14, payment_terms="Net 30"),
    current_round=8, max_rounds=10, history=[],
)
check("Reject EUR40 (below own floor)", not ok2, reason2[:60])

# EUR39 = well below min — must reject regardless of round
ok3, reason3 = agent.should_accept_offer(
    NegotiationOffer(unit_price=39.0, volume=1000, delivery_days=14, payment_terms="Net 30"),
    current_round=5, max_rounds=10, history=[],
)
check("Reject EUR39 (well below own floor)", not ok3, reason3[:60])

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\nLLM calls: {call_count}")
print(f"\n{'='*40}")
if FAIL == 0:
    print(f"ALL {PASS} TESTS PASSED")
else:
    print(f"{PASS} passed, {FAIL} FAILED")
    raise SystemExit(1)