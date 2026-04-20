"""Pillar registry — one module per Azure pillar.

Each pillar module exposes the uniform contract:
    PILLAR_METADATA: dict
    def render_inputs(st, prefs, app_name, region, upload_bytes, upload_name, profile) -> dict
    def build_bom(client, region, inputs, app_name, pricing_mode) -> tuple[list[BomLine], list[dict]]
"""
from __future__ import annotations

from types import ModuleType
from typing import Dict, List

from . import (
    ai_application,
    auto_simulate,
    azure_security,
    data_platform,
    infra_lift_shift,
    infra_modernization,
)


# Iteration order (also the order displayed in the multi-select)
PILLAR_ORDER: List[str] = [
    "infra_lift_shift",
    "infra_modernization",
    "data_platform",
    "ai_application",
    "azure_security",
]


PILLARS: Dict[str, ModuleType] = {
    "infra_lift_shift":    infra_lift_shift,
    "infra_modernization": infra_modernization,
    "data_platform":       data_platform,
    "ai_application":      ai_application,
    "azure_security":      azure_security,
}


def get_pillar(key: str) -> ModuleType:
    return PILLARS[key]


def all_metadata() -> Dict[str, dict]:
    return {k: m.PILLAR_METADATA for k, m in PILLARS.items()}


__all__ = [
    "PILLARS",
    "PILLAR_ORDER",
    "get_pillar",
    "all_metadata",
    "ai_application",
    "auto_simulate",
    "azure_security",
    "data_platform",
    "infra_lift_shift",
    "infra_modernization",
]
