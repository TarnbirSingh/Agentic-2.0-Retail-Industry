"""
Quick Test: Autonomous Walk-Away bei No-ZOPA-Szenario
─────────────────────────────────────────────────────────

Testet, ob der Agent bei No-ZOPA selbständig walk_away erkennt
BEVOR das max_rounds-Limit erreicht wird.

Erwartetes Verhalten:
- Stagnation Detection triggert nach ~4-5 Runden
- Agent beendet mit WALK_AWAY_SIGNAL
- Begründung: "Keine Konvergenz erkennbar"
"""

# ── Load .env before anything else ───────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass  # python-dotenv optional — env vars must be set manually

import logging
import time
import uuid
from datetime import datetime

from models.negotiation_models import (
    AgentRole,
    NegotiationOffer,
    NegotiationSession,
    PartyLimits,
    SessionStatus,
)
from orchestration.simple_orchestrator import SimpleOrchestrator
from llm.ai_core_client import AICoreClient

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


def test_no_zopa_autonomous_walkaway(orch: SimpleOrchestrator):
    """Test No-ZOPA mit autonomem Walk-Away."""
    
    print("\n" + "="*80)
    print("TEST: No-ZOPA → Autonomer Walk-Away")
    print("="*80 + "\n")
    
    # Szenario: Keine Überlappung
    # Retailer: max €100 | Supplier: min €130
    # → Kein ZOPA, sollte nach ~5 Runden mit Walk-Away enden
    
    session = NegotiationSession(
        session_id=str(uuid.uuid4())[:8],
        product_id="TEST_NO_ZOPA",
        product_name="Test Product No ZOPA",
        initiator=AgentRole.SUPPLIER,
        supplier_id="ProdRill",
        retailer_id="REWE",
        initial_offer=NegotiationOffer(
            unit_price=115.0,
            volume=1000,
            delivery_days=14,
            payment_terms="Net 45",
        ),
        supplier_limits=PartyLimits(
            min_price=130.0,  # Supplier braucht min €130
            max_price=None,
            min_volume=800,
            max_volume=1200,
            acceptable_payment_terms=["Net 45", "Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=100.0,  # Retailer zahlt max €100
            min_volume=800,
            max_volume=1200,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        max_rounds=15,  # Genug Spielraum, aber Agent sollte vorher stoppen
    )
    
    print(f"Szenario: No ZOPA")
    print(f"  Retailer max: €{session.retailer_limits.max_price}")
    print(f"  Supplier min: €{session.supplier_limits.min_price}")
    print(f"  Gap: €{session.supplier_limits.min_price - session.retailer_limits.max_price}")
    print(f"  Max Rounds: {session.max_rounds}")
    print(f"\nErwartung: Agent erkennt Stagnation nach ~4-6 Runden")
    print(f"           und triggert autonomen Walk-Away\n")
    
    start_time = time.time()
    
    try:
        # Start negotiation
        session = orch.start_negotiation(session)
        
        # Run rounds
        for i in range(60):  # Loop guard
            if session.status not in (SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING):
                break
            session = orch.run_negotiation_round(session)
            if session.rounds:
                last = session.rounds[-1]
                print(f"  Runde {session.current_round:2d} | {last.role.value:8s} | "
                      f"€{last.offer.unit_price:7.2f} | status={session.status.value}")
        
        duration = time.time() - start_time
        
        print("\n" + "="*80)
        print("ERGEBNIS")
        print("="*80)
        print(f"Status: {session.status.value}")
        print(f"Runden: {session.current_round} von {session.max_rounds}")
        print(f"Dauer: {duration:.1f}s")
        
        final_offer = session.rounds[-1].offer if session.rounds else None
        if final_offer:
            print(f"\nFinales Angebot:")
            print(f"  Preis: €{final_offer.unit_price:.2f}")
            print(f"  Menge: {final_offer.volume}")
        
        # Analyse: Wurde walk_away VOR max_rounds erreicht?
        if session.status in (SessionStatus.MAX_ROUNDS, SessionStatus.FAILED):
            if session.current_round < session.max_rounds:
                print(f"\n✅ SUCCESS: Autonomer Walk-Away nach {session.current_round} Runden")
                print(f"            (vor max_rounds={session.max_rounds})")
                
                # Prüfe letzte Runden auf STAGNATION
                if session.rounds:
                    for rnd in session.rounds[-3:]:
                        if rnd.agent_reasoning:
                            reasoning_str = str(rnd.agent_reasoning)
                            if "STAGNATION" in reasoning_str.upper():
                                print(f"\n  🎯 Runde {rnd.round_number} ({rnd.role.value}): Stagnation erkannt!")
                                print(f"     Reasoning: {reasoning_str[:200]}...")
            else:
                print(f"\n⚠️  FAILED: max_rounds erreicht ({session.current_round})")
                print(f"           Agent hat NICHT autonom gestoppt")
        else:
            print(f"\n⚠️  UNEXPECTED: Status={session.status.value}")
        
        # Detaillierte Runden-Historie
        print(f"\n{'─'*80}")
        print("VERHANDLUNGSVERLAUF")
        print(f"{'─'*80}")
        
        for r in session.rounds:
            print(f"\nRunde {r.round_number} ({r.role.value}):")
            print(f"  Preis: €{r.offer.unit_price:.2f}")
            print(f"  Menge: {r.offer.volume}")
            
            # Zeige Reasoning-Highlights
            if r.agent_reasoning:
                reasoning_str = str(r.agent_reasoning)
                if "walk" in reasoning_str.lower() or "stagnation" in reasoning_str.lower():
                    print(f"    💭 {reasoning_str[:150]}...")
        
        print("\n" + "="*80)
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        print(f"\n❌ ERROR: {e}")


def test_narrow_zopa_without_walkaway(orch: SimpleOrchestrator):
    """Vergleichstest: Narrow ZOPA sollte NICHT zum Walk-Away führen."""
    
    print("\n" + "="*80)
    print("VERGLEICHSTEST: Narrow ZOPA (kein Walk-Away erwartet)")
    print("="*80 + "\n")
    
    # Szenario: Kleine Überlappung
    # Retailer: max €120 | Supplier: min €115
    # → ZOPA €115-120, sollte erfolgreich verhandelt werden
    
    session = NegotiationSession(
        session_id=str(uuid.uuid4())[:8],
        product_id="TEST_NARROW_ZOPA",
        product_name="Test Product Narrow ZOPA",
        initiator=AgentRole.SUPPLIER,
        supplier_id="ProdRill",
        retailer_id="REWE",
        initial_offer=NegotiationOffer(
            unit_price=117.0,
            volume=1000,
            delivery_days=14,
            payment_terms="Net 45",
        ),
        supplier_limits=PartyLimits(
            min_price=115.0,
            max_price=None,
            min_volume=800,
            max_volume=1200,
            acceptable_payment_terms=["Net 45", "Net 30"],
        ),
        retailer_limits=PartyLimits(
            max_price=120.0,
            min_volume=800,
            max_volume=1200,
            acceptable_payment_terms=["Net 30", "Net 45"],
        ),
        max_rounds=15,
    )
    
    print(f"Szenario: Narrow ZOPA")
    print(f"  Retailer max: €{session.retailer_limits.max_price}")
    print(f"  Supplier min: €{session.supplier_limits.min_price}")
    print(f"  ZOPA: €{session.supplier_limits.min_price}-{session.retailer_limits.max_price}")
    print(f"\nErwartung: Erfolgreiche Einigung OHNE Walk-Away\n")
    
    start_time = time.time()
    
    try:
        session = orch.start_negotiation(session)
        
        for i in range(60):
            if session.status not in (SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING):
                break
            session = orch.run_negotiation_round(session)
            if session.rounds:
                last = session.rounds[-1]
                print(f"  Runde {session.current_round:2d} | {last.role.value:8s} | "
                      f"€{last.offer.unit_price:7.2f}")
        
        duration = time.time() - start_time
        
        print("\n" + "="*80)
        print("ERGEBNIS")
        print("="*80)
        print(f"Status: {session.status.value}")
        print(f"Runden: {session.current_round}")
        print(f"Dauer: {duration:.1f}s")
        
        final_offer = session.rounds[-1].offer if session.rounds else None
        if final_offer:
            print(f"\nFinales Angebot:")
            print(f"  Preis: €{final_offer.unit_price:.2f}")
            print(f"  Menge: {final_offer.volume}")
            
            # Prüfe Position im ZOPA
            zopa_size = session.retailer_limits.max_price - session.supplier_limits.min_price
            position = (final_offer.unit_price - session.supplier_limits.min_price) / zopa_size
            print(f"  Position im ZOPA: {position:.0%}")
        
        if session.status in (SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL):
            print(f"\n✅ SUCCESS: Narrow ZOPA erfolgreich verhandelt")
        else:
            print(f"\n⚠️  UNEXPECTED: Status={session.status.value}")
        
        print("\n" + "="*80)
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        print(f"\n❌ ERROR: {e}")


if __name__ == "__main__":
    print("\n" + "█"*80)
    print("WALKAWAY STAGNATION DETECTION TESTS")
    print("█"*80)
    
    # Initialize orchestrator with LLM
    llm = AICoreClient(model_name="gpt-4o", temperature=0.3, max_tokens=1024)
    orchestrator = SimpleOrchestrator(llm_client=llm)
    
    # Test 1: No ZOPA → Autonomer Walk-Away
    test_no_zopa_autonomous_walkaway(orchestrator)
    
    # print("\n\n")
    
    # # Test 2: Narrow ZOPA → Erfolgreiche Verhandlung (kein Walk-Away) - DEAKTIVIERT
    # test_narrow_zopa_without_walkaway(orchestrator)
    
    print("\n" + "█"*80)
    print("TESTS ABGESCHLOSSEN")
    print("█"*80 + "\n")
