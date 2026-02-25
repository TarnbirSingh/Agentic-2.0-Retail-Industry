"""
models/code_models.py
─────────────────────
Pydantic data models for the CoderAgent sub-system.

The CoderAgent pattern is the key element that elevates this system
to Agentic 2.0: negotiation agents dynamically delegate numeric
reasoning to a code-generation-and-execution sub-agent rather than
relying on the LLM to do arithmetic in its head.

Flow:
    NegotiationAgent  →  CodeTask  →  CoderAgent  →  CodeResult  →  NegotiationAgent
                                         │
                                    LLM generates Python code
                                    Sandboxed exec() runs it
                                    float result returned
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CodeTask(BaseModel):
    """
    A request sent to the CoderAgent describing a numeric computation.

    Fields
    ------
    task_description      : Natural-language explanation of what to compute.
                            Be specific: include all variable names and their
                            meaning so the LLM can write correct code.
    context               : Dict of variable names → numeric values that will
                            be available as pre-bound names in the execution
                            namespace. Only numeric types (int/float) allowed.
    expected_output_var   : Name of the Python variable that must hold the
                            final answer after execution. Default: ``result``.
    """

    task_description: str = Field(
        ...,
        min_length=10,
        description="Natural-language description of the calculation to perform.",
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Numeric variables pre-injected into the execution namespace.",
    )
    expected_output_var: str = Field(
        default="result",
        description="Python variable name that holds the final answer.",
    )


class CodeResult(BaseModel):
    """
    Result returned by the CoderAgent after code generation and execution.

    Fields
    ------
    success          : True if code was generated AND executed without errors.
    value            : The numeric result (None on failure).
    generated_code   : The Python source code produced by the LLM.
    error_message    : Empty on success; error detail on failure.
    execution_time_ms: Wall-clock time for code execution in milliseconds.
    """

    success: bool
    value: Optional[float] = None
    generated_code: str = ""
    error_message: str = ""
    execution_time_ms: float = 0.0

    def get_value_or_default(self, default: float) -> float:
        """Return the computed value, or ``default`` if execution failed."""
        if self.success and self.value is not None:
            return self.value
        return default

    def __repr__(self) -> str:
        if self.success:
            return f"CodeResult(OK, value={self.value})"
        return f"CodeResult(FAIL, error={self.error_message!r})"
