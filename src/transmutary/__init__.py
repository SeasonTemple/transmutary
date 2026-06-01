"""Transmutary — external ecosystem repository observation system.

Phase 0 shared skeleton: config + schema, state, artifacts, llm, stub deliver,
service. Collectors / diagnose / trend belong to Phase 1/2 and are not present.
"""

from transmutary.config import ConfigError
from transmutary.collect.github import SSRFError

__version__ = "0.11.0"

__all__ = (
    "__version__",
    "ConfigError",
    "LLMError",
    "LLMBudgetExceeded",
    "SSRFError",
)


def __getattr__(name):
    """Lazy attribute access for names that require heavy imports.

    LLMError / LLMBudgetExceeded live in :mod:`transmutary.llm`, which imports
    ``litellm``. Importing ``transmutary`` for submodules that do not need the
    LLM gateway (e.g. ``from transmutary import service``) must not pay the
    litellm bootstrap cost. Resolved on first attribute access.
    """
    if name in ("LLMError", "LLMBudgetExceeded"):
        from transmutary import llm

        return getattr(llm, name)
    raise AttributeError(f"module 'transmutary' has no attribute {name!r}")
