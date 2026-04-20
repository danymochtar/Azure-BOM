"""Back-compat shim. Security catalog + builders moved to `src.pillars.azure_security`.

Re-exports the public symbols so any existing import sites keep working.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..models import BomLine
from ..pillars.azure_security import (
    SECURITY_COMPONENTS,
    SECURITY_TIERS,
    _cspm_line,
    _la_commitment_line,
    _la_retention_line,
    _pick,
    _sentinel_line,
    build_security_bom,
    defender_plans_for_tier,
)
from ..pricing.retail import RetailPricesClient


def build_security_tier_bom(
    client: RetailPricesClient,
    region: str,
    tier: str,
    vm_count: int,
    sentinel_gb: Optional[float] = None,
    log_analytics_gb: Optional[float] = None,
    defender_manual_counts: Optional[Dict[str, float]] = None,
    add_log_analytics: bool = True,
) -> List[BomLine]:
    """Preset-driven security BOM. Prefer `build_security_bom` for new code."""
    keys = SECURITY_TIERS.get(tier, {}).get("components", [])
    return build_security_bom(
        client=client, region=region, enabled_keys=keys,
        vm_count=vm_count, la_gb=sentinel_gb or log_analytics_gb or 0.0,
        manual_counts=defender_manual_counts or {},
    )


__all__ = [
    "SECURITY_COMPONENTS",
    "SECURITY_TIERS",
    "build_security_bom",
    "build_security_tier_bom",
    "defender_plans_for_tier",
    "_cspm_line",
    "_sentinel_line",
    "_la_commitment_line",
    "_la_retention_line",
    "_pick",
]
