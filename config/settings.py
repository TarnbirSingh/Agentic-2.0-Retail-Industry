"""
config/settings.py
──────────────────
Central configuration module for the multi-agent negotiation framework.

All system-wide settings, experiment parameters, and environment-variable
loading are defined here.  Nothing else in the system should read os.environ
directly – all configuration flows through this module.

Design principles:
  - AICoreSettings  → Pydantic BaseSettings (reads .env / environment)
  - LLMConfig       → dataclass  (fully serialisable, passed around)
  - NegotiationConfig → dataclass (experiment-level knobs)
  - SyntheticScenarioConfig → dataclass (the canonical test scenario)
"""

import logging
from dataclasses import dataclass, field
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─────────────────────────────────────────────────────────────────────────────
# ENVIRONMENT / SECRETS  (Pydantic BaseSettings – reads from .env or shell)
# ─────────────────────────────────────────────────────────────────────────────

class AICoreSettings(BaseSettings):
    """
    SAP AI Core connection settings.

    All values are loaded from environment variables or a `.env` file.
    The class is intentionally *not* a dataclass so that Pydantic can
    handle coercion, validation, and env-file parsing automatically.

    Required variables (must be set before running experiments):
        AICORE_CLIENT_ID
        AICORE_CLIENT_SECRET
        AICORE_BASE_URL

    Optional variables (have sensible defaults):
        AICORE_AUTH_URL
        AICORE_RESOURCE_GROUP
        AICORE_DEPLOYMENT_ID
        AICORE_MODEL_NAME
        LOG_LEVEL
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",           # silently ignore unknown env vars
        case_sensitive=False,     # AICORE_CLIENT_ID == aicore_client_id
    )

    # ── SAP AI Core OAuth2 credentials ───────────────────────────────────────
    aicore_client_id: str = ""
    aicore_client_secret: str = ""
    aicore_base_url: str = ""
    aicore_auth_url: str = ""
    aicore_resource_group: str = "default"
    aicore_deployment_id: str = ""

    # ── Model / LLM configuration ─────────────────────────────────────────────
    aicore_model_name: str = "gpt-4o"

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Fallback (local dev without SAP AI Core) ──────────────────────────────
    openai_api_key: str = ""

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return v_upper

    # ── Helper methods ────────────────────────────────────────────────────────
    def is_configured(self) -> bool:
        """Return True if the minimum required AI Core credentials are present."""
        return bool(self.aicore_client_id and self.aicore_client_secret and self.aicore_base_url)

    def validate_required(self) -> None:
        """
        Raise EnvironmentError if required credentials are missing.
        Call this at application start-up for early failure.
        """
        if not self.is_configured():
            missing: list[str] = []
            if not self.aicore_client_id:
                missing.append("AICORE_CLIENT_ID")
            if not self.aicore_client_secret:
                missing.append("AICORE_CLIENT_SECRET")
            if not self.aicore_base_url:
                missing.append("AICORE_BASE_URL")
            raise EnvironmentError(
                f"Missing required environment variables: {missing}\n"
                "Set them in your .env file or shell environment.\n"
                "See .env.example for reference."
            )


# ─────────────────────────────────────────────────────────────────────────────
# LLM CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    """
    LLM-specific parameters.

    temperature = 0.0  →  fully deterministic output (required for research).
    Increase slightly (0.1–0.3) only if you want stochastic experiments.
    """

    temperature: float = 0.0    # deterministic by default
    model_name: str = "gpt-4o"  # deployment model alias in SAP AI Core
    max_tokens: int = 1024      # cap on response length
    max_retries: int = 3        # retry count on JSON parse failure


# ─────────────────────────────────────────────────────────────────────────────
# NEGOTIATION / EXPERIMENT CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NegotiationConfig:
    """
    Runtime configuration for a single negotiation experiment.

    This dataclass is the single source of truth for an experiment run.
    Pass different instances to ``run_experiment()`` for batch evaluation
    or grid-search over parameters.

    Attributes
    ----------
    max_rounds          : Hard cap on negotiation iterations.
    agreement_threshold : |supplier_price − retail_offer| < threshold → agreement.
    llm_config          : LLM-specific parameters (temperature, model, …).
    experiment_id       : Unique label for logging and KPI storage.
    log_level           : Python logging level for this run.
    """

    max_rounds: int = 5
    agreement_threshold: float = 2.0           # EUR price gap triggers agreement
    llm_config: LLMConfig = field(default_factory=LLMConfig)
    experiment_id: str = "experiment_001"
    log_level: str = "INFO"


# ─────────────────────────────────────────────────────────────────────────────
# SYNTHETIC SCENARIO CONFIGURATION  (the canonical research baseline)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SyntheticScenarioConfig:
    """
    Canonical synthetic scenario used for reproducible baseline experiments.

    Economic context
    ----------------
    * Supplier floor  : 40 EUR/unit
    * Supplier start  : 50 EUR/unit
    * Retail sells at : 60 EUR/unit
    * Min margin      : 25 %  →  max acceptable buy price = 60 × (1−0.25) = 45 EUR
    * Retail target   : 35 EUR/unit  (aspirational, below agreement zone)
    * Agreement zone  : [40, 45] EUR  (feasible if both agents converge)
    * Max rounds      : 5
    """

    # ── Constraint parameters ─────────────────────────────────────────────────
    min_margin: float = 0.25
    min_supplier_price: float = 40.0
    max_budget: float = 100_000.0
    allowed_delivery_windows: List[str] = field(
        default_factory=lambda: ["Q3", "Q4"]
    )

    # ── Initial offer parameters ──────────────────────────────────────────────
    supplier_initial_price: float = 50.0
    supplier_initial_volume: int = 1_000
    retail_target_price: float = 35.0
    initial_delivery_window: str = "Q3"
    initial_payment_terms: str = "Net30"

    # ── Retail economics ──────────────────────────────────────────────────────
    # The price at which the retailer sells to end customers.
    # Used by the Validator to compute gross margin.
    retail_selling_price: float = 60.0


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """
    Configure the root logger with the project's standard format.

    Call this once at the start of ``main.py`` or ``run_experiment()``.
    The ``force=True`` flag allows re-configuration across repeated
    experiment runs in a single Python session (e.g., batch notebooks).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        force=True,
    )
