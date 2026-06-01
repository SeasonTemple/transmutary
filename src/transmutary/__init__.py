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
    ``litellm``. The lazy lookup here only short-circuits the direct
    ``import transmutary`` path (and ``from transmutary import LLMError``
    style attribute access); submodule imports such as
    ``from transmutary import service`` follow the normal package machinery
    and pull in their transitive deps (currently ``service -> pipeline ->
    filter -> llm``), so the litellm cost is unavoidable while the
    dependency graph stays the way it is. Resolved on first attribute
    access.
    """
    if name in ("LLMError", "LLMBudgetExceeded"):
        from transmutary import llm

        return getattr(llm, name)
    raise AttributeError(f"module 'transmutary' has no attribute {name!r}")
