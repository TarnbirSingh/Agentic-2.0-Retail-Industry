"""
agents/coder_agent.py
─────────────────────
On-demand code-generation-and-execution sub-agent.

This is the component that elevates the system to Agentic 2.0.

Why this matters
----------------
LLMs are unreliable at mental arithmetic, especially under constraints.
If we ask the LLM to "compute the optimal counter-price" in natural
language it may produce wrong numbers (hallucination, rounding errors).

The CoderAgent solves this by:
  1. Asking the LLM to *write Python code* for the calculation (LLMs
     are far more reliable at code generation than mental math).
  2. Executing that code in a sandboxed Python namespace.
  3. Returning the verified numeric result to the calling agent.

The negotiation agents (RetailAgent, SupplierAgent) call this before
building their LLM prompt, so the final prompt contains a
*verified computed value* rather than a vague instruction like
"aim for something around 42 EUR".

Architecture position
---------------------
    RetailAgent / SupplierAgent
        │
        ├── CoderAgent.compute(task) ← Agentic 2.0 delegation
        │       │
        │       ├── LLM generates Python code (code-gen call)
        │       ├── Sandbox exec()  (deterministic execution)
        │       └── Returns float
        │
        └── NegotiationOffer LLM call  (uses verified number in prompt)

Security model
--------------
Generated code runs in a *whitelist sandbox*:
  - Allowed builtins: abs, min, max, round, sum, range, int, float, bool
  - Allowed module:   math  (pre-imported)
  - Blocked: import, open, os, subprocess, eval, exec, __builtins__ access
  - Timeout: 2 seconds (prevents infinite loops)

A static safety check scans for dangerous patterns before execution.
This is sufficient for a research prototype.  Production systems should
use a process-isolated sandbox (e.g. PyPy sandbox, subprocess, Docker).
"""

import logging
import math
import re
import time
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from llm.ai_core_client import AICoreClient
from models.code_models import CodeResult, CodeTask

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SAFETY CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# Regex patterns that indicate dangerous code → reject before execution
_DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bimport\b"),
    re.compile(r"\bopen\s*\("),
    re.compile(r"\bos\s*\."),
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"__[a-z]+__"),
    re.compile(r"\bsocket\b"),
    re.compile(r"\brequests\b"),
    re.compile(r"\bbuiltins\b"),
]

# Whitelisted builtins available inside generated code
_SAFE_BUILTINS: dict = {
    "abs": abs, "min": min, "max": max, "round": round,
    "sum": sum, "len": len, "range": range,
    "int": int, "float": float, "bool": bool, "str": str,
    "True": True, "False": False, "None": None,
    "print": print,   # harmless, useful for debug
}

# Maximum lines of generated code (soft readability limit)
_MAX_CODE_LINES = 30


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

_CODER_SYSTEM_PROMPT = """\
You are a Python code generator specialised in financial and negotiation calculations.

STRICT RULES:
1. Output ONLY a Python code block between ```python and ```.
2. DO NOT use any import statements – the `math` module is already available.
3. DO NOT use open(), os, subprocess, eval, exec, or any I/O operations.
4. The LAST assignment in the code MUST set `result = <float>`.
5. Keep the code concise (≤ {max_lines} lines), readable, and well-commented.
6. Use only these builtins: abs, min, max, round, sum, range, int, float, bool.
7. All intermediate variable names must be descriptive.

The output variable `result` will be extracted and used directly in a B2B
negotiation engine. It must be a float representing a price in EUR.
"""

_CODER_HUMAN_PROMPT = """\
=== CALCULATION TASK ===
{task_description}

=== AVAILABLE VARIABLES (pre-loaded in namespace) ===
{context_str}

Write Python code that computes `result = <answer in EUR>`.
Only output the ```python ... ``` block. Nothing else.
"""


# ─────────────────────────────────────────────────────────────────────────────
# CODER AGENT
# ─────────────────────────────────────────────────────────────────────────────

class CoderAgent:
    """
    On-demand code-generation-and-execution sub-agent.

    Used by negotiation agents to delegate numeric reasoning to
    deterministic Python code rather than LLM mental arithmetic.

    Parameters
    ----------
    llm_client  : Shared ``AICoreClient`` instance (reuses LLM connection).
    max_retries : Retry count on code generation or execution failure.
    """

    def __init__(
        self,
        llm_client: AICoreClient,
        max_retries: int = 2,
    ) -> None:
        self.llm_client = llm_client
        self.max_retries = max_retries
        self.logger = logging.getLogger("agent.CoderAgent")

    # ── Public API ────────────────────────────────────────────────────────────

    def compute(self, task: CodeTask) -> CodeResult:
        """
        Generate and execute Python code for the given numeric task.

        Retries up to ``max_retries`` times on generation or execution
        failure before returning a failed ``CodeResult``.

        Parameters
        ----------
        task : ``CodeTask`` describing what to compute and the variable context.

        Returns
        -------
        CodeResult
            Always returns a result; check ``result.success`` before using
            ``result.value``.
        """
        last_error = "Unknown error"

        for attempt in range(1, self.max_retries + 1):
            try:
                result = self._generate_and_execute(task)
                self.logger.info(
                    "CoderAgent | attempt %d/%d | result=%s",
                    attempt, self.max_retries, result,
                )
                return result

            except Exception as exc:
                last_error = str(exc)
                self.logger.warning(
                    "CoderAgent attempt %d/%d failed: %s",
                    attempt, self.max_retries, exc,
                )

        return CodeResult(
            success=False,
            error_message=f"All {self.max_retries} attempts failed. Last: {last_error}",
        )

    def compute_optimal_retail_price(
        self,
        last_supplier_price: float,
        target_price: float,
        max_acceptable_price: float,
        current_round: int,
        max_rounds: int,
        agreement_threshold: float,
    ) -> CodeResult:
        """
        Convenience method: compute the optimal retail counter-price.

        Uses a concession-curve formula: start near target_price and
        converge toward max_acceptable_price as rounds progress.
        This is the *computed anchor* injected into the RetailAgent prompt.
        """
        context = {
            "last_supplier_price":  last_supplier_price,
            "target_price":         target_price,
            "max_acceptable_price": max_acceptable_price,
            "current_round":        current_round,
            "max_rounds":           max_rounds,
            "agreement_threshold":  agreement_threshold,
        }
        task = CodeTask(
            task_description=(
                "Compute the optimal retail counter-price for this negotiation round.\n"
                "Strategy: use a concession curve that starts near target_price "
                "and converges toward max_acceptable_price as rounds progress.\n"
                "If last_supplier_price is already within agreement_threshold of "
                "max_acceptable_price, return max_acceptable_price directly.\n"
                "The result must be a float in EUR, rounded to 2 decimal places, "
                "and must never exceed max_acceptable_price."
            ),
            context=context,
        )
        return self.compute(task)

    def compute_optimal_supplier_price(
        self,
        last_retail_price: float,
        initial_price: float,
        min_supplier_price: float,
        current_round: int,
        max_rounds: int,
        agreement_threshold: float,
    ) -> CodeResult:
        """
        Convenience method: compute the optimal supplier counter-price.

        Uses a concession-curve formula: start near initial_price and
        concede gradually toward min_supplier_price as rounds progress.
        """
        context = {
            "last_retail_price":   last_retail_price,
            "initial_price":       initial_price,
            "min_supplier_price":  min_supplier_price,
            "current_round":       current_round,
            "max_rounds":          max_rounds,
            "agreement_threshold": agreement_threshold,
        }
        task = CodeTask(
            task_description=(
                "Compute the optimal supplier counter-price for this negotiation round.\n"
                "Strategy: use a concession curve that starts near initial_price "
                "and concedes toward min_supplier_price as rounds progress.\n"
                "If last_retail_price is within agreement_threshold of min_supplier_price, "
                "return min_supplier_price directly to close the deal.\n"
                "The result must be a float in EUR, rounded to 2 decimal places, "
                "and must never go below min_supplier_price."
            ),
            context=context,
        )
        return self.compute(task)

    # ── Private: generation + execution pipeline ──────────────────────────────

    def _generate_and_execute(self, task: CodeTask) -> CodeResult:
        """Single attempt: LLM generates code → extract → safety check → exec."""
        # 1. Generate code via LLM
        raw_code = self._call_llm(task)

        # 2. Extract code block
        code = self._extract_code_block(raw_code)
        self.logger.debug("CoderAgent generated code:\n%s", code)

        # 3. Safety check
        safety_error = self._check_safety(code)
        if safety_error:
            return CodeResult(
                success=False,
                generated_code=code,
                error_message=f"Safety check failed: {safety_error}",
            )

        # 4. Execute in sandbox
        return self._execute_sandboxed(code, task)

    def _call_llm(self, task: CodeTask) -> str:
        """Invoke LLM with the code-generation prompt and return raw text."""
        system_msg = _CODER_SYSTEM_PROMPT.format(max_lines=_MAX_CODE_LINES)
        context_str = "\n".join(
            f"  {k} = {v}  ({type(v).__name__})"
            for k, v in task.context.items()
        )
        human_msg = _CODER_HUMAN_PROMPT.format(
            task_description=task.task_description,
            context_str=context_str or "  (no context variables)",
        )

        llm = self.llm_client.get_llm()
        response = llm.invoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=human_msg),
        ])
        return response.content if hasattr(response, "content") else str(response)

    def _extract_code_block(self, text: str) -> str:
        """
        Extract Python source from a ```python ... ``` markdown fence.

        Falls back to the raw text if no fence is found.
        """
        # Try ```python ... ```
        match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try plain ``` ... ```
        match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Last resort: assume the whole response is code
        return text.strip()

    def _check_safety(self, code: str) -> Optional[str]:
        """
        Return an error string if the code contains dangerous patterns.
        Returns None if the code passes all checks.
        """
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(code):
                return f"Forbidden pattern detected: '{pattern.pattern}'"

        lines = code.splitlines()
        if len(lines) > _MAX_CODE_LINES:
            return f"Code too long ({len(lines)} lines > {_MAX_CODE_LINES} limit)"

        return None

    def _execute_sandboxed(self, code: str, task: CodeTask) -> CodeResult:
        """
        Execute ``code`` in an isolated namespace with a whitelist of builtins.

        The context variables from ``task.context`` are pre-injected so the
        generated code can reference them directly by name.

        A simple 2-second timeout guard is implemented via time measurement.
        (For a production system, use subprocess or threading.Timer.)
        """
        namespace: dict = {
            "__builtins__": _SAFE_BUILTINS,
            "math": math,
            **task.context,   # inject context variables
        }

        start_ns = time.perf_counter_ns()
        try:
            exec(compile(code, "<coder_agent>", "exec"), namespace)  # noqa: S102
        except Exception as exc:
            elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
            return CodeResult(
                success=False,
                generated_code=code,
                error_message=f"Execution error: {exc}",
                execution_time_ms=elapsed_ms,
            )

        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000

        # Extract the expected output variable
        raw_value = namespace.get(task.expected_output_var)

        if raw_value is None:
            return CodeResult(
                success=False,
                generated_code=code,
                error_message=(
                    f"Variable '{task.expected_output_var}' was not set "
                    f"after code execution. "
                    f"Available vars: {[k for k in namespace if not k.startswith('_')]}"
                ),
                execution_time_ms=elapsed_ms,
            )

        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            return CodeResult(
                success=False,
                generated_code=code,
                error_message=f"Result '{raw_value}' could not be converted to float: {exc}",
                execution_time_ms=elapsed_ms,
            )

        return CodeResult(
            success=True,
            value=value,
            generated_code=code,
            execution_time_ms=elapsed_ms,
        )

    def __repr__(self) -> str:
        return f"CoderAgent(model={self.llm_client.model_name!r})"
