"""Curated Azure VM SKU catalog used for right-sizing.

Kept intentionally small and focused on the most common general-purpose (D),
memory-optimized (E), and burstable (B) families in v5 generation. The mapper
picks the smallest SKU that satisfies required vCPU and memory after applying
the headroom factor, preferring lower family priority numbers.

Add more SKUs here when you need broader coverage. Names MUST match the ARM
size name (e.g. "Standard_D4s_v5") since those are used to resolve retail
prices via the Azure Retail Prices API.
"""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class VmSku:
    arm_name: str          # e.g. "Standard_D4s_v5"
    display: str           # e.g. "D4s v5"
    family: str            # "general", "memory", "burstable"
    vcpu: int
    memory_gb: float
    # meter name fragment used when searching Retail Prices API
    meter_match: str       # e.g. "D4s v5"
    priority: int = 10     # lower = preferred for tie-break


# Burstable (best fit for small, low-utilization workloads)
BURSTABLE: List[VmSku] = [
    VmSku("Standard_B2s",      "B2s",      "burstable", 2,  4,   "B2s",      priority=5),
    VmSku("Standard_B2ms",     "B2ms",     "burstable", 2,  8,   "B2ms",     priority=5),
    VmSku("Standard_B4ms",     "B4ms",     "burstable", 4,  16,  "B4ms",     priority=5),
    VmSku("Standard_B8ms",     "B8ms",     "burstable", 8,  32,  "B8ms",     priority=5),
    VmSku("Standard_B16ms",    "B16ms",    "burstable", 16, 64,  "B16ms",    priority=5),
]

# General-purpose Dsv5 (premium SSD capable)
GENERAL: List[VmSku] = [
    VmSku("Standard_D2s_v5",   "D2s v5",   "general", 2,   8,   "D2s v5"),
    VmSku("Standard_D4s_v5",   "D4s v5",   "general", 4,   16,  "D4s v5"),
    VmSku("Standard_D8s_v5",   "D8s v5",   "general", 8,   32,  "D8s v5"),
    VmSku("Standard_D16s_v5",  "D16s v5",  "general", 16,  64,  "D16s v5"),
    VmSku("Standard_D32s_v5",  "D32s v5",  "general", 32,  128, "D32s v5"),
    VmSku("Standard_D48s_v5",  "D48s v5",  "general", 48,  192, "D48s v5"),
    VmSku("Standard_D64s_v5",  "D64s v5",  "general", 64,  256, "D64s v5"),
]

# Memory-optimized Esv5
MEMORY: List[VmSku] = [
    VmSku("Standard_E2s_v5",   "E2s v5",   "memory",  2,   16,  "E2s v5"),
    VmSku("Standard_E4s_v5",   "E4s v5",   "memory",  4,   32,  "E4s v5"),
    VmSku("Standard_E8s_v5",   "E8s v5",   "memory",  8,   64,  "E8s v5"),
    VmSku("Standard_E16s_v5",  "E16s v5",  "memory",  16,  128, "E16s v5"),
    VmSku("Standard_E32s_v5",  "E32s v5",  "memory",  32,  256, "E32s v5"),
    VmSku("Standard_E48s_v5",  "E48s v5",  "memory",  48,  384, "E48s v5"),
    VmSku("Standard_E64s_v5",  "E64s v5",  "memory",  64,  512, "E64s v5"),
]

VM_CATALOG: List[VmSku] = BURSTABLE + GENERAL + MEMORY
