"""AI-powered inventory parser using Claude Opus 4.7.

Given an arbitrary spreadsheet/CSV, Claude inspects the sheet names, column
headers, and a row sample, and returns a mapping spec. We then apply that
mapping locally to the full file — keeping token cost low even for huge files.

Uses:
- `claude-opus-4-7` (latest, most capable for structured reasoning)
- `adaptive` thinking (Opus 4.7 default) for robust column inference
- `effort: "medium"` — enough reasoning for this task, tight output
- Structured outputs via `messages.parse()` with a Pydantic schema
- Prompt caching on the system prompt + instructions so subsequent uploads
  within the 5-minute window only pay for the tail.
"""
from __future__ import annotations

import io
import json
from typing import List, Optional, Tuple

import anthropic
import pandas as pd
from pydantic import BaseModel, Field

from ..models import InventoryItem


MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """You are an expert data engineer specializing in IT infrastructure inventory.

You will be given a preview of a spreadsheet (sheet names, column headers, and a
handful of sample rows). Your job is to pick the sheet that contains the actual
VM/server inventory and map its columns to a normalized schema so a downstream
parser can process the full file.

Normalized fields:
- name: VM / server / host name (required)
- vcpu: number of virtual CPUs (integer)
- memory: RAM amount (the number itself — you also tell us the unit)
- storage: provisioned storage (the number itself — you also tell us the unit)
- os: operating system (e.g. "Windows Server 2019", "Red Hat 8", "Ubuntu 22.04")
- powerstate: power state column if present (poweredOn / poweredOff / unknown)
- environment: prod / dev / test / qa / staging

Rules:
1. Use EXACT column names as they appear in the headers list, including
   whitespace, casing, and any parenthesized hints.
2. If multiple sheets look plausible, pick the one with the most inventory-like
   rows (names + CPU + memory + storage). RVTools files typically use "vInfo".
3. If a field has no plausible column, set it to null. Do NOT invent columns.
4. Set memory_unit and storage_unit based on header hints like "MiB", "GB",
   "MB", "Memory (GB)", "Capacity MiB". If the column clearly holds values in
   one unit but the header is ambiguous, infer from the sample values (e.g.
   memory values in the thousands = MB; memory values under 512 = GB).
5. row_filter is an optional pandas-query-style expression to drop rows that
   are templates, powered-off replicas, or non-VMs. Leave as null if unsure.
6. Always return valid JSON matching the schema."""


class InventoryMapping(BaseModel):
    """Mapping spec produced by Claude."""

    sheet_name: str = Field(description="Sheet containing the inventory")
    name_col: str = Field(description="Column holding VM/host names")
    vcpu_col: Optional[str] = Field(default=None, description="Column holding vCPU count")
    memory_col: Optional[str] = Field(default=None, description="Column holding RAM")
    storage_col: Optional[str] = Field(default=None, description="Column holding provisioned storage")
    os_col: Optional[str] = Field(default=None, description="Column holding OS name")
    powerstate_col: Optional[str] = Field(default=None, description="Column holding power state")
    environment_col: Optional[str] = Field(default=None, description="Column holding environment tag")
    memory_unit: str = Field(default="GB", description="One of: MB, GB, MiB, GiB")
    storage_unit: str = Field(default="GB", description="One of: MB, GB, MiB, GiB, TB, TiB")
    row_filter: Optional[str] = Field(default=None, description="Pandas query to drop non-VM rows")
    notes: str = Field(default="", description="Caveats or confidence issues")


UNIT_TO_GB = {
    "mb": 1 / 1024.0, "mib": 1 / 1024.0,
    "gb": 1.0,        "gib": 1.0,
    "tb": 1024.0,     "tib": 1024.0,
    "kb": 1 / (1024.0 * 1024.0), "kib": 1 / (1024.0 * 1024.0),
}


def _read_file(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    """Return {sheet_name: df} for Excel; {"data": df} for CSV."""
    if filename.lower().endswith(".csv"):
        return {"data": pd.read_csv(io.BytesIO(data))}
    xl = pd.ExcelFile(io.BytesIO(data))
    return {name: xl.parse(name) for name in xl.sheet_names}


def _build_preview(sheets: dict[str, pd.DataFrame], sample_rows: int = 12) -> str:
    """Build a compact text preview of every sheet for Claude."""
    parts = []
    for name, df in sheets.items():
        parts.append(f"=== Sheet: {name} ===")
        parts.append(f"Rows: {len(df)} | Columns: {len(df.columns)}")
        parts.append("Headers: " + " | ".join(str(c) for c in df.columns))
        if len(df) > 0:
            sample = df.head(sample_rows).copy()
            # Stringify and truncate long cells
            sample = sample.astype(str).map(lambda v: v[:60] + "..." if len(v) > 60 else v)
            parts.append("Sample rows (first " + str(min(sample_rows, len(df))) + "):")
            parts.append(sample.to_csv(index=False))
        parts.append("")
    return "\n".join(parts)


def ai_generate_mapping(
    data: bytes,
    filename: str,
    api_key: str,
) -> Tuple[InventoryMapping, dict[str, pd.DataFrame]]:
    """Call Claude to produce a mapping spec for the uploaded file."""
    sheets = _read_file(data, filename)
    preview = _build_preview(sheets)

    client = anthropic.Anthropic(api_key=api_key)

    # Structured output via messages.parse + Pydantic schema.
    # Prompt caching on the system block so repeated uploads in a session are cheap.
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"File: {filename}\n\n"
                    f"Here is the preview of every sheet with headers and sample rows.\n\n"
                    f"{preview}\n\n"
                    "Produce the InventoryMapping for this file."
                ),
            }
        ],
        output_format=InventoryMapping,
    )
    return response.parsed_output, sheets


def apply_mapping(
    mapping: InventoryMapping,
    sheets: dict[str, pd.DataFrame],
    include_powered_off: bool = False,
) -> List[InventoryItem]:
    """Turn the AI-produced mapping into InventoryItem records."""
    if mapping.sheet_name not in sheets:
        # Tolerant fallback: case-insensitive match
        lookup = {k.lower(): k for k in sheets}
        key = lookup.get(mapping.sheet_name.lower())
        if not key:
            raise ValueError(
                f"AI chose sheet '{mapping.sheet_name}' but file has: {list(sheets)}"
            )
        df = sheets[key]
    else:
        df = sheets[mapping.sheet_name]

    if mapping.row_filter:
        try:
            df = df.query(mapping.row_filter)
        except Exception:
            pass  # filter is best-effort

    mem_factor = UNIT_TO_GB.get(mapping.memory_unit.lower(), 1.0)
    stor_factor = UNIT_TO_GB.get(mapping.storage_unit.lower(), 1.0)

    items: List[InventoryItem] = []
    for _, row in df.iterrows():
        name = row.get(mapping.name_col) if mapping.name_col else None
        if name is None or (isinstance(name, float) and pd.isna(name)) or str(name).strip() == "":
            continue

        try:
            vcpu = int(float(row.get(mapping.vcpu_col, 0) or 0)) if mapping.vcpu_col else 0
            mem_raw = float(row.get(mapping.memory_col, 0) or 0) if mapping.memory_col else 0.0
            stor_raw = float(row.get(mapping.storage_col, 0) or 0) if mapping.storage_col else 0.0
        except (TypeError, ValueError):
            continue

        power = str(row.get(mapping.powerstate_col, "poweredOn")) if mapping.powerstate_col else "poweredOn"
        if not include_powered_off and "off" in power.lower():
            continue

        if vcpu <= 0 and mem_raw <= 0:
            continue

        items.append(
            InventoryItem(
                name=str(name),
                vcpu=max(vcpu, 1),
                memory_gb=round(mem_raw * mem_factor, 2),
                storage_gb=round(stor_raw * stor_factor, 2),
                os=str(row.get(mapping.os_col, "Linux")) if mapping.os_col else "Linux",
                environment=str(row.get(mapping.environment_col, "prod")) if mapping.environment_col else "prod",
                powerstate=power,
            )
        )
    return items


def ai_parse_inventory(
    data: bytes,
    filename: str,
    api_key: str,
    include_powered_off: bool = False,
) -> Tuple[List[InventoryItem], InventoryMapping]:
    """One-shot helper: preview → AI mapping → apply. Returns (items, mapping)."""
    mapping, sheets = ai_generate_mapping(data, filename, api_key)
    items = apply_mapping(mapping, sheets, include_powered_off=include_powered_off)
    return items, mapping
