"""
test_evaluation_scenarios.py
─────────────────────────────
End-to-End Evaluationstest-Suite für die 14 Bachelorarbeit-Szenarien.

Verwendet die ECHTE AICoreClient (SAP AI Core / GPT-4o) — kein Mocking.
Alle Szenarien werden aus data/evaluation_scenarios.json geladen.

KPIs pro Szenario:
    CSR  — Constraint Satisfaction Rate (Supplier + Retailer separat)
    WAA  — Walk-Away Accuracy (aggregiert über alle 14 Szenarien)
    ZU   — ZOPA Utilization (nur bei DEAL-Szenarien)

Ergebnis-Export:
    evaluation/results/eval_{timestamp}.json  (automatisch nach jedem Lauf)

Run:
    python3 test_evaluation_scenarios.py

Run einzelnes Szenario:
    python3 test_evaluation_scenarios.py S03
"""

import argparse
import json
import logging
import os
import statistics as _stats
import sys
import time

# Ensure project root is on sys.path when running from tests/
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ── Load .env ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

# Logging
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
from evaluation.kpi_calculator import (
    calculate_csr,
    calculate_waa,
    calculate_zu,
    generate_evaluation_report,
    get_constraint_violations,
    get_zopa_position,
)

logger = logging.getLogger("eval_scenarios")

# ── Pfade ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_SCENARIOS_FILE = _ROOT / "data" / "evaluation_scenarios.json"
_RESULTS_DIR = _ROOT / "evaluation" / "results"


# ══════════════════════════════════════════════════════════════════════════════
# DATENSTRUKTUREN
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EvalScenarioResult:
    scenario_id: str
    name: str
    category: str
    primary_kpi: str
    expected_outcome: str
    passed: bool = False
    actual_status: str = ""
    rounds_used: int = 0
    final_price: Optional[float] = None
    final_volume: Optional[int] = None
    final_delivery_days: Optional[int] = None
    final_payment_terms: Optional[str] = None
    csr_supplier: float = 0.0
    csr_retailer: float = 0.0
    zu: Optional[float] = None
    zopa_position: str = "n/a"
    constraint_violations: List[dict] = field(default_factory=list)
    assertions: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    notes: str = ""
    session: Optional[object] = field(default=None)  # NegotiationSession — für Round-Export


def _assert(r: EvalScenarioResult, condition: bool, msg_pass: str, msg_fail: str):
    if condition:
        r.assertions.append(f"✓ {msg_pass}")
    else:
        r.assertions.append(f"✗ {msg_fail}")
        r.failures.append(msg_fail)


# ══════════════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def _load_scenarios() -> list[dict]:
    """Lädt alle 14 Szenarien aus der JSON-Definitionsdatei."""
    with open(_SCENARIOS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _scenario_to_session(sc: dict) -> NegotiationSession:
    """Erstellt eine NegotiationSession aus einer Szenario-Definition."""
    sup = sc["supplier_limits"]
    ret = sc["retailer_limits"]
    ini = sc["initial_offer"]

    supplier_limits = PartyLimits(
        min_price=sup.get("min_price"),
        max_price=sup.get("max_price"),
        min_volume=sup.get("min_volume"),
        max_volume=sup.get("max_volume"),
        max_delivery_days=sup.get("max_delivery_days"),
        acceptable_payment_terms=sup.get("acceptable_payment_terms", []),
    )

    retailer_limits = PartyLimits(
        max_price=ret.get("max_price"),
        min_volume=ret.get("min_volume"),
        max_volume=ret.get("max_volume"),
        max_delivery_days=ret.get("max_delivery_days"),
        acceptable_payment_terms=ret.get("acceptable_payment_terms", []),
        target_margin=ret.get("target_margin"),
        retail_price=ret.get("retail_price"),
    )

    initial_offer = NegotiationOffer(
        unit_price=ini["unit_price"],
        volume=ini["volume"],
        delivery_days=ini["delivery_days"],
        payment_terms=ini["payment_terms"],
    )

    return NegotiationSession(
        session_id=f"{sc['scenario_id'].lower()}-{str(uuid.uuid4())[:6]}",
        product_id=sc["product_id"],
        product_name=sc["product_name"],
        initiator=AgentRole.SUPPLIER,
        supplier_id="eval-supplier",
        retailer_id="eval-retailer",
        initial_offer=initial_offer,
        supplier_limits=supplier_limits,
        retailer_limits=retailer_limits,
        max_rounds=15,
    )


def _run_full_negotiation(
    session: NegotiationSession,
    orchestrator: SimpleOrchestrator,
    max_loop_guard: int = 60,
    verbose: bool = True,
) -> NegotiationSession:
    """Führt die Verhandlung bis zum Terminalstatus durch."""
    session = orchestrator.start_negotiation(session)

    if session.status not in (SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING):
        if verbose:
            logger.info(f"  → Pre-start terminal: {session.status.value}")
        return session

    for _ in range(max_loop_guard):
        session = orchestrator.run_negotiation_round(session)

        if verbose and session.rounds:
            last = session.rounds[-1]
            logger.info(
                f"  Round {session.current_round:2d} | {last.role.value:8s} | "
                f"€{last.offer.unit_price:8.2f} | {last.offer.volume:5d} units | "
                f"{last.offer.delivery_days:2d}d | {last.offer.payment_terms:6s} | "
                f"valid={last.is_valid} | {session.status.value}"
            )

        if session.status not in (SessionStatus.NEGOTIATING, SessionStatus.RENEGOTIATING):
            break

    return session


# ══════════════════════════════════════════════════════════════════════════════
# ASSERTIONS-HELFER
# ══════════════════════════════════════════════════════════════════════════════

_DEAL_STATUSES = {SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL}
_ABORT_STATUSES = {SessionStatus.FAILED, SessionStatus.MAX_ROUNDS, SessionStatus.REJECTED}
_TERMINAL_STATUSES = _DEAL_STATUSES | _ABORT_STATUSES


def _check_csr(r: EvalScenarioResult, session: NegotiationSession):
    """Prüft Constraint Satisfaction Rate für beide Rollen."""
    r.csr_supplier = calculate_csr(session, AgentRole.SUPPLIER)
    r.csr_retailer = calculate_csr(session, AgentRole.RETAILER)
    r.constraint_violations = (
        get_constraint_violations(session, AgentRole.SUPPLIER)
        + get_constraint_violations(session, AgentRole.RETAILER)
    )

    _assert(r, r.csr_supplier == 1.0,
            f"Supplier CSR=1.0 (alle {len([x for x in session.rounds if x.role == AgentRole.SUPPLIER])} Angebote konform)",
            f"Supplier CSR={r.csr_supplier:.2f} — {len([v for v in r.constraint_violations if v['role'] == 'supplier'])} Verletzungen")
    _assert(r, r.csr_retailer == 1.0,
            f"Retailer CSR=1.0 (alle {len([x for x in session.rounds if x.role == AgentRole.RETAILER])} Angebote konform)",
            f"Retailer CSR={r.csr_retailer:.2f} — {len([v for v in r.constraint_violations if v['role'] == 'retailer'])} Verletzungen")


def _check_waa_deal_scenario(r: EvalScenarioResult, session: NegotiationSession):
    """WAA-Check für Szenarien mit ZOPA: kein frühzeitiger Abbruch."""
    _assert(r, session.status not in _ABORT_STATUSES or session.current_round >= 5,
            "Kein frühzeitiger Walk-Away bei ZOPA-Szenario",
            f"Frühzeitiger Abbruch in Runde {session.current_round} trotz ZOPA")


def _check_waa_no_zopa_scenario(r: EvalScenarioResult, session: NegotiationSession):
    """WAA-Check für No-ZOPA-Szenarien: kein False Agreement erlaubt."""
    _assert(r, session.status not in _DEAL_STATUSES,
            f"Korrekt abgebrochen: {session.status.value} (kein False Agreement)",
            f"FALSE AGREEMENT bei No-ZOPA-Szenario! Status={session.status.value}, "
            f"Preis=€{session.rounds[-1].offer.unit_price:.2f}" if session.rounds else "FALSE AGREEMENT!")


def _check_zu(
    r: EvalScenarioResult,
    session: NegotiationSession,
    supplier_min: float,
    retailer_max: float,
):
    """Berechnet und loggt ZOPA Utilization."""
    zu = calculate_zu(session, supplier_min, retailer_max)
    r.zu = zu
    r.zopa_position = get_zopa_position(zu)

    if zu is not None:
        logger.info(f"  ZU={zu:.3f} ({r.zopa_position}) | "
                    f"ZOPA=[{supplier_min}–{retailer_max}] | "
                    f"Deal=€{session.rounds[-1].offer.unit_price:.2f}")
        _assert(r, 0.0 <= zu <= 1.0,
                f"ZU={zu:.3f} liegt im gültigen Bereich [0, 1]",
                f"ZU={zu:.3f} außerhalb [0, 1] — Preis außerhalb ZOPA")


def _check_price_in_zopa(
    r: EvalScenarioResult,
    session: NegotiationSession,
    supplier_min: float,
    retailer_max: float,
):
    """Prüft ob der Final-Preis innerhalb des ZOPA liegt."""
    if not session.rounds:
        return
    final_price = session.rounds[-1].offer.unit_price
    if session.status in _DEAL_STATUSES:
        _assert(r, supplier_min <= final_price <= retailer_max,
                f"Final-Preis €{final_price:.2f} liegt im ZOPA [€{supplier_min}–€{retailer_max}]",
                f"Final-Preis €{final_price:.2f} verletzt ZOPA [€{supplier_min}–€{retailer_max}]")


# ══════════════════════════════════════════════════════════════════════════════
# SZENARIO-RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def run_scenario(sc: dict, orch: SimpleOrchestrator) -> EvalScenarioResult:
    """Führt ein einzelnes Evaluationsszenario aus und gibt das Ergebnis zurück."""
    sid = sc["scenario_id"]
    category = sc["category"]
    expected = sc["expected_outcome"]

    r = EvalScenarioResult(
        scenario_id=sid,
        name=sc["name"],
        category=category,
        primary_kpi=sc["primary_kpi"],
        expected_outcome=expected,
        notes=sc.get("notes", ""),
    )

    logger.info(f"\n{'='*70}")
    logger.info(f"[{sid}] {sc['name']}")
    logger.info(f"  Kategorie: {category} | Primär-KPI: {sc['primary_kpi']} | Erwartet: {expected}")
    if sc.get("zopa_exists"):
        logger.info(f"  ZOPA: [{sc['supplier_limits']['min_price']}–{sc['retailer_limits']['max_price']}] "
                    f"= {sc.get('zopa_width_eur', '?')} EUR ({sc.get('zopa_width_pct', 0)*100:.1f}%)")
    else:
        logger.info(f"  No ZOPA | Lücke: {sc.get('gap_eur', '?')} EUR")

    t0 = time.time()
    try:
        session = _scenario_to_session(sc)
        session = _run_full_negotiation(session, orch)

        r.elapsed_sec = time.time() - t0
        r.actual_status = session.status.value
        r.rounds_used = session.current_round
        r.session = session  # für Round-Export in export_results()

        if session.rounds:
            last_offer = session.rounds[-1].offer
            r.final_price = last_offer.unit_price
            r.final_volume = last_offer.volume
            r.final_delivery_days = last_offer.delivery_days
            r.final_payment_terms = last_offer.payment_terms

        # ── Terminierung ──────────────────────────────────────────────────────
        _assert(r, session.status in _TERMINAL_STATUSES,
                f"Terminalstatus erreicht: {session.status.value}",
                f"Nicht-terminaler Status: {session.status.value}")

        # ── Rundenanzahl ──────────────────────────────────────────────────────
        _assert(r, session.current_round >= 1,
                f"≥ 1 Runde gespielt ({session.current_round})",
                "Keine Runden gespielt")
        _assert(r, session.current_round <= 15,
                f"{session.current_round} Runden ≤ max_rounds=15",
                f"Runden ({session.current_round}) übersteigen max_rounds=15")

        # ── Kein unkontrollierter Fehler ──────────────────────────────────────
        if session.status == SessionStatus.FAILED:
            # FAILED ist bei No-ZOPA OK, sonst eine Warnung
            if sc.get("zopa_exists", True):
                _assert(r, False,
                        "",
                        f"Unerwarteter FAILED-Status: {session.status_message}")

        # ── CSR ───────────────────────────────────────────────────────────────
        _check_csr(r, session)

        # ── WAA / Kategorie-spezifische Assertions ────────────────────────────
        if category in ("NO_ZOPA_OBVIOUS", "NO_ZOPA_NEAR_MISS"):
            _check_waa_no_zopa_scenario(r, session)

        elif category in ("WIDE_ZOPA", "NARROW_ZOPA", "VOLUME_LEVERAGE"):
            _check_waa_deal_scenario(r, session)
            supplier_min = sc["supplier_limits"]["min_price"]
            retailer_max = sc["retailer_limits"]["max_price"]
            _check_zu(r, session, supplier_min, retailer_max)
            _check_price_in_zopa(r, session, supplier_min, retailer_max)

        elif category == "ASYMMETRIC":
            # Preis-ZOPA existiert, aber andere Constraints könnten einen Deal verhindern
            supplier_min = sc["supplier_limits"]["min_price"]
            retailer_max = sc["retailer_limits"]["max_price"]
            _check_zu(r, session, supplier_min, retailer_max)
            # Beides ist valid: Deal oder Abort
            logger.info(f"  Asymmetrisches Szenario: Outcome={session.status.value} "
                        f"(DEAL und ABORT beide akzeptiert)")

        # ── Passed-Flag ───────────────────────────────────────────────────────
        r.passed = len(r.failures) == 0

        # ── Zusammenfassung ───────────────────────────────────────────────────
        verdict = "PASS ✓" if r.passed else f"FAIL ✗ ({len(r.failures)} Fehler)"
        logger.info(f"  → [{sid}] {verdict} | Status={r.actual_status} | "
                    f"Runden={r.rounds_used} | "
                    f"Preis=€{r.final_price:.2f}" if r.final_price else
                    f"  → [{sid}] {verdict} | Status={r.actual_status} | Runden={r.rounds_used}")
        if r.failures:
            for fail in r.failures:
                logger.warning(f"    ✗ {fail}")
        if r.constraint_violations:
            for v in r.constraint_violations:
                logger.warning(f"    Verletzung Runde {v['round_number']} ({v['role']}): {v['message']}")

    except Exception as exc:
        r.elapsed_sec = time.time() - t0
        r.actual_status = "ERROR"
        r.failures.append(f"Exception: {type(exc).__name__}: {exc}")
        r.passed = False
        logger.error(f"  [{sid}] FEHLER: {exc}", exc_info=True)

    return r


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def export_results(results: List[EvalScenarioResult], model: str = "gpt-4o"):
    """Exportiert alle Ergebnisse als JSON in evaluation/results/."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Konvertiere EvalScenarioResult → dict für den Report-Generator
    raw_dicts = []
    for r in results:
        d = {
            "scenario_id": r.scenario_id,
            "name": r.name,
            "category": r.category,
            "primary_kpi": r.primary_kpi,
            "expected_outcome": r.expected_outcome,
            "actual_status": r.actual_status,
            "rounds_used": r.rounds_used,
            "final_price": r.final_price,
            "csr_supplier": r.csr_supplier,
            "csr_retailer": r.csr_retailer,
            "zu": r.zu,
            "constraint_violations": r.constraint_violations,
            "elapsed_sec": r.elapsed_sec,
        }
        # Vollständiger Verhandlungsverlauf — nur wenn session mitgeführt wurde
        if r.session is not None and r.session.rounds:
            d["rounds"] = [
                {
                    "round_number": rnd.round_number,
                    "role": rnd.role.value,
                    "offer": {
                        "unit_price": rnd.offer.unit_price,
                        "volume": rnd.offer.volume,
                        "delivery_days": rnd.offer.delivery_days,
                        "payment_terms": rnd.offer.payment_terms,
                        "justification": rnd.offer.justification,
                        "leverage_used": rnd.offer.leverage_used,
                    },
                    "raw_offer": {
                        "unit_price": rnd.raw_offer.unit_price,
                        "volume": rnd.raw_offer.volume,
                        "delivery_days": rnd.raw_offer.delivery_days,
                        "payment_terms": rnd.raw_offer.payment_terms,
                    } if rnd.raw_offer is not None else None,
                    "is_valid": rnd.is_valid,
                    "validation_message": rnd.validation_message,
                    "retry_count": rnd.retry_count,
                    "agent_reasoning": rnd.agent_reasoning,  # dict oder None
                    "timestamp": rnd.timestamp,
                }
                for rnd in r.session.rounds
            ]
        raw_dicts.append(d)

    report = generate_evaluation_report(raw_dicts, model=model, temperature=0.0)

    # Assertions + failures hinzufügen (nicht im KPI-Calculator)
    for i, sc_report in enumerate(report["scenarios"]):
        if i < len(results):
            sc_report["assertions"] = results[i].assertions
            sc_report["failures"] = results[i].failures
            sc_report["passed"] = results[i].passed
            sc_report["final_volume"] = results[i].final_volume
            sc_report["final_delivery_days"] = results[i].final_delivery_days
            sc_report["final_payment_terms"] = results[i].final_payment_terms

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_path = _RESULTS_DIR / f"eval_{timestamp}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"\n📄 Ergebnis-Export: {output_path}")
    return output_path, report


# ══════════════════════════════════════════════════════════════════════════════
# ZUSAMMENFASSUNGS-REPORT
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(results: List[EvalScenarioResult], report: dict):
    """Druckt eine kompakte Zusammenfassung aller Szenarien und aggregierter KPIs."""
    kpis = report["aggregate_kpis"]
    waa = report["waa_confusion_matrix"]

    print("\n" + "═" * 70)
    print("EVALUATIONS-ZUSAMMENFASSUNG — TradeBridge 2.0")
    print("═" * 70)

    # Tabelle
    header = f"{'ID':>4}  {'Kategorie':<22} {'Status':<22} {'Rd':>3} {'Preis':>9} {'CSR-S':>6} {'CSR-R':>6} {'ZU':>6}  {'OK'}"
    print(header)
    print("-" * len(header))

    for r in results:
        ok = "✓" if r.passed else "✗"
        preis_str = f"€{r.final_price:.2f}" if r.final_price else "    —"
        zu_str = f"{r.zu:.3f}" if r.zu is not None else "  —  "
        csr_s = f"{r.csr_supplier:.2f}"
        csr_r = f"{r.csr_retailer:.2f}"
        print(f"{r.scenario_id:>4}  {r.category:<22} {r.actual_status:<22} "
              f"{r.rounds_used:>3} {preis_str:>9} {csr_s:>6} {csr_r:>6} {zu_str:>6}  {ok}")

    print("\n" + "─" * 70)
    print("AGGREGIERTE KPIs:")
    print(f"  CSR overall:          {kpis['csr_overall']:.4f}")
    print(f"  WAA Precision:        {kpis['waa_precision']:.4f}  (TP={waa['TP']}, FP={waa['FP']})")
    print(f"  WAA Recall:           {kpis['waa_recall']:.4f}  (TP={waa['TP']}, FN={waa['FN']})")
    print(f"  WAA F1:               {kpis['waa_f1']:.4f}")
    print(f"  False Agreement Rate: {kpis['false_agreement_rate']:.4f}  ← FP/(FP+TN): Deal trotz No-ZOPA, muss 0.0 sein")
    print(f"  Missed Walkaway Rate: {kpis['missed_walkaway_rate']:.4f}  ← FN/(FN+TP): Walk-Away verpasst")
    print(f"  ZU Mean:              {kpis['zu_mean']:.4f}" if kpis['zu_mean'] is not None else "  ZU Mean:              n/a")
    print(f"  ZU Median:            {kpis['zu_median']:.4f}" if kpis['zu_median'] is not None else "  ZU Median:            n/a")
    print(f"  Ø Runden:             {kpis['avg_rounds']:.2f}")
    print(f"  Agreement Rate:       {kpis['agreement_rate']:.4f}  ({kpis['deals_reached']}/{len(results)} Deals)")
    print(f"  Outcome Accuracy:     {kpis['outcome_accuracy']:.4f}")

    passed = sum(1 for r in results if r.passed)
    print(f"\n  Tests bestanden: {passed}/{len(results)}")
    if passed < len(results):
        failed_ids = [r.scenario_id for r in results if not r.passed]
        print(f"  Fehlgeschlagen:  {', '.join(failed_ids)}")
    print("═" * 70)


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-RUN AGGREGATION
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_runs(run_reports: list[dict]) -> dict:
    """Berechnet Cross-Run-Statistiken über alle N Run-Reports."""
    n = len(run_reports)

    def stat(values):
        values = [v for v in values if v is not None]
        if not values:
            return {"mean": None, "std": None, "min": None, "max": None}
        return {
            "mean": round(_stats.mean(values), 4),
            "std": round(_stats.stdev(values), 4) if len(values) > 1 else 0.0,
            "min": round(min(values), 4),
            "max": round(max(values), 4),
        }

    csr_values    = [r["aggregate_kpis"]["csr_overall"]          for r in run_reports]
    waa_f1_values = [r["aggregate_kpis"]["waa_f1"]               for r in run_reports]
    zu_values     = [r["aggregate_kpis"]["zu_mean"]              for r in run_reports]
    acc_values    = [r["aggregate_kpis"]["outcome_accuracy"]     for r in run_reports]
    far_values    = [r["aggregate_kpis"]["false_agreement_rate"] for r in run_reports]
    mwr_values    = [r["aggregate_kpis"]["missed_walkaway_rate"] for r in run_reports]

    # Szenario-Stabilität: wie oft war outcome_correct pro Szenario
    scenario_stability: dict = {}
    for report in run_reports:
        for sc in report["scenarios"]:
            sid = sc["scenario_id"]
            if sid not in scenario_stability:
                scenario_stability[sid] = {"n_correct": 0, "n_runs": 0, "outcomes": []}
            scenario_stability[sid]["n_runs"] += 1
            if sc.get("outcome_correct"):
                scenario_stability[sid]["n_correct"] += 1
            scenario_stability[sid]["outcomes"].append(sc.get("actual_status", ""))

    for sid, s in scenario_stability.items():
        s["stable"] = s["n_correct"] == s["n_runs"]
        s["stability_rate"] = round(s["n_correct"] / s["n_runs"], 4) if s["n_runs"] > 0 else 0.0

    return {
        "n_runs": n,
        "cross_run_statistics": {
            "csr_overall":          stat(csr_values),
            "waa_f1":               stat(waa_f1_values),
            "zu_mean":              stat(zu_values),
            "outcome_accuracy":     stat(acc_values),
            "false_agreement_rate": stat(far_values),
            "missed_walkaway_rate": stat(mwr_values),
        },
        "scenario_stability": scenario_stability,
    }


def export_multirun_results(multirun: dict, run_paths: list) -> Path:
    """Exportiert Multi-Run-Aggregat als JSON."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_path = _RESULTS_DIR / f"eval_multirun_{timestamp}.json"

    export = {
        "experiment_type": "multi_run",
        "timestamp_utc": timestamp,
        "n_runs": multirun["n_runs"],
        "run_files": [str(p) for p in run_paths],
        "cross_run_statistics": multirun["cross_run_statistics"],
        "scenario_stability": multirun["scenario_stability"],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, ensure_ascii=False)

    logger.info(f"\n📊 Multi-Run-Report: {output_path}")
    return output_path


def print_multirun_summary(multirun: dict):
    """Konsolenausgabe für Cross-Run-Statistiken."""
    stats = multirun["cross_run_statistics"]
    stability = multirun["scenario_stability"]
    n = multirun["n_runs"]

    print("\n" + "═" * 70)
    print(f"MULTI-RUN ZUSAMMENFASSUNG — {n} Läufe")
    print("═" * 70)

    def fmt(s):
        if s["mean"] is None:
            return "n/a"
        if s["std"] == 0.0:
            return f"{s['mean']:.4f} (deterministisch)"
        return f"{s['mean']:.4f} ± {s['std']:.4f}  [min={s['min']:.4f}, max={s['max']:.4f}]"

    print(f"  CSR overall:          {fmt(stats['csr_overall'])}")
    print(f"  WAA F1:               {fmt(stats['waa_f1'])}")
    print(f"  ZU Mean:              {fmt(stats['zu_mean'])}")
    print(f"  Outcome Accuracy:     {fmt(stats['outcome_accuracy'])}")
    print(f"  False Agreement Rate: {fmt(stats['false_agreement_rate'])}  ← FP/(FP+TN)")
    print(f"  Missed Walkaway Rate: {fmt(stats['missed_walkaway_rate'])}  ← FN/(FN+TP)")

    print(f"\n  Szenario-Stabilität ({n} Runs):")
    unstable = [(sid, s) for sid, s in stability.items() if not s["stable"]]
    if not unstable:
        print(f"  ✓ Alle Szenarien in allen {n} Runs korrekt")
    else:
        for sid, s in sorted(unstable):
            print(f"  ✗ {sid}: {s['n_correct']}/{s['n_runs']} korrekt | Outcomes: {s['outcomes']}")

    print("═" * 70)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

# Argument-Parser auf Modul-Ebene (so kann main() ihn direkt nutzen)
_parser = argparse.ArgumentParser(description="TradeBridge 2.0 Evaluations-Suite")
_parser.add_argument("--runs", type=int, default=1, metavar="N",
                     help="Anzahl Wiederholungsläufe (default=1)")
_parser.add_argument("scenarios", nargs="*",
                     help="Optionale Szenario-IDs, z.B. S01 S07 (default: alle)")


def main():
    args = _parser.parse_args()
    n_runs = args.runs
    filter_ids: set[str] = {s.upper() for s in args.scenarios}

    if filter_ids:
        logger.info(f"Nur Szenarien: {sorted(filter_ids)}")

    # LLM-Client (temperature=0.0 für Reproduzierbarkeit)
    llm_client = AICoreClient(temperature=0.0)
    orch = SimpleOrchestrator(llm_client=llm_client)

    # Szenarien laden
    all_scenarios = _load_scenarios()
    if filter_ids:
        all_scenarios = [sc for sc in all_scenarios if sc["scenario_id"] in filter_ids]
    if not all_scenarios:
        logger.error("Keine Szenarien gefunden.")
        sys.exit(1)

    logger.info(f"\nStarte {n_runs} Run(s) mit {len(all_scenarios)} Szenario(en)...")
    logger.info(f"Modell: gpt-4o | Temperatur: 0.0 | max_rounds: 15\n")

    run_reports: list[dict] = []
    run_paths: list[Path] = []
    last_run_results: list[EvalScenarioResult] = []
    total_start = time.time()

    for run_idx in range(1, n_runs + 1):
        if n_runs > 1:
            logger.info(f"\n{'█' * 70}")
            logger.info(f"  RUN {run_idx} / {n_runs}")
            logger.info(f"{'█' * 70}")

        run_start = time.time()
        run_results: list[EvalScenarioResult] = []
        for sc in all_scenarios:
            result = run_scenario(sc, orch)
            run_results.append(result)

        run_elapsed = time.time() - run_start
        run_min, run_sec = divmod(int(run_elapsed), 60)
        if n_runs > 1:
            logger.info(f"  RUN {run_idx} abgeschlossen in {run_min}m {run_sec:02d}s")

        path, report = export_results(run_results)
        print_summary(run_results, report)
        if n_runs == 1:
            total_elapsed = time.time() - total_start
            t_min, t_sec = divmod(int(total_elapsed), 60)
            print(f"  Gesamtlaufzeit: {t_min}m {t_sec:02d}s")
        run_reports.append(report)
        run_paths.append(path)
        last_run_results = run_results

    # Multi-Run-Aggregation (nur bei mehr als 1 Run)
    if n_runs > 1:
        multirun = aggregate_runs(run_reports)
        export_multirun_results(multirun, run_paths)
        print_multirun_summary(multirun)
        total_elapsed = time.time() - total_start
        t_min, t_sec = divmod(int(total_elapsed), 60)
        print(f"\n  Gesamtlaufzeit alle {n_runs} Runs: {t_min}m {t_sec:02d}s  "
              f"(Ø {t_min // n_runs}m {(t_sec + (t_min % n_runs) * 60) // n_runs:02d}s pro Run)")

    # Exit-Code basiert auf letztem Run
    all_passed = all(r.passed for r in last_run_results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
