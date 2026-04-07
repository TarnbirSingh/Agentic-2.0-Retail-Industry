"""
test_e2e_scenarios.py
─────────────────────
Comprehensive End-to-End scenario tests for the Agentic 2.0 negotiation system.

Uses the REAL AICoreClient (SAP AI Core / GPT-4o) — no mocking.

Tests the full negotiation pipeline end-to-end:
  .env credentials → AICoreClient → NegotiationAgent → SimpleOrchestrator
  → LLM-driven tactic selection → convergence / acceptance / edge-case outcomes

Coverage:
  NORMAL scenarios  (1-8):  standard negotiations with real catalog products
  COMPLEMENT scenarios (9-11): accessory / complementary-good negotiations
  EDGE CASE scenarios (12-16): no ZOPA, max rounds, point ZOPA, HITL, approval flow

Run:  python3 test_e2e_scenarios.py
"""

import os
import sys
import uuid
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional

# ── Load .env before anything else ───────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass  # python-dotenv optional — env vars must be set manually

# Configure logging: INFO for scenarios, suppress noisy lower-level libs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("gen_ai_hub").setLevel(logging.WARNING)

from llm.ai_core_client import AICoreClient
from models.negotiation_models import (
    AgentRole,
    NegotiationOffer,
    NegotiationSession,
    PartyLimits,
    SessionStatus,
)
from orchestration.simple_orchestrator import SimpleOrchestrator

logger = logging.getLogger("e2e_test")


# ══════════════════════════════════════════════════════════════════════════════
# RESULT TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ScenarioResult:
    number: int
    name: str
    category: str
    product: str
    supplier: str
    retailer: str
    expected_status: str
    passed: bool = False
    actual_status: str = ""
    rounds_used: int = 0
    final_price: Optional[float] = None
    zopa_range: str = ""
    acceptance_type: str = ""
    hitl_triggered: bool = False
    assertions: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    notes: str = ""
    elapsed_sec: float = 0.0


RESULTS: List[ScenarioResult] = []


def _assert(r: ScenarioResult, condition: bool, msg_pass: str, msg_fail: str):
    if condition:
        r.assertions.append(f"✓ {msg_pass}")
    else:
        r.assertions.append(f"✗ {msg_fail}")
        r.failures.append(msg_fail)


def _run_full_negotiation(
    session: NegotiationSession,
    orchestrator: SimpleOrchestrator,
    max_loop_guard: int = 60,
    verbose: bool = True,
) -> NegotiationSession:
    """Run negotiation until terminal status or loop guard."""
    session = orchestrator.start_negotiation(session)

    if verbose and session.status not in (SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING):
        logger.info(f"  → pre-start terminal: {session.status.value}")
        return session

    for i in range(max_loop_guard):
        session = orchestrator.run_negotiation_round(session)

        if verbose and session.rounds:
            last = session.rounds[-1]
            logger.info(
                f"  Round {session.current_round:2d} | {last.role.value:8s} | "
                f"€{last.offer.unit_price:7.2f} | {last.offer.volume:5d} units | "
                f"{last.offer.delivery_days:2d}d | {last.offer.payment_terms:6s} | "
                f"valid={last.is_valid} | status={session.status.value}"
            )

        if session.status not in (SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING):
            break

    return session


def _make_session(
    product_id: str,
    product_name: str,
    supplier_limits: PartyLimits,
    retailer_limits: PartyLimits,
    initial_price: float,
    initial_volume: int,
    max_rounds: int = 20,
    initiator: AgentRole = AgentRole.SUPPLIER,
    supplier_id: str = "supplier-a",
    retailer_id: str = "retailer-b",
) -> NegotiationSession:
    return NegotiationSession(
        session_id=str(uuid.uuid4())[:8],
        product_id=product_id,
        product_name=product_name,
        initiator=initiator,
        supplier_id=supplier_id,
        retailer_id=retailer_id,
        initial_offer=NegotiationOffer(
            unit_price=initial_price,
            volume=initial_volume,
            delivery_days=14,
            payment_terms="Net 45",
        ),
        supplier_limits=supplier_limits,
        retailer_limits=retailer_limits,
        max_rounds=max_rounds,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

def run_scenario_01(orch):
    """Standard negotiation — Bosch Cordless Drill, wide ZOPA (€145–€220)."""
    r = ScenarioResult(
        number=1, name="Standard Negotiation – Wide ZOPA",
        category="NORMAL", product="Bosch GSR 18V-90 C Professional",
        supplier="Bosch", retailer="BAUHAUS",
        expected_status="accepted_or_pending",
        zopa_range="€145–€220",
    )
    t0 = time.time()
    session = _make_session(
        product_id="bosch-gsr-18v-90",
        product_name="Bosch GSR 18V-90 C Professional",
        supplier_limits=PartyLimits(
            min_price=145.0, max_price=200.0,
            min_volume=200, max_volume=5000,
            acceptable_payment_terms=["Net 45", "Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=220.0, min_volume=200, max_volume=1000,
            max_delivery_days=21,
            acceptable_payment_terms=["Net 30", "Net 45", "Net 60"],
        ),
        initial_price=189.0, initial_volume=500,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round

    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    terminal = session.status in (
        SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL, SessionStatus.MAX_ROUNDS,
    )
    _assert(r, terminal, "Terminal status reached", f"Non-terminal: {session.status.value}")
    _assert(r, session.zopa_exists, "ZOPA detected", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed: {session.status_message}")
    if r.final_price is not None:
        _assert(r, 145.0 <= r.final_price <= 220.0,
                f"Final price €{r.final_price:.2f} within ZOPA [€145–€220]",
                f"Price €{r.final_price:.2f} outside ZOPA [€145–€220]")
    _assert(r, session.current_round >= 2, "≥ 2 rounds played",
            f"Only {session.current_round} round(s) — too fast")
    _assert(r, session.current_round <= 20, "Rounds ≤ 20", f"Exceeded max: {session.current_round}")

    if session.status == SessionStatus.ACCEPTED:
        r.acceptance_type = "autonomous"
    elif session.status == SessionStatus.PENDING_APPROVAL:
        r.acceptance_type = "convergence"
    r.passed = len(r.failures) == 0
    return r


def run_scenario_02(orch):
    """Narrow ZOPA — Makita Angle Grinder, only €10 overlap (€115–€125)."""
    r = ScenarioResult(
        number=2, name="Narrow ZOPA – Makita Angle Grinder",
        category="NORMAL", product="Makita DGA513Z",
        supplier="Makita", retailer="OBI",
        expected_status="terminal",
        zopa_range="€115–€125",
    )
    t0 = time.time()
    session = _make_session(
        product_id="makita-dga513",
        product_name="Makita DGA513Z",
        supplier_limits=PartyLimits(
            min_price=115.0, max_price=160.0,
            min_volume=200, max_volume=4500,
            acceptable_payment_terms=["Net 45", "Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=125.0, min_volume=200, max_volume=800,
            max_delivery_days=20,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=145.0, initial_volume=400, max_rounds=25,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    _assert(r, session.zopa_exists, "ZOPA detected (€115–€125)", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.NO_ZOPA, "No false no_zopa", "Incorrectly NO_ZOPA")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed: {session.status_message}")
    if r.final_price is not None:
        _assert(r, r.final_price >= 115.0, f"Price €{r.final_price:.2f} ≥ supplier min €115",
                f"Price €{r.final_price:.2f} below supplier floor")
        _assert(r, r.final_price <= 125.0, f"Price €{r.final_price:.2f} ≤ retailer max €125",
                f"Price €{r.final_price:.2f} exceeds retailer ceiling")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_03(orch):
    """Supplier-initiated negotiation — STIHL Chainsaw (HORNBACH)."""
    r = ScenarioResult(
        number=3, name="Supplier-Initiated – STIHL Chainsaw",
        category="NORMAL", product="STIHL MSA 140 C-B",
        supplier="STIHL", retailer="HORNBACH",
        expected_status="terminal",
        zopa_range="€239–€330",
    )
    t0 = time.time()
    session = _make_session(
        product_id="stihl-msa-140",
        product_name="STIHL MSA 140 C-B",
        supplier_limits=PartyLimits(
            min_price=239.0, max_price=350.0,
            min_volume=100, max_volume=1800,
            acceptable_payment_terms=["Net 45", "Net 60"],
        ),
        retailer_limits=PartyLimits(
            max_price=330.0, min_volume=100, max_volume=600,
            max_delivery_days=25,
            acceptable_payment_terms=["Net 45", "Net 60"],
        ),
        initial_price=289.0, initial_volume=200,
        initiator=AgentRole.SUPPLIER,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    _assert(r, session.initiator == AgentRole.SUPPLIER, "Supplier is initiator", "Initiator mismatch")
    _assert(r, session.zopa_exists, "ZOPA detected (€239–€330)", "ZOPA not detected")
    _assert(r, session.status not in (SessionStatus.FAILED, SessionStatus.NO_ZOPA),
            "No unexpected failure", f"Bad status: {session.status.value}")
    _assert(r, session.rounds, "At least 1 round played", "No rounds played")
    if r.final_price is not None:
        _assert(r, r.final_price >= 239.0, f"Price €{r.final_price:.2f} ≥ supplier min",
                f"Price €{r.final_price:.2f} below supplier floor €239")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_04(orch):
    """High-volume negotiation — Kärcher K5 Pressure Washer, 1000+ units."""
    r = ScenarioResult(
        number=4, name="High Volume Negotiation – Kärcher K5",
        category="NORMAL", product="Kärcher K 5 Premium",
        supplier="Kärcher", retailer="hagebau",
        expected_status="terminal",
        zopa_range="€289–€420",
    )
    t0 = time.time()
    session = _make_session(
        product_id="karcher-k5-premium",
        product_name="Kärcher K 5 Premium Full Control Plus",
        supplier_limits=PartyLimits(
            min_price=289.0, max_price=400.0,
            min_volume=80, max_volume=1500,
            acceptable_payment_terms=["Net 30", "Net 45", "Net 60"],
        ),
        retailer_limits=PartyLimits(
            max_price=420.0, min_volume=500, max_volume=1500,
            max_delivery_days=20,
            acceptable_payment_terms=["Net 45", "Net 60"],
        ),
        initial_price=345.0, initial_volume=1000,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    _assert(r, session.zopa_exists, "ZOPA detected", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed")
    if final_offer:
        _assert(r, final_offer.volume >= 80 and final_offer.volume <= 1500,
                f"Volume {final_offer.volume} within supplier capacity [80–1500]",
                f"Volume {final_offer.volume} violates supplier capacity")
        _assert(r, r.final_price >= 289.0, f"Price €{r.final_price:.2f} ≥ supplier min",
                f"Price €{r.final_price:.2f} below floor €289")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_05(orch):
    """Premium segment — Weber Spirit Gas Grill, seasonal product."""
    r = ScenarioResult(
        number=5, name="Premium Segment – Weber Spirit Gas Grill",
        category="NORMAL", product="Weber Spirit E-325s GBS",
        supplier="Weber", retailer="BAUHAUS",
        expected_status="terminal",
        zopa_range="€549–€780",
    )
    t0 = time.time()
    session = _make_session(
        product_id="weber-spirit-e325",
        product_name="Weber Spirit E-325s GBS",
        supplier_limits=PartyLimits(
            min_price=549.0, max_price=800.0,
            min_volume=50, max_volume=800,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        retailer_limits=PartyLimits(
            max_price=780.0, min_volume=50, max_volume=300,
            max_delivery_days=30,
            acceptable_payment_terms=["Net 45", "Net 60"],
        ),
        initial_price=649.0, initial_volume=100,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    _assert(r, session.zopa_exists, "ZOPA detected (€549–€780)", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed")
    if r.final_price:
        _assert(r, r.final_price >= 549.0, f"Price €{r.final_price:.2f} ≥ supplier min €549",
                f"Price €{r.final_price:.2f} below supplier floor")
        _assert(r, r.final_price <= 780.0, f"Price €{r.final_price:.2f} ≤ retailer max €780",
                f"Price €{r.final_price:.2f} exceeds retailer ceiling")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_06(orch):
    """Retailer-initiated negotiation — DeWalt Drill Kit (toom)."""
    r = ScenarioResult(
        number=6, name="Retailer-Initiated – DeWalt Drill Kit",
        category="NORMAL", product="DEWALT DCD796P2",
        supplier="DeWalt", retailer="toom",
        expected_status="terminal",
        zopa_range="€189–€280",
    )
    t0 = time.time()
    session = _make_session(
        product_id="dewalt-dcd796",
        product_name="DEWALT DCD796P2",
        supplier_limits=PartyLimits(
            min_price=189.0, max_price=290.0,
            min_volume=180, max_volume=4200,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        retailer_limits=PartyLimits(
            max_price=280.0, min_volume=200, max_volume=1000,
            max_delivery_days=18,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=234.0, initial_volume=300,
        initiator=AgentRole.RETAILER,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    _assert(r, session.initiator == AgentRole.RETAILER, "Retailer is initiator", "Initiator mismatch")
    _assert(r, session.zopa_exists, "ZOPA detected", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed: {session.status_message}")
    if r.final_price is not None:
        _assert(r, 189.0 <= r.final_price <= 280.0,
                f"Price €{r.final_price:.2f} in ZOPA [€189–€280]",
                f"Price €{r.final_price:.2f} outside ZOPA")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_07(orch):
    """Robustness — GARDENA SilentCut, optional fields absent."""
    r = ScenarioResult(
        number=7, name="Optional Fields Missing – GARDENA SilentCut",
        category="NORMAL", product="GARDENA SilentCut 400 Li",
        supplier="GARDENA", retailer="OBI",
        expected_status="terminal",
        zopa_range="€145–€220",
        notes="No max_price, no max_volume, no delivery limit on supplier side",
    )
    t0 = time.time()
    session = _make_session(
        product_id="gardena-silentcut",
        product_name="GARDENA SilentCut 400 Li",
        supplier_limits=PartyLimits(
            min_price=145.0,
            min_volume=150,
            acceptable_payment_terms=["Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=220.0,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=178.0, initial_volume=300,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round

    _assert(r, session.status != SessionStatus.FAILED,
            "No crash with missing optional fields", f"Crashed: {session.status_message}")
    _assert(r, session.zopa_exists, "ZOPA detected", "ZOPA not detected")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_08(orch):
    """Symmetric ZOPA — Bosch Laser Meter, supplier min = retailer max target."""
    r = ScenarioResult(
        number=8, name="Symmetric ZOPA – Bosch Laser Meter",
        category="NORMAL", product="Bosch GLM 50-27 CG",
        supplier="Bosch", retailer="HELLWEG",
        expected_status="accepted_or_pending",
        zopa_range="€125–€175",
    )
    t0 = time.time()
    session = _make_session(
        product_id="bosch-glm-50-27",
        product_name="Bosch GLM 50-27 CG Professional",
        supplier_limits=PartyLimits(
            min_price=125.0, max_price=175.0,
            min_volume=300, max_volume=8000,
            acceptable_payment_terms=["Net 45", "Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=175.0, min_volume=300,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=150.0, initial_volume=500, max_rounds=15,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    terminal = session.status in (
        SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL, SessionStatus.MAX_ROUNDS,
    )
    _assert(r, terminal, f"Terminal status: {session.status.value}", f"Non-terminal: {session.status.value}")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", "Session failed")
    if r.final_price is not None:
        _assert(r, 125.0 <= r.final_price <= 175.0,
                f"Price €{r.final_price:.2f} within ZOPA [€125–€175]",
                f"Price €{r.final_price:.2f} outside ZOPA")
    if session.status == SessionStatus.ACCEPTED:
        r.acceptance_type = "autonomous"
        _assert(r, session.supplier_approved and session.retailer_approved,
                "Both approved on ACCEPTED", "Approval flags not set")
    r.passed = len(r.failures) == 0
    return r


# ── COMPLEMENT SCENARIOS ──────────────────────────────────────────────────────

def run_scenario_09(orch):
    """Complement good — Bosch 18V Battery Pack (accessory to GSR drill)."""
    r = ScenarioResult(
        number=9, name="Complement Good – Bosch 18V Battery 2-Pack",
        category="COMPLEMENT", product="Bosch 18V 5.0Ah Battery 2-Pack",
        supplier="Bosch", retailer="BAUHAUS",
        expected_status="terminal",
        zopa_range="€119–€185",
        notes="Accessory: lower price, higher MOQ, 14-day lead time",
    )
    t0 = time.time()
    session = _make_session(
        product_id="bosch-18v-battery-set",
        product_name="Bosch Professional 18V 5.0Ah Battery 2-Pack",
        supplier_limits=PartyLimits(
            min_price=119.0, max_price=180.0,
            min_volume=300, max_volume=8000,
            acceptable_payment_terms=["Net 45", "Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=185.0, min_volume=300, max_volume=3000,
            max_delivery_days=14,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=159.0, initial_volume=600,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    _assert(r, session.zopa_exists, "ZOPA detected for accessory", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", "Session failed")
    if r.final_price is not None:
        _assert(r, r.final_price >= 119.0, f"Price €{r.final_price:.2f} ≥ supplier min €119",
                f"Price below floor: €{r.final_price:.2f}")
        _assert(r, r.final_price <= 185.0, f"Price €{r.final_price:.2f} ≤ retailer max €185",
                f"Price above ceiling: €{r.final_price:.2f}")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_10(orch):
    """Low-price consumable — Bosch Bit & Drill Set (~€15–€30). Aligned payment/delivery."""
    r = ScenarioResult(
        number=10, name="Low-Price Consumable – Bosch Bit & Drill Set",
        category="COMPLEMENT", product="Bosch 43-Piece Bit & Drill Set",
        supplier="Bosch", retailer="toom",
        expected_status="terminal",
        zopa_range="€15–€30",
        notes="Aligned payment (Net 30) + delivery (14d ≤ 21d) — no constraint deadlock",
    )
    t0 = time.time()
    session = _make_session(
        product_id="bosch-bit-drill-set",
        product_name="Bosch 43-Piece Screwdriver Bit & Drill Set",
        supplier_limits=PartyLimits(
            min_price=15.0, max_price=30.0,
            min_volume=1500, max_volume=40000,
            acceptable_payment_terms=["Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=30.0, min_volume=2000, max_volume=20000,
            max_delivery_days=21,   # ← raised from 10 to 21 to avoid delivery deadlock
            acceptable_payment_terms=["Net 30"],  # aligned with supplier
        ),
        initial_price=24.0, initial_volume=5000,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    _assert(r, session.zopa_exists, "ZOPA detected (€15–€30)", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", "Session failed")
    if r.final_price is not None:
        _assert(r, r.final_price >= 15.0, f"Price €{r.final_price:.2f} ≥ min €15",
                f"Price below minimum: €{r.final_price:.2f}")
        _assert(r, r.final_price <= 30.0, f"Price €{r.final_price:.2f} ≤ max €30",
                f"Price above retailer max: €{r.final_price:.2f}")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_11(orch):
    """Bundle complement — Kärcher K5 + Surface Cleaner T7 (two parallel sessions)."""
    r = ScenarioResult(
        number=11, name="Bundle Complement – Kärcher K5 + Surface Cleaner T7",
        category="COMPLEMENT", product="Kärcher K5 + T7 Accessory Bundle",
        supplier="Kärcher", retailer="hagebau",
        expected_status="both_terminal",
        zopa_range="K5: €289–€420 | T7: €45–€80",
        notes="Two products negotiated independently — tests session isolation",
    )
    t0 = time.time()
    session_a = _make_session(
        product_id="karcher-k5-premium",
        product_name="Kärcher K 5 Premium Full Control Plus",
        supplier_limits=PartyLimits(
            min_price=289.0, max_price=400.0, min_volume=80, max_volume=1500,
            acceptable_payment_terms=["Net 30", "Net 45", "Net 60"],
        ),
        retailer_limits=PartyLimits(
            max_price=420.0, min_volume=200, max_volume=800,
            acceptable_payment_terms=["Net 45", "Net 60"],
        ),
        initial_price=345.0, initial_volume=400,
    )
    session_b = _make_session(
        product_id="karcher-surface-cleaner-t7",
        product_name="Kärcher T 7 Plus Surface Cleaner",
        supplier_limits=PartyLimits(
            min_price=45.0, max_price=79.0, min_volume=200, max_volume=5000,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        retailer_limits=PartyLimits(
            max_price=80.0, min_volume=200, max_volume=2000,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=62.0, initial_volume=500,
    )
    logger.info("  [Session A: Kärcher K5]")
    session_a = _run_full_negotiation(session_a, orch)
    logger.info("  [Session B: Kärcher T7]")
    session_b = _run_full_negotiation(session_b, orch)
    r.elapsed_sec = time.time() - t0

    both_terminal = all(
        s.status in (
            SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL,
            SessionStatus.MAX_ROUNDS, SessionStatus.NO_ZOPA,
        )
        for s in (session_a, session_b)
    )
    r.actual_status = f"A={session_a.status.value} | B={session_b.status.value}"
    r.rounds_used = session_a.current_round + session_b.current_round
    final_a = next((rnd.offer for rnd in reversed(session_a.rounds)), None)
    final_b = next((rnd.offer for rnd in reversed(session_b.rounds)), None)
    if final_a:
        r.final_price = final_a.unit_price

    _assert(r, both_terminal, "Both sessions terminal",
            f"Not both terminal: {r.actual_status}")
    _assert(r, session_a.zopa_exists, "K5 ZOPA detected", "K5 ZOPA missing")
    _assert(r, session_b.zopa_exists, "T7 ZOPA detected", "T7 ZOPA missing")
    if final_a:
        _assert(r, final_a.unit_price >= 289.0,
                f"K5 price €{final_a.unit_price:.2f} ≥ min €289",
                f"K5 price €{final_a.unit_price:.2f} below floor")
    if final_b:
        _assert(r, final_b.unit_price >= 45.0,
                f"T7 price €{final_b.unit_price:.2f} ≥ min €45",
                f"T7 price below floor: €{final_b.unit_price:.2f}")

    r.notes += f" | K5: €{final_a.unit_price:.2f}" if final_a else ""
    r.notes += f" | T7: €{final_b.unit_price:.2f}" if final_b else ""
    r.passed = len(r.failures) == 0
    return r


# ── EDGE CASE SCENARIOS ───────────────────────────────────────────────────────

def run_scenario_12(orch):
    """
    Edge: No overlap — Supplier min (€135) > Retailer max (€120).

    Design principle (ZOPA-free): negotiation starts regardless of overlap.
    Agents discover they cannot agree and the session runs to MAX_ROUNDS.
    The overlap flag is set to False for analytics, but never blocks the session.
    """
    r = ScenarioResult(
        number=12, name="Edge: No Price Overlap — Agents Discover Gap",
        category="EDGE CASE", product="DEWALT DCG405N",
        supplier="DeWalt", retailer="toom",
        expected_status="max_rounds_or_accepted",
        zopa_range="None (gap = €15, supplier_min €135 > retailer_max €120)",
        notes="No ZOPA-block: agents negotiate, analytics flag overlap=False, session runs to MAX_ROUNDS",
    )
    t0 = time.time()
    session = _make_session(
        product_id="dewalt-dcg405",
        product_name="DEWALT DCG405N Angle Grinder",
        supplier_limits=PartyLimits(
            min_price=135.0, max_price=200.0,
            min_volume=160, max_volume=3800,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        retailer_limits=PartyLimits(
            max_price=120.0, min_volume=200,
            acceptable_payment_terms=["Net 30"],
        ),
        initial_price=167.0, initial_volume=300,
        max_rounds=10,  # short cap for faster test
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round

    # Negotiation starts — never blocked by ZOPA pre-check
    _assert(r, session.status != SessionStatus.NO_ZOPA,
            "No pre-flight ZOPA block", f"Old NO_ZOPA block still active: {session.status.value}")
    # Overlap analytics must be False (supplier_min > retailer_max)
    _assert(r, session.zopa_exists is False,
            "Overlap analytics correctly False", f"Expected False, got {session.zopa_exists}")
    # Session must reach a terminal state
    terminal = session.status in (
        SessionStatus.MAX_ROUNDS, SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL,
    )
    _assert(r, terminal, f"Terminal status: {session.status.value}",
            f"Non-terminal: {session.status.value}")
    # At least one round must have been played
    _assert(r, session.current_round >= 1,
            f"Agents negotiated ({session.current_round} rounds)",
            "0 rounds played — ZOPA block still active")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_13(orch):
    """Edge: Max rounds cap enforced — orchestrator never exceeds max_rounds."""
    MAX = 4
    r = ScenarioResult(
        number=13, name="Edge: Max Rounds Limit Enforced",
        category="EDGE CASE", product="Weber Master-Touch GBS",
        supplier="Weber", retailer="HORNBACH",
        expected_status="terminal_within_cap",
        zopa_range="€239–€350 (max_rounds=4)",
        notes=f"max_rounds={MAX} — verifies cap invariant regardless of outcome",
    )
    t0 = time.time()
    session = _make_session(
        product_id="weber-master-touch",
        product_name="Weber Master-Touch GBS E-5750",
        supplier_limits=PartyLimits(
            min_price=239.0, max_price=370.0,
            min_volume=80, max_volume=1200,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        retailer_limits=PartyLimits(
            max_price=350.0, min_volume=80,
            acceptable_payment_terms=["Net 45"],
        ),
        initial_price=289.0, initial_volume=100,
        max_rounds=MAX,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round

    terminal = session.status in (
        SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL, SessionStatus.MAX_ROUNDS,
    )
    _assert(r, terminal, f"Terminal: {session.status.value}", f"Non-terminal: {session.status.value}")
    _assert(r, session.current_round <= MAX,
            f"Rounds ({session.current_round}) ≤ cap ({MAX})",
            f"Cap exceeded: {session.current_round} > {MAX}")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed: {session.status_message}")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_14(orch):
    """Edge: Point ZOPA — Supplier min exactly equals Retailer max (€119)."""
    r = ScenarioResult(
        number=14, name="Edge: Point ZOPA (Single Valid Price)",
        category="EDGE CASE", product="STIHL FSA 57 Trimmer",
        supplier="STIHL", retailer="hagebau",
        expected_status="terminal",
        zopa_range="Exactly €119 (point)",
        notes="supplier_min == retailer_max → only one valid price exists",
    )
    t0 = time.time()
    session = _make_session(
        product_id="stihl-fsa-57",
        product_name="STIHL FSA 57",
        supplier_limits=PartyLimits(
            min_price=119.0, max_price=200.0,
            min_volume=150, max_volume=2800,
            acceptable_payment_terms=["Net 45", "Net 60"],
        ),
        retailer_limits=PartyLimits(
            max_price=119.0,
            min_volume=150,
            acceptable_payment_terms=["Net 45", "Net 60"],
        ),
        initial_price=145.0, initial_volume=300, max_rounds=25,
    )
    session = _run_full_negotiation(session, orch)
    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round

    _assert(r, session.zopa_exists, "Point ZOPA detected as valid", "ZOPA not detected")
    _assert(r, session.status != SessionStatus.NO_ZOPA,
            "Not rejected as no_zopa", "Point ZOPA incorrectly rejected")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed")
    r.passed = len(r.failures) == 0
    return r


def run_scenario_15(orch):
    """Edge: HITL check — stall detection across a multi-round negotiation."""
    r = ScenarioResult(
        number=15, name="Edge: HITL Stall Detection",
        category="EDGE CASE", product="Makita DHP486Z Drill",
        supplier="Makita", retailer="OBI",
        expected_status="hitl_or_terminal",
        zopa_range="€98–€165",
        notes="HITL check called after each round; both trigger and non-trigger are valid",
    )
    t0 = time.time()
    session = _make_session(
        product_id="makita-dhp486",
        product_name="Makita DHP486Z",
        supplier_limits=PartyLimits(
            min_price=98.0, max_price=165.0,
            min_volume=250, max_volume=6000,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        retailer_limits=PartyLimits(
            max_price=165.0, min_volume=250,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=124.0, initial_volume=500, max_rounds=20,
    )
    session = orch.start_negotiation(session)

    hitl_found = None
    for _ in range(20):
        if session.status not in (SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING):
            break
        session = orch.run_negotiation_round(session)
        if session.rounds:
            last = session.rounds[-1]
            logger.info(
                f"  Round {session.current_round:2d} | {last.role.value:8s} | "
                f"€{last.offer.unit_price:7.2f} | status={session.status.value}"
            )
        hitl = orch.check_hitl_needed(session)
        if hitl and not hitl_found:
            hitl_found = hitl
            logger.info(f"  HITL triggered: {hitl.reason.value} — {hitl.message}")

    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    r.hitl_triggered = hitl_found is not None

    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed")
    _assert(r, session.current_round <= 20, "Rounds ≤ 20", f"Exceeded: {session.current_round}")
    r.notes += f" | HITL: {'triggered (' + hitl_found.reason.value + ')' if r.hitl_triggered else 'not triggered (converged normally)'}"
    r.passed = len(r.failures) == 0
    return r


def run_scenario_16(orch):
    """Edge: Full approval flow — convergence → PENDING_APPROVAL → both approve → ACCEPTED."""
    r = ScenarioResult(
        number=16, name="Edge: Full Approval Flow",
        category="EDGE CASE", product="GARDENA Smart Water Control",
        supplier="GARDENA", retailer="OBI",
        expected_status="accepted",
        zopa_range="€109–€170",
        notes="PENDING_APPROVAL → manual approve by both parties → ACCEPTED",
    )
    t0 = time.time()
    session = _make_session(
        product_id="gardena-smartsystem",
        product_name="GARDENA smart Water Control Set",
        supplier_limits=PartyLimits(
            min_price=109.0, max_price=170.0,
            min_volume=200, max_volume=3500,
            acceptable_payment_terms=["Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=170.0, min_volume=200,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        initial_price=134.0, initial_volume=400,
    )
    session = _run_full_negotiation(session, orch)

    if session.status == SessionStatus.PENDING_APPROVAL:
        r.acceptance_type = "convergence+approval"
        session = orch.approve_deal(session, AgentRole.SUPPLIER)
        session = orch.approve_deal(session, AgentRole.RETAILER)
        logger.info(f"  Manual approval applied → {session.status.value}")
    elif session.status == SessionStatus.ACCEPTED:
        r.acceptance_type = "autonomous"

    r.elapsed_sec = time.time() - t0
    r.actual_status = session.status.value
    r.rounds_used = session.current_round
    final_offer = next((rnd.offer for rnd in reversed(session.rounds)), None)
    r.final_price = final_offer.unit_price if final_offer else None

    terminal_ok = session.status in (SessionStatus.ACCEPTED, SessionStatus.MAX_ROUNDS)
    _assert(r, terminal_ok, f"Terminal: {session.status.value}", f"Non-terminal: {session.status.value}")
    _assert(r, session.status != SessionStatus.FAILED, "No crash", f"Failed")
    if session.status == SessionStatus.ACCEPTED:
        _assert(r, session.supplier_approved, "Supplier approved", "Supplier not approved")
        _assert(r, session.retailer_approved, "Retailer approved", "Retailer not approved")
    if r.final_price is not None:
        _assert(r, 109.0 <= r.final_price <= 170.0,
                f"Price €{r.final_price:.2f} in ZOPA [€109–€170]",
                f"Price €{r.final_price:.2f} outside ZOPA")
    r.passed = len(r.failures) == 0
    return r


# ══════════════════════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECK
# ══════════════════════════════════════════════════════════════════════════════

def _preflight_check(llm: AICoreClient) -> bool:
    """
    Verify SAP AI Core connectivity before running all 16 scenarios.
    Sends a minimal probe request and checks for a non-empty response.
    """
    print("\n  ─── Pre-flight: Checking SAP AI Core connectivity ───")
    try:
        t0 = time.time()
        response = llm.generate(
            messages=[{"role": "user", "content": "Reply with exactly one word: READY"}],
            temperature=0.0,
        )
        elapsed = time.time() - t0
        if response and len(response.strip()) > 0:
            print(f"  ✅ AI Core connected ({elapsed:.1f}s) | model={llm.model_name}")
            print(f"     Response: {response.strip()[:60]}")
            return True
        else:
            print(f"  ❌ AI Core returned empty response")
            return False
    except Exception as e:
        print(f"  ❌ AI Core connection failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════════════════

def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def print_report(results: List[ScenarioResult]):
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    total_time = sum(r.elapsed_sec for r in results)

    PASS_MARK = "✅ PASS"
    FAIL_MARK = "❌ FAIL"
    divider = "═" * 116

    print()
    print(divider)
    print(f"  AGENTIC 2.0 — END-TO-END NEGOTIATION TEST REPORT  (REAL LLM: SAP AI Core / GPT-4o)")
    print(divider)
    print()

    pct = (passed / total * 100) if total else 0
    print(f"  Result : {passed}/{total} scenarios passed  [{_bar(pct)}] {pct:.0f}%")
    print(f"  Time   : {total_time:.1f}s total  ({total_time/total:.1f}s avg per scenario)")
    print()

    categories = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    for cat, group in categories.items():
        cat_pass = sum(1 for r in group if r.passed)
        print(f"  ┌─ {cat} ({cat_pass}/{len(group)} passed) {'─' * (86 - len(cat))}")
        print(f"  │")
        print(f"  │  {'#':<3}  {'Scenario':<45} {'Status':<14} {'Rounds':<7} {'Price':<10} {'Time':>6}  {'Result'}")
        print(f"  │  {'─'*3}  {'─'*45} {'─'*14} {'─'*7} {'─'*10} {'─'*6}  {'─'*9}")
        for r in group:
            price_str = f"€{r.final_price:.2f}" if r.final_price else "—"
            mark = PASS_MARK if r.passed else FAIL_MARK
            status_short = r.actual_status[:14] if r.actual_status else "—"
            print(
                f"  │  {r.number:<3}  {r.name:<45} {status_short:<14} {r.rounds_used:<7} "
                f"{price_str:<10} {r.elapsed_sec:5.1f}s  {mark}"
            )
        print(f"  │")

    print(f"  └{'─' * 114}")
    print()

    # Detailed breakdown
    print(f"  {'─' * 114}")
    print(f"  DETAILED SCENARIO BREAKDOWN")
    print(f"  {'─' * 114}")
    for r in results:
        mark = "✅" if r.passed else "❌"
        print()
        print(f"  {mark} #{r.number} — {r.name}  [{r.elapsed_sec:.1f}s]")
        print(f"     Product : {r.product}")
        print(f"     Parties : {r.supplier} (supplier) ↔ {r.retailer} (retailer)")
        print(f"     ZOPA    : {r.zopa_range}")
        price_display = f"€{r.final_price:.2f}" if r.final_price else "—"
        print(f"     Status  : {r.actual_status}  |  Rounds: {r.rounds_used}  |  Final Price: {price_display}")
        if r.acceptance_type:
            print(f"     Accept  : {r.acceptance_type}")
        if r.hitl_triggered:
            print(f"     HITL    : triggered")
        if r.notes:
            print(f"     Notes   : {r.notes}")
        for a in r.assertions:
            print(f"       {a}")
        if r.failures:
            print(f"     FAILURES:")
            for f in r.failures:
                print(f"       ✗ {f}")

    print()
    print(divider)
    print(f"  SUMMARY")
    print(f"  {'─' * 114}")
    print(f"  Total Scenarios : {total}")
    print(f"  Passed          : {passed}  ✅")
    print(f"  Failed          : {failed}  {'❌' if failed > 0 else '—'}")
    print(f"  Total Time      : {total_time:.1f}s")
    print(f"  LLM             : SAP AI Core / GPT-4o  (no mocking)")
    print(divider)
    print()

    return failed


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Instantiate real LLM client — reads credentials from .env
    llm = AICoreClient(
        model_name=os.getenv("AICORE_MODEL_NAME", "gpt-4o"),
        temperature=0.3,    # Slight randomness for realistic tactic variation
        max_tokens=1024,
        deployment_id=os.getenv("AICORE_DEPLOYMENT_ID", ""),
    )

    # Pre-flight check
    if not _preflight_check(llm):
        print("\n  ❌ Cannot reach SAP AI Core. Check .env credentials.\n")
        sys.exit(1)

    orchestrator = SimpleOrchestrator(llm_client=llm)

    print(f"\n  Running {16} E2E Negotiation Scenarios with live LLM calls ...\n")
    print(f"  ⚠  Each scenario makes multiple real AI Core API calls.")
    print(f"     Expected runtime: 3–10 minutes depending on negotiation complexity.\n")

    SCENARIOS = [
        ("NORMAL",      [
            (run_scenario_01, "Standard Negotiation – Wide ZOPA"),
            (run_scenario_02, "Narrow ZOPA – Makita Angle Grinder"),
            (run_scenario_03, "Supplier-Initiated – STIHL Chainsaw"),
            (run_scenario_04, "High Volume – Kärcher K5"),
            (run_scenario_05, "Premium Segment – Weber Spirit Grill"),
            (run_scenario_06, "Retailer-Initiated – DeWalt Drill Kit"),
            (run_scenario_07, "Optional Fields Missing – GARDENA"),
            (run_scenario_08, "Symmetric ZOPA – Bosch Laser Meter"),
        ]),
        ("COMPLEMENT",  [
            (run_scenario_09, "Complement – Bosch Battery Pack"),
            (run_scenario_10, "Low-Price Consumable – Bosch Bit Set"),
            (run_scenario_11, "Bundle – Kärcher K5 + T7 Cleaner"),
        ]),
        ("EDGE CASE",   [
            (run_scenario_12, "No ZOPA"),
            (run_scenario_13, "Max Rounds Cap"),
            (run_scenario_14, "Point ZOPA"),
            (run_scenario_15, "HITL Stall Detection"),
            (run_scenario_16, "Full Approval Flow"),
        ]),
    ]

    idx = 0
    for cat, scenario_list in SCENARIOS:
        print(f"\n  ── {cat} ──────────────────────────────────────────────────────")
        for fn, label in scenario_list:
            idx += 1
            print(f"\n  [{idx:02d}/16] {label}")
            try:
                result = fn(orchestrator)
                RESULTS.append(result)
                status_mark = "✅" if result.passed else "❌"
                print(
                    f"  {status_mark} Done: status={result.actual_status}  "
                    f"rounds={result.rounds_used}  "
                    f"price={'€'+str(round(result.final_price, 2)) if result.final_price else '—'}  "
                    f"({result.elapsed_sec:.1f}s)"
                )
                if result.failures:
                    for f in result.failures:
                        print(f"     ✗ {f}")
            except Exception as exc:
                logger.error(f"Scenario {idx} crashed: {exc}", exc_info=True)
                crash_result = ScenarioResult(
                    number=idx, name=label, category=cat,
                    product="—", supplier="—", retailer="—",
                    expected_status="—", passed=False,
                    actual_status="CRASHED",
                    failures=[f"Unhandled exception: {exc}"],
                )
                RESULTS.append(crash_result)

    failed_count = print_report(RESULTS)
    sys.exit(0 if failed_count == 0 else 1)