"""Right-size on-prem VMs into Azure VM SKUs and managed disks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..models import InventoryItem
from .vm_catalog import VM_CATALOG, VmSku


@dataclass
class DiskRec:
    sku: str          # Azure disk SKU: P, E, S tier + size code (e.g. P10, E30)
    tier: str         # "Premium SSD", "Standard SSD", "Standard HDD"
    size_gib: int
    meter_name: str   # e.g. "P10 LRS Disk"


# Managed disk size ladder (GiB) — maps provisioned size to the smallest >= disk
DISK_LADDER: List[Tuple[str, int]] = [
    ("P1", 4),     ("P2", 8),     ("P3", 16),    ("P4", 32),    ("P6", 64),
    ("P10", 128),  ("P15", 256),  ("P20", 512),  ("P30", 1024), ("P40", 2048),
    ("P50", 4096), ("P60", 8192), ("P70", 16384),("P80", 32767),
]

TIER_PREFIX = {"Premium SSD": "P", "Standard SSD": "E", "Standard HDD": "S"}
TIER_METER_PREFIX = {"Premium SSD": "P", "Standard SSD": "E", "Standard HDD": "S"}


def _memory_to_cpu_ratio(item: InventoryItem) -> float:
    return (item.memory_gb / item.vcpu) if item.vcpu else 0.0


def recommend_vm(item: InventoryItem, headroom: float = 1.0, prefer_burstable_cpu: int = 2) -> VmSku:
    """Right-size an on-prem VM to the closest Azure SKU.

    Sizing policy:
      1. **1:1 exact match.** If the catalog contains a SKU whose vCPU AND
         memory equal the source spec, return it — no padding, no over-
         provisioning. This is the common case for well-tagged RVTools
         exports (e.g. 4 vCPU / 16 GB → D4s v5).
      2. **Nearest cost-optimized fit.** If no exact match exists, return
         the SMALLEST SKU whose capacity still covers the source spec
         (vCPU >= item.vcpu AND memory_gb >= item.memory_gb). Picking the
         smallest fitting SKU minimises cost while ensuring the workload
         fits — the "nearest" in both size and price.

    `headroom` defaults to 1.0 (1:1 sizing). Callers who deliberately want
    a safety margin may pass 1.1 / 1.2 / etc. — the raw spec is multiplied
    before the fit search. When headroom > 1.0, the exact-match fast path
    is skipped (an exact match of the RAW spec isn't the user's intent
    when they've asked for padding).

    Family preference:
      - Burstable B-series only for small (<=prefer_burstable_cpu vCPU)
        low-memory workloads.
      - Memory-optimized E-series when memory-to-CPU ratio >= 6.
      - General D-series otherwise.
    """
    ratio = _memory_to_cpu_ratio(item)

    def family_score(sku: VmSku) -> int:
        if sku.family == "burstable":
            return 0 if item.vcpu <= prefer_burstable_cpu and item.memory_gb <= 32 else 3
        if sku.family == "memory":
            return 0 if ratio >= 6 else 2
        return 1  # general

    # --- 1:1 exact match (only when no deliberate headroom padding) ---
    if abs(headroom - 1.0) < 1e-9:
        exact = [
            s for s in VM_CATALOG
            if s.vcpu == int(item.vcpu) and abs(s.memory_gb - float(item.memory_gb)) < 0.5
        ]
        if exact:
            exact.sort(key=lambda s: (family_score(s), s.priority))
            return exact[0]

    # --- Nearest cost-optimized fit ---
    req_cpu = max(1, int(round(item.vcpu * headroom)))
    req_mem = round(item.memory_gb * headroom, 2)

    def fits(sku: VmSku) -> bool:
        return sku.vcpu >= req_cpu and sku.memory_gb >= req_mem

    candidates = [s for s in VM_CATALOG if fits(s)]
    if not candidates:
        # Larger than any catalog entry — return the biggest we have; caller
        # can surface it in the UI as an under-sized recommendation.
        return max(VM_CATALOG, key=lambda s: (s.vcpu, s.memory_gb))

    # Pick the smallest (by vcpu then memory) within the best family, with
    # tie-break on priority. This is the cheapest SKU that still fits.
    candidates.sort(key=lambda s: (family_score(s), s.vcpu, s.memory_gb, s.priority))
    return candidates[0]


def recommend_disk(size_gb: float, tier: str = "Premium SSD") -> DiskRec:
    """Map a provisioned GB to the smallest Azure managed disk SKU in tier."""
    size_gib = max(4, int(round(size_gb)))
    prefix = TIER_PREFIX.get(tier, "P")
    # rebuild ladder with chosen prefix (ladder sizes are identical per tier)
    for code, cap in DISK_LADDER:
        if cap >= size_gib:
            sku = code.replace("P", prefix, 1)
            return DiskRec(sku=sku, tier=tier, size_gib=cap, meter_name=f"{sku} LRS Disk")
    # larger than ladder top — return top
    code, cap = DISK_LADDER[-1]
    sku = code.replace("P", prefix, 1)
    return DiskRec(sku=sku, tier=tier, size_gib=cap, meter_name=f"{sku} LRS Disk")


def os_is_windows(os_str: str) -> bool:
    return "win" in (os_str or "").lower()
