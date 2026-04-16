"""
evaluation/kpi_calculator.py
────────────────────────────
KPI-Berechnungsmodul für die 14 Evaluationsszenarien der Bachelorarbeit.

KPIs (aus Business Understanding):
    CSR  — Constraint Satisfaction Rate: Anteil constraint-konformer Angebote
    WAA  — Walk-Away Accuracy: Precision/Recall der No-ZOPA-Erkennung
    ZU   — ZOPA Utilization: Position des Abschlusses im Einigungskorridor
    BP   — Business Plausibility: Human-as-a-Judge (nicht automatisiert)

Formeln:
    CSR  = valid_offers / total_offers  (pro Szenario, pro Rolle)
    ZU   = (retailer_max - agreed_price) / (retailer_max - supplier_min)
    WAA: TP=korrekt abgebrochen, TN=korrekt weiterverhandelt,
         FP=fälschlich abgebrochen, FN=False Agreement (schlimmster Fehler)
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Optional

from models.negotiation_models import AgentRole, NegotiationSession, SessionStatus
from models.constraints import (
    validate_offer_against_supplier_limits,
    validate_offer_against_retailer_limits,
)


# ---------------------------------------------------------------------------
# CSR — Constraint Satisfaction Rate
# ---------------------------------------------------------------------------

def calculate_csr(
    session: NegotiationSession,
    role: AgentRole,
) -> float:
    """
    Berechnet die Constraint Satisfaction Rate für eine Rolle in einer Session.

    Verwendet `raw_offer` (unkorrigierter LLM-Output vor Clamping) falls vorhanden,
    sonst `offer` (geclamptes Angebot). Dadurch misst CSR die tatsächliche Constraint-
    Treue des Agenten, nicht die des Orchestrators.

    Gibt 1.0 zurück, wenn es keine Angebote dieser Rolle gibt (kein Verstoß möglich).

    Args:
        session: Die abgeschlossene Verhandlungssession.
        role:    AgentRole.SUPPLIER oder AgentRole.RETAILER.

    Returns:
        CSR ∈ [0.0, 1.0] — Anteil der constraint-konformen Angebote.
    """
    role_rounds = [r for r in session.rounds if r.role == role]
    if not role_rounds:
        return 1.0

    valid_count = 0
    for rnd in role_rounds:
        # Use raw_offer if available (true LLM output), fall back to offer
        offer = rnd.raw_offer if rnd.raw_offer is not None else rnd.offer
        if role == AgentRole.SUPPLIER:
            limits = session.supplier_limits
            if limits is None:
                valid_count += 1
                continue
            result = validate_offer_against_supplier_limits(
                unit_price=offer.unit_price,
                volume=offer.volume,
                delivery_days=offer.delivery_days,
                payment_terms=offer.payment_terms,
                supplier_min_price=limits.min_price,
                supplier_min_volume=limits.min_volume,
                supplier_max_volume=limits.max_volume,
                supplier_acceptable_payment_terms=limits.acceptable_payment_terms or [],
            )
        else:
            limits = session.retailer_limits
            if limits is None:
                valid_count += 1
                continue
            result = validate_offer_against_retailer_limits(
                unit_price=offer.unit_price,
                volume=offer.volume,
                delivery_days=offer.delivery_days,
                payment_terms=offer.payment_terms,
                retailer_max_price=limits.max_price,
                retailer_min_volume=limits.min_volume,
                retailer_max_volume=limits.max_volume,
                retailer_max_delivery_days=limits.max_delivery_days,
                retailer_acceptable_payment_terms=limits.acceptable_payment_terms or [],
                retailer_target_margin=limits.target_margin,
                retailer_retail_price=limits.retail_price,
            )

        if result.is_valid:
            valid_count += 1

    return valid_count / len(role_rounds)


def get_constraint_violations(
    session: NegotiationSession,
    role: AgentRole,
) -> list[dict]:
    """
    Gibt eine Liste aller Constraint-Verletzungen für eine Rolle zurück.

    Returns:
        List von Dicts mit round_number, field, violation_type, message.
    """
    violations: list[dict] = []
    role_rounds = [r for r in session.rounds if r.role == role]

    for rnd in role_rounds:
        # Use raw_offer if available (true LLM output before clamping)
        offer = rnd.raw_offer if rnd.raw_offer is not None else rnd.offer
        if role == AgentRole.SUPPLIER:
            limits = session.supplier_limits
            if limits is None:
                continue
            result = validate_offer_against_supplier_limits(
                unit_price=offer.unit_price,
                volume=offer.volume,
                delivery_days=offer.delivery_days,
                payment_terms=offer.payment_terms,
                supplier_min_price=limits.min_price,
                supplier_min_volume=limits.min_volume,
                supplier_max_volume=limits.max_volume,
                supplier_acceptable_payment_terms=limits.acceptable_payment_terms or [],
            )
        else:
            limits = session.retailer_limits
            if limits is None:
                continue
            result = validate_offer_against_retailer_limits(
                unit_price=offer.unit_price,
                volume=offer.volume,
                delivery_days=offer.delivery_days,
                payment_terms=offer.payment_terms,
                retailer_max_price=limits.max_price,
                retailer_min_volume=limits.min_volume,
                retailer_max_volume=limits.max_volume,
                retailer_max_delivery_days=limits.max_delivery_days,
                retailer_acceptable_payment_terms=limits.acceptable_payment_terms or [],
                retailer_target_margin=limits.target_margin,
                retailer_retail_price=limits.retail_price,
            )

        for v in result.violations:
            violations.append({
                "round_number": rnd.round_number,
                "role": role.value,
                "field": v.field,
                "violation_type": v.violation_type,
                "message": v.message,
                "current_value": v.current_value,
                "limit_value": v.limit_value,
            })

    return violations


# ---------------------------------------------------------------------------
# ZU — ZOPA Utilization
# ---------------------------------------------------------------------------

def calculate_zu(
    session: NegotiationSession,
    supplier_min: float,
    retailer_max: float,
) -> Optional[float]:
    """
    Berechnet die ZOPA Utilization des finalen Deals.

    ZU = (retailer_max - agreed_price) / (retailer_max - supplier_min)

    Interpretation:
        0.0  — Deal genau am retailer_max (schlecht für Retailer, gut für Supplier)
        0.5  — Deal in der Mitte (symmetrisch)
        1.0  — Deal genau am supplier_min (optimal für Retailer, schlecht für Supplier)

    Nur berechenbar wenn:
        - Session mit ACCEPTED oder PENDING_APPROVAL abgeschlossen hat
        - retailer_max > supplier_min (ZOPA existiert)
        - Mindestens eine Runde gespielt wurde

    Returns:
        ZU ∈ [0.0, 1.0] oder None falls nicht berechenbar.
    """
    zopa_width = retailer_max - supplier_min
    if zopa_width <= 0:
        return None

    agreed_price = _get_agreed_price(session)
    if agreed_price is None:
        return None

    zu = (retailer_max - agreed_price) / zopa_width
    # Klemmen auf [0, 1] für numerische Stabilität
    return max(0.0, min(1.0, zu))


def _get_agreed_price(session: NegotiationSession) -> Optional[float]:
    """
    Ermittelt den finalen verhandelten Preis aus der Session.

    Sucht nach dem letzten Angebot, das zur Einigung geführt hat:
    - Bei ACCEPTED: letztes Angebot (das akzeptierte)
    - Bei PENDING_APPROVAL: letztes Angebot (Konvergenz-Preis)
    - Sonst: None
    """
    terminal = {SessionStatus.ACCEPTED, SessionStatus.PENDING_APPROVAL}
    if session.status not in terminal:
        return None

    if not session.rounds:
        return None

    return session.rounds[-1].offer.unit_price


def get_zopa_position(zu: Optional[float]) -> str:
    """
    Kategorisiert die ZU-Position für den Report.

    Returns:
        'supplier_favored' (ZU < 0.33)
        'balanced' (0.33 ≤ ZU ≤ 0.67)
        'retailer_favored' (ZU > 0.67)
        'n/a' falls ZU nicht berechnet
    """
    if zu is None:
        return "n/a"
    if zu < 0.33:
        return "supplier_favored"
    if zu <= 0.67:
        return "balanced"
    return "retailer_favored"


# ---------------------------------------------------------------------------
# WAA — Walk-Away Accuracy
# ---------------------------------------------------------------------------

# Kategorien der 14 Szenarien für WAA-Klassifikation.
#
# S11/S12 sind ASYMMETRIC (Preis-ZOPA vorhanden, aber Nicht-Preis-Konflikt).
# expected_outcome="TRADE_OFF_OR_ABORT" — sowohl Deal als auch Abbruch sind
# methodisch korrekt. Diese Szenarien werden aus der WAA-Berechnung
# ausgeklammert, da ihre binäre Klassifikation (ZOPA/No-ZOPA) nicht
# eindeutig ist. Sie werden im Report als "EXCLUDED" markiert.
_NO_ZOPA_SCENARIOS = {"S07", "S08", "S09", "S10"}
_ZOPA_SCENARIOS    = {"S01", "S02", "S03", "S04", "S05", "S06", "S13", "S14"}
_AMBIGUOUS_SCENARIOS = {"S11", "S12"}  # TRADE_OFF_OR_ABORT: aus WAA ausgeklammert

# Terminal-Status: als "abgebrochen" gilt alles außer ACCEPTED / PENDING_APPROVAL
_ABORT_STATUSES = {
    SessionStatus.FAILED,
    SessionStatus.MAX_ROUNDS,
    SessionStatus.REJECTED,
}
_DEAL_STATUSES = {
    SessionStatus.ACCEPTED,
    SessionStatus.PENDING_APPROVAL,
}


def calculate_waa(scenario_results: list[dict]) -> dict:
    """
    Berechnet Walk-Away Accuracy (Precision, Recall, F1) über die WAA-relevanten
    Szenarien (S01–S10, S13–S14). S11 und S12 werden methodisch ausgeklammert.

    Klassifikationsmatrix (nur für eindeutige ZOPA / No-ZOPA Szenarien):
        TP — No-ZOPA-Szenario endet korrekt mit FAILED/MAX_ROUNDS
        TN — ZOPA-Szenario endet korrekt mit ACCEPTED/PENDING_APPROVAL
        FP — ZOPA-Szenario endet fälschlich mit FAILED/MAX_ROUNDS (false walk-away)
        FN — No-ZOPA-Szenario endet mit ACCEPTED = FALSE AGREEMENT (kritischster Fehler!)

    EXCLUDED — S11/S12 (ASYMMETRIC, TRADE_OFF_OR_ABORT): Preis-ZOPA vorhanden,
        aber Nicht-Preis-Konflikt macht binäre WAA-Klassifikation methodisch
        unzulässig. Beide Outcomes (Deal und Abbruch) sind korrekt.

    Args:
        scenario_results: Liste von Dicts, jedes mit:
            - 'scenario_id': z.B. "S07"
            - 'actual_status': SessionStatus-Wert als String

    Returns:
        Dict mit precision, recall, f1, false_agreement_rate, und Rohdaten.
    """
    tp = tn = fp = fn = 0
    details: list[dict] = []

    for res in scenario_results:
        sid = res["scenario_id"]
        status_str = res["actual_status"]

        # S11/S12 methodisch ausgeklammert
        if sid in _AMBIGUOUS_SCENARIOS:
            details.append({
                "scenario_id": sid,
                "actual_status": status_str,
                "is_no_zopa": False,
                "classification": "EXCLUDED",
                "exclusion_reason": "ASYMMETRIC: TRADE_OFF_OR_ABORT — beide Outcomes methodisch korrekt",
            })
            continue

        try:
            status = SessionStatus(status_str)
        except ValueError:
            status = None

        is_no_zopa = sid in _NO_ZOPA_SCENARIOS
        is_aborted = status in _ABORT_STATUSES if status else True
        is_deal = status in _DEAL_STATUSES if status else False

        if is_no_zopa:
            if is_aborted:
                cls = "TP"
                tp += 1
            else:
                # Deal bei No-ZOPA = False Agreement (schlimmster Fehler)
                cls = "FN"
                fn += 1
        else:
            if is_deal:
                cls = "TN"
                tn += 1
            else:
                # Kein Deal bei ZOPA-Szenario = unnötig abgebrochen
                cls = "FP"
                fp += 1

        details.append({
            "scenario_id": sid,
            "actual_status": status_str,
            "is_no_zopa": is_no_zopa,
            "classification": cls,
        })

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    false_agreement_rate = fn / (fn + tp) if (fn + tp) > 0 else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_agreement_rate": round(false_agreement_rate, 4),
        "true_positives": tp,
        "true_negatives": tn,
        "false_positives": fp,
        "false_negatives": fn,
        "scenarios_evaluated": tp + tn + fp + fn,
        "scenarios_excluded": len(_AMBIGUOUS_SCENARIOS),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Aggregate Report Generator
# ---------------------------------------------------------------------------

def generate_evaluation_report(
    all_results: list[dict],
    model: str = "gpt-4o",
    temperature: float = 0.0,
) -> dict:
    """
    Aggregiert alle KPIs über die 14 Szenarien und gibt einen strukturierten Report zurück.

    Args:
        all_results: Liste von Dicts, eines pro Szenario. Jedes Dict muss enthalten:
            - scenario_id, category, primary_kpi, expected_outcome
            - actual_status (SessionStatus-Wert als String)
            - rounds_used (int)
            - final_price (Optional[float])
            - csr_supplier (float)
            - csr_retailer (float)
            - zu (Optional[float])
            - constraint_violations (list[dict])
        model: LLM-Modell (für Metadaten)
        temperature: Inferenz-Temperatur (für Metadaten)

    Returns:
        Vollständiges Evaluations-Report-Dict, JSON-serialisierbar.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    experiment_id = f"eval_v1_{timestamp}"

    # ---- Szenario-spezifische Daten ----
    scenarios_out = []
    for res in all_results:
        zu = res.get("zu")
        scenarios_out.append({
            "scenario_id": res["scenario_id"],
            "name": res.get("name", ""),
            "category": res["category"],
            "primary_kpi": res["primary_kpi"],
            "expected_outcome": res["expected_outcome"],
            "actual_status": res["actual_status"],
            "outcome_correct": _outcome_correct(res),
            "rounds_used": res["rounds_used"],
            "final_price": res.get("final_price"),
            "csr_supplier": round(res["csr_supplier"], 4),
            "csr_retailer": round(res["csr_retailer"], 4),
            "csr_combined": round((res["csr_supplier"] + res["csr_retailer"]) / 2, 4),
            "zu": round(zu, 4) if zu is not None else None,
            "zopa_position": get_zopa_position(zu),
            "constraint_violations": res.get("constraint_violations", []),
            "elapsed_sec": round(res.get("elapsed_sec", 0.0), 2),
        })

    # ---- WAA ----
    waa = calculate_waa(all_results)

    # ---- Aggregierte KPIs ----
    all_csr = [(r["csr_supplier"] + r["csr_retailer"]) / 2 for r in all_results]
    csr_overall = statistics.mean(all_csr) if all_csr else 0.0

    zu_values = [r["zu"] for r in all_results if r.get("zu") is not None]
    zu_mean = statistics.mean(zu_values) if zu_values else None
    zu_median = statistics.median(zu_values) if zu_values else None

    rounds_list = [r["rounds_used"] for r in all_results]
    avg_rounds = statistics.mean(rounds_list) if rounds_list else 0.0

    deal_statuses = {SessionStatus.ACCEPTED.value, SessionStatus.PENDING_APPROVAL.value}
    deals = sum(1 for r in all_results if r["actual_status"] in deal_statuses)
    agreement_rate = deals / len(all_results) if all_results else 0.0

    correct_outcomes = sum(1 for r in all_results if _outcome_correct(r))
    outcome_accuracy = correct_outcomes / len(all_results) if all_results else 0.0

    return {
        "experiment_id": experiment_id,
        "timestamp_utc": timestamp,
        "model": model,
        "temperature": temperature,
        "total_scenarios": len(all_results),
        "scenarios": scenarios_out,
        "aggregate_kpis": {
            "csr_overall": round(csr_overall, 4),
            "waa_precision": waa["precision"],
            "waa_recall": waa["recall"],
            "waa_f1": waa["f1"],
            "false_agreement_rate": waa["false_agreement_rate"],
            "zu_mean": round(zu_mean, 4) if zu_mean is not None else None,
            "zu_median": round(zu_median, 4) if zu_median is not None else None,
            "avg_rounds": round(avg_rounds, 2),
            "agreement_rate": round(agreement_rate, 4),
            "outcome_accuracy": round(outcome_accuracy, 4),
            "deals_reached": deals,
            "aborts": len(all_results) - deals,
        },
        "waa_confusion_matrix": {
            "TP": waa["true_positives"],
            "TN": waa["true_negatives"],
            "FP": waa["false_positives"],
            "FN": waa["false_negatives"],
            "details": waa["details"],
        },
    }


def _outcome_correct(res: dict) -> bool:
    """
    Prüft ob das tatsächliche Ergebnis dem erwarteten entspricht.

    DEAL       → ACCEPTED oder PENDING_APPROVAL
    CONTROLLED_ABORT → FAILED, MAX_ROUNDS, oder REJECTED
    TRADE_OFF_OR_ABORT → alles außer False Agreement (ACCEPTED mit verletzten Constraints)
    """
    expected = res.get("expected_outcome", "")
    actual = res.get("actual_status", "")
    deal_statuses = {SessionStatus.ACCEPTED.value, SessionStatus.PENDING_APPROVAL.value}
    abort_statuses = {SessionStatus.FAILED.value, SessionStatus.MAX_ROUNDS.value, SessionStatus.REJECTED.value}

    if expected == "DEAL":
        return actual in deal_statuses
    elif expected == "CONTROLLED_ABORT":
        return actual in abort_statuses
    elif expected == "TRADE_OFF_OR_ABORT":
        # Beides akzeptiert — nur False Agreement (No-ZOPA → Deal) wäre ein Fehler,
        # aber S11/S12 haben einen Preis-ZOPA, daher ist ACCEPTED grundsätzlich ok.
        return actual in deal_statuses | abort_statuses
    return False
