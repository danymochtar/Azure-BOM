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


def recommend_vm(item: InventoryItem, headroom: float = 1.3, prefer_burstable_cpu: int = 2) -> VmSku:
    """Return the smallest SKU that satisfies required vCPU*headroom and memory*headroom.

    - If item has <=prefer_burstable_cpu vCPU, burstable B-series is allowed.
    - High memory/CPU ratios (>= 6) steer toward memory-optimized E-series.
    """
    req_cpu = max(1, int(round(item.vcpu * headroom)))
    req_mem = round(item.memory_gb * headroom, 2)
    ratio = _memory_to_cpu_ratio(item)

    def fits(sku: VmSku) -> bool:
        return sku.vcpu >= req_cpu and sku.memory_gb >= req_mem

    candidates = [s for s in VM_CATALOG if fits(s)]
    if not candidates:
        # Fall back: return the largest available so caller can flag it
        return max(VM_CATALOG, key=lambda s: (s.vcpu, s.memory_gb))

    # Family preference based on workload shape
    def family_score(sku: VmSku) -> int:
        if sku.family == "burstable":
            return 0 if item.vcpu <= prefer_burstable_cpu and item.memory_gb <= 32 else 3
        if sku.family == "memory":
            return 0 if ratio >= 6 else 2
        # general
        return 1

    # Pick the smallest (by vcpu then memory) within the best family, with tie-break on priority
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
