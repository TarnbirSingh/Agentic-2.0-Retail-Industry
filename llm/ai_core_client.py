"""
llm/ai_core_client.py
─────────────────────
SAP AI Core LLM client wrapper.

Provides a LangChain-compatible ``BaseChatModel`` instance backed by
SAP's Generative AI Hub SDK.  Supports hot-swappable temperature for
deterministic vs. stochastic experiments.

Authentication is handled entirely via environment variables (read by
the ``gen_ai_hub`` SDK automatically):
    AICORE_CLIENT_ID
    AICORE_CLIENT_SECRET
    AICORE_BASE_URL
    AICORE_AUTH_URL
    AICORE_RESOURCE_GROUP

If the ``generative-ai-hub-sdk`` package is unavailable (e.g., during
local development), the client falls back to ``langchain-openai`` and
reads ``OPENAI_API_KEY`` from the environment.

Design notes
------------
- Lazy initialisation: the actual LLM object is only created on first
  call to ``get_llm()``.  This avoids expensive network round-trips
  during unit testing and import-time.
- ``with_temperature()`` returns a *new* ``AICoreClient`` with the
  requested temperature, leaving the original untouched (immutable
  semantics → reproducibility).
"""

import logging
import os
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)


class AICoreClient:
    """
    Reusable LLM client for SAP AI Core Generative AI Hub.

    Parameters
    ----------
    model_name  : Model alias used in the AI Core deployment
                  (e.g. "gpt-4o", "gpt-35-turbo").
    temperature : Sampling temperature.  0.0 = fully deterministic.
    max_tokens  : Maximum number of tokens in the LLM response.
    deployment_id : Optional explicit deployment ID.  If omitted, read
                  from ``AICORE_DEPLOYMENT_ID`` environment variable.
    """

    def __init__(
        self,
        model_name: str = "gpt-4o",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        deployment_id: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.deployment_id = deployment_id or os.getenv("AICORE_DEPLOYMENT_ID", "")

        # Lazy – populated on first call to get_llm()
        self._llm: Optional[BaseChatModel] = None

        logger.info(
            "AICoreClient created | model=%s | temperature=%s | max_tokens=%s",
            model_name,
            temperature,
            max_tokens,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def get_llm(self) -> BaseChatModel:
        """
        Return the LangChain ``BaseChatModel`` instance (lazy init).

        Thread-safety note: not thread-safe by design.  Each experiment
        run should use its own ``AICoreClient`` instance.
        """
        if self._llm is None:
            self._llm = self._initialise_llm()
        return self._llm

    def with_temperature(self, temperature: float) -> "AICoreClient":
        """
        Return a *new* ``AICoreClient`` with a different temperature.

        The original instance is not mutated, preserving reproducibility
        when comparing deterministic (T=0) vs. stochastic runs.

        Example
        -------
        >>> stochastic_client = base_client.with_temperature(0.5)
        """
        return AICoreClient(
            model_name=self.model_name,
            temperature=temperature,
            max_tokens=self.max_tokens,
            deployment_id=self.deployment_id,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PRIVATE – INITIALISATION
    # ─────────────────────────────────────────────────────────────────────────

    def _initialise_llm(self) -> BaseChatModel:
        """
        Build the concrete LangChain LLM object.

        Tries SAP Generative AI Hub first; falls back to standard
        ``langchain-openai`` if the SAP SDK is not installed.
        """
        # ── Primary: SAP Generative AI Hub SDK ────────────────────────────────
        try:
            return self._build_aicore_llm()
        except ImportError:
            logger.warning(
                "generative-ai-hub-sdk not found. "
                "Falling back to langchain-openai. "
                "Install 'generative-ai-hub-sdk' for SAP AI Core support."
            )

        # ── Fallback: standard OpenAI via langchain-openai ────────────────────
        try:
            return self._build_openai_fallback_llm()
        except ImportError:
            raise RuntimeError(
                "No LLM backend available.\n"
                "Install at least one of:\n"
                "  pip install generative-ai-hub-sdk   # SAP AI Core\n"
                "  pip install langchain-openai         # Standard OpenAI fallback\n"
                "and set the corresponding environment variables."
            )

    def _build_aicore_llm(self) -> BaseChatModel:
        """
        Instantiate the LLM via SAP's ``gen_ai_hub`` LangChain integration.

        Credentials are passed *explicitly* to ``get_proxy_client()`` so the
        SDK never has to auto-discover them via ``AICoreV2Client.from_env()``.
        This makes the client work reliably regardless of whether the caller
        pre-loaded the .env file into ``os.environ``.
        """
        # Import here so the module can still load without the SAP SDK
        from gen_ai_hub.proxy.langchain.openai import ChatOpenAI as AICoreOpenAI  # type: ignore[import]
        from gen_ai_hub.proxy.core.proxy_clients import get_proxy_client  # type: ignore[import]

        base_url = os.getenv("AICORE_BASE_URL", "")
        if not base_url:
            raise EnvironmentError(
                "AICORE_BASE_URL is not set. "
                "Copy .env.example to .env and fill in your SAP AI Core credentials."
            )

        logger.info("Connecting to SAP Generative AI Hub | model=%s", self.model_name)

        # Build the proxy client with explicit credentials.
        # GenAIHubProxyClient accepts base_url/auth_url/client_id/client_secret
        # and calls AICoreV2Client.from_env(**kwargs) internally.
        proxy_client = get_proxy_client(
            base_url=base_url,
            auth_url=os.getenv("AICORE_AUTH_URL", ""),
            client_id=os.getenv("AICORE_CLIENT_ID", ""),
            client_secret=os.getenv("AICORE_CLIENT_SECRET", ""),
            resource_group=os.getenv("AICORE_RESOURCE_GROUP", "default"),
        )

        llm = AICoreOpenAI(
            proxy_model_name=self.model_name,
            proxy_client=proxy_client,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        logger.info("SAP AI Core LLM ready.")
        return llm

    def _build_openai_fallback_llm(self) -> BaseChatModel:
        """
        Fallback: standard ``langchain-openai`` ``ChatOpenAI``.

        Used when the SAP SDK is unavailable (local development / CI).
        Requires ``OPENAI_API_KEY`` in the environment.
        """
        from langchain_openai import ChatOpenAI  # type: ignore[import]

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "Fallback requires OPENAI_API_KEY environment variable. "
                "Set it or install 'generative-ai-hub-sdk' for SAP AI Core."
            )

        logger.warning(
            "Using OpenAI fallback client (NOT SAP AI Core) | model=%s",
            self.model_name,
        )
        return ChatOpenAI(
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            api_key=api_key,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # DUNDER HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"AICoreClient(model={self.model_name!r}, "
            f"temperature={self.temperature}, "
            f"max_tokens={self.max_tokens})"
        )
