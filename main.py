"""
main.py
───────
Main entry point and primary experiment runner for the
Multi-Agent Negotiation Framework.

This module exposes:

  run_experiment(config, scenario) -> dict
      Execute one full negotiation experiment and return all KPIs.
      Fully deterministic at temperature=0.0.
      Safe to call multiple times in the same process for batch runs.

  run_batch_experiments(configs, scenario) -> list[dict]
      Run multiple experiments in sequence (e.g., grid search over
      agreement thresholds or temperatures).

Usage
-----
    # Single run with defaults (synthetic baseline scenario)
    python main.py

    # Programmatic use
    from main import run_experiment
    from config.settings import NegotiationConfig, LLMConfig

    kpis = run_experiment(
        config=NegotiationConfig(
            max_rounds=5,
            agreement_threshold=2.0,
            llm_config=LLMConfig(temperature=0.0, model_name="gpt-4o"),
            experiment_id="baseline_001",
        )
    )

Notes
-----
- Temperature=0 is enforced by default for reproducibility.
- The LLM is only called inside agents; orchestration is rule-based.
- All constraint validation happens in the Validator, not in the LLM.
"""

import json
import logging
import sys
from typing import Optional

from dotenv import load_dotenv
load_dotenv()  # Populate os.environ from .env BEFORE any SAP SDK code runs

from config.settings import (
    AICoreSettings,
    LLMConfig,
    NegotiationConfig,
    SyntheticScenarioConfig,
    setup_logging,
)
from agents.retail_agent import RetailAgent
from agents.supplier_agent import SupplierAgent
from evaluation.kpi_tracker import KPITracker
from llm.ai_core_client import AICoreClient
from models.constraints import ConstraintModel
from orchestration.orchestrator import NegotiationOrchestrator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY EXPERIMENT RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_experiment(
    config: Optional[NegotiationConfig] = None,
    scenario: Optional[SyntheticScenarioConfig] = None,
) -> dict:
    """
    Run one complete negotiation experiment and return a KPI dictionary.

    This function is the single entry point for all experiment runs,
    including batch evaluation, parameter sweeps, and reproducibility
    benchmarks in the bachelor thesis.

    Parameters
    ----------
    config   : ``NegotiationConfig`` controlling max rounds, threshold,
               LLM temperature, experiment ID, etc.
               Defaults to the canonical baseline (5 rounds, T=0).
    scenario : ``SyntheticScenarioConfig`` defining initial prices and
               constraint parameters.
               Defaults to the canonical synthetic scenario.

    Returns
    -------
    dict
        Flat KPI dictionary ready for logging, CSV export, or comparison:
        {
            "experiment_id":               str,
            "total_rounds":                int,
            "agreement_reached":           bool,
            "termination_reason":          str | None,
            "final_supplier_price":        float | None,
            "final_retail_offer":          float | None,
            "final_price_gap":             float | None,
            "final_margin":                float | None,
            "final_margin_pct":            str | None,
            "constraint_violations_count": int,
            "total_tokens_used":           int,
            "runtime_seconds":             float,
        }

    Raises
    ------
    EnvironmentError
        If SAP AI Core credentials are missing and no fallback is configured.
    """
    # ── Defaults ──────────────────────────────────────────────────────────────
    if config is None:
        config = NegotiationConfig()
    if scenario is None:
        scenario = SyntheticScenarioConfig()

    # ── Logging ───────────────────────────────────────────────────────────────
    setup_logging(config.log_level)
    logger.info(
        "╔══ run_experiment() ══╗  id='%s'  max_rounds=%d  "
        "threshold=%.2f  temperature=%.1f",
        config.experiment_id,
        config.max_rounds,
        config.agreement_threshold,
        config.llm_config.temperature,
    )

    # ── Constraints ───────────────────────────────────────────────────────────
    constraints = ConstraintModel(
        min_margin=scenario.min_margin,
        min_supplier_price=scenario.min_supplier_price,
        max_budget=scenario.max_budget,
        allowed_delivery_windows=scenario.allowed_delivery_windows,
        retail_selling_price=scenario.retail_selling_price,
    )
    logger.info("Constraints: %s", constraints)

    # ── LLM client ────────────────────────────────────────────────────────────
    llm_client = AICoreClient(
        model_name=config.llm_config.model_name,
        temperature=config.llm_config.temperature,
        max_tokens=config.llm_config.max_tokens,
    )

    # ── Agents ────────────────────────────────────────────────────────────────
    supplier = SupplierAgent(
        name="SupplierAgent",
        llm_client=llm_client,
        initial_price=scenario.supplier_initial_price,
        initial_volume=scenario.supplier_initial_volume,
        max_rounds=config.max_rounds,
        agreement_threshold=config.agreement_threshold,
        max_retries=config.llm_config.max_retries,
    )
    retailer = RetailAgent(
        name="RetailAgent",
        llm_client=llm_client,
        target_price=scenario.retail_target_price,
        retail_selling_price=scenario.retail_selling_price,
        max_rounds=config.max_rounds,
        agreement_threshold=config.agreement_threshold,
        max_retries=config.llm_config.max_retries,
    )

    # ── Orchestrator ──────────────────────────────────────────────────────────
    orchestrator = NegotiationOrchestrator(
        supplier_agent=supplier,
        retail_agent=retailer,
        constraints=constraints,
        config=config,
    )

    # ── Start KPI tracker with experiment ID ──────────────────────────────────
    orchestrator.kpi_tracker.start(experiment_id=config.experiment_id)

    # ── Run negotiation ───────────────────────────────────────────────────────
    final_state = orchestrator.run()

    # ── Collect KPIs ──────────────────────────────────────────────────────────
    kpis = orchestrator.kpi_tracker.get_kpis()

    logger.info(
        "╚══ run_experiment() complete ══╝  "
        "agreement=%s  rounds=%d  runtime=%.2fs",
        kpis["agreement_reached"],
        kpis["total_rounds"],
        kpis["runtime_seconds"],
    )

    return kpis


# ─────────────────────────────────────────────────────────────────────────────
# BATCH EXPERIMENT RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_batch_experiments(
    configs: list[NegotiationConfig],
    scenario: Optional[SyntheticScenarioConfig] = None,
) -> list[dict]:
    """
    Run multiple experiments sequentially and collect all KPI dicts.

    Useful for:
    - Grid search over temperature, max_rounds, agreement_threshold
    - Stability testing (same config, multiple runs)
    - Comparing LLM-based vs. future rule-based baseline agents

    Parameters
    ----------
    configs  : List of ``NegotiationConfig`` objects.
    scenario : Shared ``SyntheticScenarioConfig`` for all runs.
               Defaults to the canonical synthetic scenario.

    Returns
    -------
    list[dict]
        One KPI dict per config, in the same order as ``configs``.
    """
    results = []
    total = len(configs)
    for idx, cfg in enumerate(configs, start=1):
        logger.info("Batch run %d/%d  experiment_id='%s'", idx, total, cfg.experiment_id)
        try:
            kpis = run_experiment(config=cfg, scenario=scenario)
        except Exception as exc:
            logger.error("Batch run %d failed: %s", idx, exc)
            kpis = {
                "experiment_id": cfg.experiment_id,
                "error":         str(exc),
                "agreement_reached": False,
            }
        results.append(kpis)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def _print_kpi_report(kpis: dict, title: str = "EXPERIMENT RESULTS") -> None:
    """Pretty-print KPI dictionary to stdout."""
    width = 56
    print("\n" + "═" * width)
    print(f"  {title}")
    print("═" * width)
    for key, value in kpis.items():
        label = key.replace("_", " ").title()
        print(f"  {label:<32}: {value}")
    print("═" * width + "\n")


if __name__ == "__main__":
    # ── Canonical synthetic baseline experiment ───────────────────────────────
    #
    # Scenario (as defined in requirements):
    #   Supplier start price : 50 EUR/unit
    #   Supplier floor price : 40 EUR/unit
    #   Retail target price  : 35 EUR/unit
    #   Retail selling price : 60 EUR/unit
    #   Min margin           : 25 %  →  max buy price = 45 EUR
    #   Agreement zone       : [40, 45] EUR
    #   Max rounds           : 5
    #   Agreement threshold  : ±2 EUR
    #   LLM temperature      : 0.0  (deterministic)
    # ─────────────────────────────────────────────────────────────────────────

    setup_logging("INFO")

    # Verify credentials are present before wasting time
    settings = AICoreSettings()
    if not settings.is_configured():
        logger.warning(
            "SAP AI Core credentials not found in environment / .env file. "
            "The system will attempt the OpenAI fallback if OPENAI_API_KEY is set. "
            "See .env.example for configuration reference."
        )

    experiment_config = NegotiationConfig(
        max_rounds=5,
        agreement_threshold=2.0,
        llm_config=LLMConfig(
            temperature=0.0,        # Deterministic – required for thesis evaluation
            model_name=settings.aicore_model_name,
            max_tokens=1024,
            max_retries=3,
        ),
        experiment_id="synthetic_baseline_001",
        log_level="INFO",
    )

    try:
        kpis = run_experiment(config=experiment_config)
        _print_kpi_report(kpis, title="SYNTHETIC BASELINE – KPI REPORT")

        # Also export as JSON for downstream analysis
        output_path = "kpi_results.json"
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(kpis, fh, indent=2, default=str)
        logger.info("KPIs exported to '%s'", output_path)

    except EnvironmentError as env_err:
        logger.error("Configuration error: %s", env_err)
        sys.exit(1)
    except Exception as exc:
        logger.exception("Unexpected error during experiment: %s", exc)
        sys.exit(1)
