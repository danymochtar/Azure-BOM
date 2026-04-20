"""Parse RVTools and generic inventory files into InventoryItem records."""
from __future__ import annotations

import io
import re
from typing import List, Tuple

import pandas as pd

from ..models import InventoryItem


NAME_ALIASES = {"name", "hostname", "host", "server", "vm", "vm name", "machine"}
VCPU_ALIASES = {"vcpu", "vcpus", "cpu", "cpus", "cores", "cpu cores", "# of cpus"}
MEMORY_ALIASES = {"memory_gb", "memory gb", "memory", "ram", "ram_gb", "ram gb", "mem", "mem_gb"}
STORAGE_ALIASES = {"storage_gb", "storage gb", "storage", "disk", "disk_gb", "disk gb", "capacity", "provisioned gb"}
OS_ALIASES = {"os", "operating system", "os name", "guest os"}
ENV_ALIASES = {"environment", "env", "tier", "stage"}
POWER_ALIASES = {"powerstate", "power state", "state"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _pick_col(columns: List[str], aliases: set) -> str | None:
    lookup = {_norm(c): c for c in columns}
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    # fuzzy contains
    for norm, original in lookup.items():
        for alias in aliases:
            if alias in norm:
                return original
    return None


def detect_format(xls_bytes: bytes, filename: str) -> str:
    """Return 'rvtools', 'excel', or 'csv'."""
    if filename.lower().endswith(".csv"):
        return "csv"
    try:
        xl = pd.ExcelFile(io.BytesIO(xls_bytes))
        sheet_names_lower = {s.lower() for s in xl.sheet_names}
        if "vinfo" in sheet_names_lower:
            return "rvtools"
    except Exception:
        pass
    return "excel"


def _parse_rvtools(xls_bytes: bytes, include_powered_off: bool) -> List[InventoryItem]:
    xl = pd.ExcelFile(io.BytesIO(xls_bytes))
    # find vInfo regardless of case
    vinfo_sheet = next((s for s in xl.sheet_names if s.lower() == "vinfo"), None)
    if vinfo_sheet is None:
        raise ValueError("RVTools file missing 'vInfo' sheet")
    df = xl.parse(vinfo_sheet)
    cols = list(df.columns)

    name_col = _pick_col(cols, {"vm"}) or _pick_col(cols, NAME_ALIASES)
    cpu_col = _pick_col(cols, {"cpus", "# of cpus"}) or _pick_col(cols, VCPU_ALIASES)
    mem_col = _pick_col(cols, {"memory"}) or _pick_col(cols, MEMORY_ALIASES)

    # Storage: prefer provisioned MiB/MB; convert to GB
    storage_col = None
    storage_divisor = 1.0
    for c in cols:
        n = _norm(c)
        if "provisioned" in n and ("mib" in n or "mb" in n):
            storage_col = c
            storage_divisor = 1024.0
            break
    if storage_col is None:
        for c in cols:
            n = _norm(c)
            if "provisioned" in n and ("gib" in n or "gb" in n):
                storage_col = c
                storage_divisor = 1.0
                break
    if storage_col is None:
        storage_col = _pick_col(cols, STORAGE_ALIASES)

    os_col = _pick_col(cols, {"os according to the configuration file", "os according to the vmware tools", "guest os"}) or _pick_col(cols, OS_ALIASES)
    power_col = _pick_col(cols, POWER_ALIASES)

    items: List[InventoryItem] = []
    for _, row in df.iterrows():
        power = str(row.get(power_col, "poweredOn")) if power_col else "poweredOn"
        if not include_powered_off and "off" in power.lower():
            continue
        try:
            vcpu = int(float(row.get(cpu_col, 0) or 0))
            mem_mb = float(row.get(mem_col, 0) or 0)
            storage_raw = float(row.get(storage_col, 0) or 0) if storage_col else 0.0
        except (TypeError, ValueError):
            continue
        if vcpu <= 0 and mem_mb <= 0:
            continue
        items.append(
            InventoryItem(
                name=str(row.get(name_col, "unnamed")),
                vcpu=max(vcpu, 1),
                memory_gb=round(mem_mb / 1024.0, 2) if mem_mb > 128 else round(mem_mb, 2),
                storage_gb=round(storage_raw / storage_divisor, 2),
                os=str(row.get(os_col, "Linux")) if os_col else "Linux",
                powerstate=power,
            )
        )
    return items


def _parse_generic(df: pd.DataFrame) -> List[InventoryItem]:
    cols = list(df.columns)
    name_col = _pick_col(cols, NAME_ALIASES)
    cpu_col = _pick_col(cols, VCPU_ALIASES)
    mem_col = _pick_col(cols, MEMORY_ALIASES)
    storage_col = _pick_col(cols, STORAGE_ALIASES)
    os_col = _pick_col(cols, OS_ALIASES)
    env_col = _pick_col(cols, ENV_ALIASES)

    required = {"name": name_col, "vcpu": cpu_col, "memory": mem_col}
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(
            f"Could not find required column(s): {', '.join(missing)}. "
            "Expected something like name/vcpu/memory_gb/storage_gb/os."
        )

    items: List[InventoryItem] = []
    for _, row in df.iterrows():
        try:
            vcpu = int(float(row.get(cpu_col, 0) or 0))
            mem = float(row.get(mem_col, 0) or 0)
        except (TypeError, ValueError):
            continue
        if vcpu <= 0 or mem <= 0:
            continue
        items.append(
            InventoryItem(
                name=str(row.get(name_col, "unnamed")),
                vcpu=vcpu,
                memory_gb=round(mem, 2),
                storage_gb=round(float(row.get(storage_col, 0) or 0), 2) if storage_col else 0.0,
                os=str(row.get(os_col, "Linux")) if os_col else "Linux",
                environment=str(row.get(env_col, "prod")) if env_col else "prod",
            )
        )
    return items


def parse_inventory(data: bytes, filename: str, include_powered_off: bool = False) -> Tuple[List[InventoryItem], str]:
    """Parse uploaded bytes; returns (items, detected_format)."""
    fmt = detect_format(data, filename)
    if fmt == "rvtools":
        return _parse_rvtools(data, include_powered_off), fmt
    if fmt == "csv":
        df = pd.read_csv(io.BytesIO(data))
        return _parse_generic(df), fmt
    df = pd.read_excel(io.BytesIO(data))
    return _parse_generic(df), fmt
