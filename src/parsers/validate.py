"""Key-info validator for extracted inventory.

Checks every InventoryItem has the four fields we need to price it 1:1
against Azure (vCPU, memory, storage, OS). Missing-field VMs are
surfaced in a remediation panel so the user can either attach another
doc or continue with an explicit assumption that's stamped into the
Cost Assumptions sheet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..models import InventoryItem


REQUIRED_FIELDS: List[str] = ["vcpu", "memory_gb", "storage_gb", "os"]


@dataclass
class MissingField:
    vm_name: str
    field: str
    current_value: object


@dataclass
class ValidationResult:
    total: int
    missing: List[MissingField]
    by_field: dict       # {field_name: count}
    has_db_info: bool    # soft check — True if any VM mentions SQL/Oracle/etc

    @property
    def ok(self) -> bool:
        return not self.missing

    @property
    def missing_by_vm(self) -> dict:
        out: dict = {}
        for m in self.missing:
            out.setdefault(m.vm_name, []).append(m.field)
        return out


def _is_empty(field: str, value: object) -> bool:
    if value is None:
        return True
    if field in ("vcpu",):
        try:
            return int(value) <= 0
        except (TypeError, ValueError):
            return True
    if field in ("memory_gb", "storage_gb"):
        try:
            return float(value) <= 0
        except (TypeError, ValueError):
            return True
    if field == "os":
        s = str(value or "").strip()
        return (not s) or s.lower() in ("unknown", "n/a", "tbd", "-")
    return False


def validate_inventory(items: List[InventoryItem]) -> ValidationResult:
    """Walk the extracted items and flag any with missing required fields."""
    missing: List[MissingField] = []
    by_field: dict = {f: 0 for f in REQUIRED_FIELDS}
    db_hits = 0
    for item in items:
        for fld in REQUIRED_FIELDS:
            value = getattr(item, fld, None)
            if _is_empty(fld, value):
                missing.append(MissingField(item.name or "(unnamed)", fld, value))
                by_field[fld] += 1
        blob = " ".join(
            str(x) for x in (item.os, item.notes, item.name) if x
        ).lower()
        if any(k in blob for k in ("sql server", "oracle", "mysql", "postgres", "mongodb", "mariadb")):
            db_hits += 1
    return ValidationResult(
        total=len(items),
        missing=missing,
        by_field=by_field,
        has_db_info=db_hits > 0,
    )


def apply_assumptions_for_missing(
    items: List[InventoryItem], result: ValidationResult,
) -> List[str]:
    """Mutate items in place to fill missing required fields with safe
    defaults (OS=Linux is the cheapest Azure default; minimal CPU/RAM/disk
    values get bumped to reasonable floors). Returns a list of assumption
    strings to stamp into the Cost Assumptions sheet.
    """
    assumptions: List[str] = []
    defaults = {"vcpu": 2, "memory_gb": 4.0, "storage_gb": 64.0, "os": "Linux"}
    for item in items:
        stamped = []
        for fld in REQUIRED_FIELDS:
            if _is_empty(fld, getattr(item, fld, None)):
                default = defaults[fld]
                setattr(item, fld, default)
                stamped.append(f"{fld}={default}")
        if stamped:
            assumptions.append(
                f"VM `{item.name}`: missing key-info defaulted "
                f"({', '.join(stamped)}) — Azure lowest-cost baseline. "
                f"Upload a doc with the actual values to tighten."
            )
    return assumptions
