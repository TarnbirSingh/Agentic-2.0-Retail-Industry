"""
agents/base_agent.py
────────────────────
Abstract base class for all negotiation agents.

Design principles
-----------------
1. The LLM is the *only* decision engine inside an agent.  Agents
   generate offers; they do not orchestrate, validate, or track state.
2. Agents are stateless between calls.  All context is passed in via
   ``NegotiationState`` and ``ConstraintModel``.
3. ``generate_offer()`` always returns a *parsed* ``NegotiationOffer``.
   If parsing fails after ``max_retries`` attempts the agent raises,
   which is handled by the orchestrator.
4. JSON extraction is centralised here so that concrete agents only
   implement prompt-building logic.

Extensibility hooks
-------------------
- Override ``_build_system_prompt()`` to change agent persona.
- Override ``_build_human_prompt()`` to change context framing.
- Override ``_post_process_offer()`` to add agent-specific tweaks
  after parsing (e.g., clamping a price to a range).
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from pydantic import ValidationError

from llm.ai_core_client import AICoreClient
from models.constraints import ConstraintModel
from models.negotiation_models import AgentRole, NegotiationOffer, NegotiationState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# JSON EXTRACTION UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def extract_json_from_text(text: str) -> dict:
    """
    Robustly extract the first JSON object from an LLM response string.

    Handles:
    * Bare JSON  (ideal case)
    * JSON wrapped in ```json ... ``` markdown fences
    * JSON wrapped in ``` ... ``` fences (no language tag)
    * JSON embedded in surrounding prose

    Raises
    ------
    ValueError
        If no valid JSON object can be found in ``text``.
    """
    text = text.strip()

    # 1. Try direct parse first (fastest path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Try stripping markdown code fences
    for pattern in [
        r"```json\s*(.*?)\s*```",   # ```json ... ```
        r"```\s*(.*?)\s*```",       # ``` ... ```
    ]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue

    # 3. Try extracting a raw {...} block from prose
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not extract valid JSON from LLM response.\n"
        f"First 300 chars: {text[:300]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ABSTRACT BASE AGENT
# ─────────────────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Abstract base class for all negotiation agents.

    Concrete subclasses must implement:
    - ``role``            : ``AgentRole`` enum value
    - ``_build_system_prompt()`` : Agent persona / instruction block
    - ``_build_human_prompt()``  : Round-specific context

    Parameters
    ----------
    name        : Human-readable agent label (used in logs).
    llm_client  : Initialised ``AICoreClient`` instance.
    max_retries : Number of times to retry on parse failure.
    """

    def __init__(
        self,
        name: str,
        llm_client: AICoreClient,
        max_retries: int = 3,
    ) -> None:
        self.name = name
        self.llm_client = llm_client
        self.max_retries = max_retries
        self.logger = logging.getLogger(f"agent.{name}")

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def role(self) -> AgentRole:
        """The ``AgentRole`` this agent represents."""
        ...

    def generate_offer(
        self,
        state: NegotiationState,
        constraints: ConstraintModel,
    ) -> NegotiationOffer:
        """
        Generate a structured negotiation offer via the LLM.

        The method builds a prompt, invokes the LLM, extracts the JSON
        response, and validates it against ``NegotiationOffer``.  It
        retries up to ``max_retries`` times on parse / validation error.

        Parameters
        ----------
        state       : Current full negotiation state (read-only).
        constraints : Business constraints for this experiment.

        Returns
        -------
        NegotiationOffer
            A successfully parsed and schema-valid offer.

        Raises
        ------
        RuntimeError
            If all retry attempts fail.
        """
        system_prompt = self._build_system_prompt(constraints)
        human_prompt  = self._build_human_prompt(state, constraints)

        last_exc: Exception = RuntimeError("Unknown error")

        for attempt in range(1, self.max_retries + 1):
            try:
                offer = self._invoke_llm(system_prompt, human_prompt)
                offer = self._post_process_offer(offer, state, constraints)
                self.logger.info(
                    "[%s] Round %d | Attempt %d/%d | offer=%s",
                    self.name,
                    state.current_round,
                    attempt,
                    self.max_retries,
                    offer.to_prompt_str(),
                )
                return offer

            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                last_exc = exc
                self.logger.warning(
                    "[%s] Attempt %d/%d failed: %s",
                    self.name,
                    attempt,
                    self.max_retries,
                    exc,
                )

        raise RuntimeError(
            f"[{self.name}] Failed to generate a valid offer after "
            f"{self.max_retries} attempts. Last error: {last_exc}"
        ) from last_exc

    # ── Abstract prompt builders ──────────────────────────────────────────────

    @abstractmethod
    def _build_system_prompt(self, constraints: ConstraintModel) -> str:
        """Return the system-level instruction string for this agent."""
        ...

    @abstractmethod
    def _build_human_prompt(
        self,
        state: NegotiationState,
        constraints: ConstraintModel,
    ) -> str:
        """Return the round-specific human message string."""
        ...

    # ── Optional post-processing hook ─────────────────────────────────────────

    def _post_process_offer(
        self,
        offer: NegotiationOffer,
        state: NegotiationState,
        constraints: ConstraintModel,
    ) -> NegotiationOffer:
        """
        Optional hook executed after parsing but before returning the offer.

        Default implementation is a no-op.  Subclasses can override to
        clamp prices, adjust volumes, etc.

        NOTE: Do not enforce business constraints here – that is the
        Validator's responsibility.
        """
        return offer

    # ── Private LLM invocation ────────────────────────────────────────────────

    def _invoke_llm(self, system_prompt: str, human_prompt: str) -> NegotiationOffer:
        """
        Invoke the LLM with the given prompts and parse the response.

        Raises
        ------
        ValueError        : If JSON extraction fails.
        ValidationError   : If the extracted dict does not match ``NegotiationOffer``.
        """
        llm = self.llm_client.get_llm()

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ]

        response = llm.invoke(messages)

        # LangChain returns AIMessage; extract string content
        raw_text: str = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )

        self.logger.debug("[%s] Raw LLM response: %s", self.name, raw_text[:400])

        offer_dict = extract_json_from_text(raw_text)
        return NegotiationOffer(**offer_dict)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, role={self.role.value!r})"
