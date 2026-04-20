"""Deprecated. Importers should use src.pillars.* directly.

This shim re-exports the symbols that lived under `src.workloads.*` before the
five-pillar restructure so external callers keep working for one release.
"""
from ..pillars.ai_application import (
    AZURE_OPENAI_MODELS,
    build_ai_ml_bom,
    build_openai_lines,
)
from ..pillars.azure_security import (
    SIEM_COMMITMENT_TIERS,
    build_siem_bom,
)
from ..pillars.data_platform import (
    FABRIC_CAPACITIES,
    build_data_platform_bom,
)

__all__ = [
    "AZURE_OPENAI_MODELS",
    "FABRIC_CAPACITIES",
    "SIEM_COMMITMENT_TIERS",
    "build_ai_ml_bom",
    "build_data_platform_bom",
    "build_openai_lines",
    "build_siem_bom",
]
