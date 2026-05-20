from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class DiskItem:
    """One managed disk attached to a VM. RVTools / Azure Migrate
    exports usually have per-disk rows (Hard Disk 1 / 2 / 3, OS Disk,
    Data Disk, etc.). Modelling them individually lets the BOM keep
    the customer's actual disk topology instead of collapsing to one
    aggregated disk per VM."""
    label: str                  # "OS Disk", "Hard Disk 1", "C:", "Data Disk 2"
    size_gb: float
    # Optional per-disk tier override. Most callers leave this None and
    # let the builder route via `recommend_disk_tier(vm)` — but if the
    # source export tags a disk as e.g. "Premium SSD" explicitly,
    # honor it.
    tier: Optional[str] = None


@dataclass
class InventoryItem:
    name: str
    vcpu: int
    memory_gb: float
    storage_gb: float           # aggregated total; primary when `disks` empty
    os: str = "Linux"
    environment: str = "prod"
    powerstate: str = "poweredOn"
    notes: str = ""
    # Per-disk breakdown. Empty list → builder falls back to a single
    # disk sized off `storage_gb` (legacy behaviour, preserves
    # backward compat with parsers that didn't populate this field).
    disks: List[DiskItem] = field(default_factory=list)


@dataclass
class BomLine:
    category: str
    resource: str
    sku: str
    meter: str
    region: str
    quantity: float
    unit: str
    unit_price: float
    monthly_cost: float
    currency: str = "USD"
    source: str = ""
    product_id: str = ""
    sku_id: str = ""
    meter_id: str = ""
    # Azure Pricing Calculator template fields
    service_name: str = ""   # e.g., "Virtual Machines", "Managed Disks", "Azure Firewall"
    custom_name: str = ""    # per-line tag: app name / workload / hub-role
    # Number of distinct identical resources represented by this line —
    # e.g. 4 for "Virtual Machine D4s v5 (Linux) x4". Lives in its own
    # column so reviewers don't have to parse "x4" out of the resource
    # string. Default 1 for single-resource lines (firewall, log
    # analytics, key vault, etc.). The `quantity` column is still the
    # per-resource usage figure (730 hours, 100 GB, etc.).
    resource_count: int = 1
    # Friendly billing-term label rendered as its own column in the
    # Results table — "PAYG" / "SP 1Y" / "SP 3Y" / "RI 1Y" / "RI 3Y".
    # Defaults to empty for non-compute lines (storage, networking,
    # SaaS) where the concept doesn't apply.
    billing_term: str = "PAYG"
    # Audit trail — written to the Cost Assumptions sheet of the exported
    # Excel. Builders with rich context (AHB %, tiered bandwidth, SQL DB
    # discount, Fabric rationale, static DevTools rate, multi-file merge
    # notes) set this directly; the export's derive_assumption() helper
    # fills a sensible default for any line left blank.
    assumption: str = ""

    def to_row(self) -> dict:
        return asdict(self)
