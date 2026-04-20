from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class InventoryItem:
    name: str
    vcpu: int
    memory_gb: float
    storage_gb: float
    os: str = "Linux"
    environment: str = "prod"
    powerstate: str = "poweredOn"
    notes: str = ""


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

    def to_row(self) -> dict:
        return asdict(self)
