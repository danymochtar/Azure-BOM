"""AI-powered inventory parser using Claude Opus 4.7.

Two modes:

- **Direct extraction** (small files, <=500 rows total): Claude reads the whole
  file and produces a list of normalized VMs directly. Handles pivoted/key-value
  layouts, free-text specs ("2 x Xeon Gold 6346 (16 Cores)",
  "D:4 x 1.9TB SSD, RAID 10"), and arbitrary structures.

- **Column mapping** (large files): Claude reads a sample, returns a mapping
  spec, and we apply it locally to the full dataframe — keeping token cost
  flat regardless of row count.

Uses structured outputs via `messages.parse()` + Pydantic, and prompt caching
on the system block so repeated uploads within 5 minutes are cheap.
"""
from __future__ import annotations

import io
from typing import List, Optional, Tuple

import anthropic
import pandas as pd
from pydantic import BaseModel, Field

from ..models import InventoryItem


# Sonnet 4.6 is the sweet spot for structured inventory extraction: strong
# reasoning, higher per-minute token limits than Opus on most tiers, and
# ~40% of the input/output cost. Callers can override with `model=...`.
MODEL = "claude-sonnet-4-6"

# Send at most this many rows per sheet into the direct-extract call. Keeps
# a single upload well inside typical 30-50K tokens/min rate limits even if
# the sheet is large. Larger files fall through to the mapping path which
# only sends a 12-row sample.
DIRECT_MODE_ROW_CAP = 200


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ExtractedItem(BaseModel):
    """A VM/server Claude extracted directly from the file."""

    name: str = Field(description="Server/VM/host name")
    vcpu: int = Field(description="Virtual CPUs. If only physical CPU specs are given (e.g. '2 x 16 cores'), compute total cores = sockets × cores_per_socket.")
    memory_gb: float = Field(description="RAM in GB. Convert MB/MiB to GB by dividing by 1024.")
    storage_gb: float = Field(description="Total provisioned storage in GB across all disks. Convert TB to GB (multiply by 1024).")
    os: str = Field(default="Linux", description="OS family, e.g. 'Windows Server 2019', 'Red Hat Enterprise Linux 8', 'Ubuntu 22.04'")
    environment: str = Field(default="prod", description="prod | dev | test | qa | staging (best guess)")
    powerstate: str = Field(default="poweredOn", description="poweredOn | poweredOff | unknown")
    workload: str = Field(default="general", description="Inferred role: 'sql' | 'web' | 'app' | 'cache' | 'queue' | 'file' | 'ad' | 'general'")
    recommended_azure_service: str = Field(
        default="Azure Virtual Machine",
        description="Target Azure service under the chosen migration strategy (e.g. 'Azure Virtual Machine', 'Azure SQL Managed Instance', 'Azure App Service', 'Azure Cache for Redis').",
    )
    notes: str = Field(default="", description="Any caveats about the extraction (e.g. 'RAID 10 assumed usable = raw/2', refactor effort for PaaS).")


class DirectExtraction(BaseModel):
    """Claude-produced list of VMs, used for small files."""

    items: List[ExtractedItem] = Field(description="Every server/VM found in the file. Do not invent or skip.")
    summary: str = Field(default="", description="One-sentence summary of the file's layout and what was extracted.")


class InventoryMapping(BaseModel):
    """Mapping spec for large tabular files (column-to-field mapping applied locally)."""

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


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

DIRECT_SYSTEM_PROMPT = """You are an expert data engineer specializing in IT infrastructure inventory discovery.

You will be given the FULL contents of a spreadsheet or CSV file. Your job is to
find every server/VM in the file and return a normalized list. The file may be:
- A tabular row-per-VM inventory (RVTools vInfo, plain CSV with headers)
- A pivoted/key-value layout where each server occupies multiple rows and
  attributes like "Processor", "RAM (GB)", "Disk (GB)" appear in one column
  with values in the next
- A free-form document with tables scattered across sheets
- Any combination of the above

Extraction rules:

- name: the server/VM/host name (required). If servers are identified by
  sections like "ERP1", "ERP2", use those as the name.
- vcpu: total virtual CPUs available to the VM.
  * If the file states vCPUs directly, use that.
  * If only physical CPU specs like "2 x Intel Xeon Gold 6346 (16 Cores, 3.1Ghz)"
    are given, compute total physical cores = sockets × cores_per_socket
    (e.g. 2 × 16 = 32). For a virtualized host, that equals usable vCPUs
    (ignoring hyperthreading unless explicitly stated).
- memory_gb: total RAM in GB. Convert MB/MiB by dividing by 1024.
- storage_gb: sum of all provisioned storage across every disk, in GB.
  * Parse free-text specs like "c: 2 X256GB SSD RAID1" or
    "D:4 x 1.9TB SSD, RAID 10" — extract the raw capacity per disk, multiply
    by the count, sum across disks.
  * RAID accounting: for RAID 1/10 (mirrors), usable = raw / 2. For RAID 5,
    usable = raw × (n-1)/n. For RAID 0 / JBOD / no RAID, usable = raw.
    For ambiguous cases prefer USABLE over RAW and explain in notes.
  * Convert TB → GB by multiplying by 1024.
  * Always return a single number (sum of all volumes).
- os: OS family/version text as stated in the source.
- powerstate: "poweredOn", "poweredOff", or "unknown".

Hard rules:
1. Return EVERY server present. Do not invent, do not skip.
2. Never return 0 for vcpu/memory/storage if the file contains enough
   information to compute a value — always compute and document assumptions
   in `notes`.
3. Non-server rows (firewalls, support plans, ISP details, SLA tables, etc.)
   must be excluded — not returned as items.
4. Output must strictly match the schema."""


MAPPING_SYSTEM_PROMPT = """You are an expert data engineer specializing in IT infrastructure inventory.

You will be given a preview of a spreadsheet (all sheets, all column headers,
and a row sample). Your job is to pick the sheet that contains the actual
VM/server inventory and map its columns to a normalized schema so a downstream
parser can apply the mapping to the full file.

Use EXACT column names as they appear in the headers, including whitespace,
casing, and parenthesized hints. Infer memory_unit and storage_unit from
header hints ("Memory MiB", "Capacity GB"). If a field has no plausible
column, set it to null — do not invent."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UNIT_TO_GB = {
    "mb": 1 / 1024.0, "mib": 1 / 1024.0,
    "gb": 1.0,        "gib": 1.0,
    "tb": 1024.0,     "tib": 1024.0,
    "kb": 1 / (1024.0 * 1024.0), "kib": 1 / (1024.0 * 1024.0),
}


def _truncate_cell(v, limit: int = 80) -> str:
    """Convert any cell value to a short string safely."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(v)
    return s[:limit] + "..." if len(s) > limit else s


def _read_file(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    """Return {sheet_name: df} for Excel; {"data": df} for CSV."""
    if filename.lower().endswith(".csv"):
        return {"data": pd.read_csv(io.BytesIO(data))}
    xl = pd.ExcelFile(io.BytesIO(data))
    return {name: xl.parse(name) for name in xl.sheet_names}


def _build_preview(sheets: dict[str, pd.DataFrame], sample_rows: Optional[int]) -> str:
    """Build a text preview. If sample_rows is None, include all rows."""
    parts = []
    for name, df in sheets.items():
        parts.append(f"=== Sheet: {name} ===")
        parts.append(f"Rows: {len(df)} | Columns: {len(df.columns)}")
        parts.append("Headers: " + " | ".join(str(c) for c in df.columns))
        if len(df) > 0:
            head = df if sample_rows is None else df.head(sample_rows)
            rows_out = [",".join(str(c) for c in head.columns)]
            for _, row in head.iterrows():
                rows_out.append(",".join(_truncate_cell(v) for v in row.tolist()))
            tag = "All rows" if sample_rows is None else f"first {min(sample_rows, len(df))}"
            parts.append(f"Rows ({tag}):")
            parts.append("\n".join(rows_out))
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Direct extraction (small files)
# ---------------------------------------------------------------------------

def ai_extract_direct(
    sheets: dict[str, pd.DataFrame],
    filename: str,
    api_key: str,
    strategy_hint: str = "",
    model: str = MODEL,
) -> DirectExtraction:
    """Claude reads the whole file and produces a list of VMs."""
    preview = _build_preview(sheets, sample_rows=None)
    # max_retries > default so transient 429s back off and retry automatically
    client = anthropic.Anthropic(api_key=api_key, max_retries=4)

    user_content = (
        f"File: {filename}\n\n"
        f"Here is the full contents of every sheet.\n\n"
        f"{preview}\n\n"
    )
    if strategy_hint:
        user_content += f"Migration strategy guidance:\n{strategy_hint}\n\n"
    user_content += "Extract every server/VM and return the normalized list."

    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=[
            {"type": "text", "text": DIRECT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=DirectExtraction,
    )
    return response.parsed_output


def _to_inventory_items(extraction: DirectExtraction, include_powered_off: bool) -> List[InventoryItem]:
    items: List[InventoryItem] = []
    for x in extraction.items:
        if not include_powered_off and "off" in (x.powerstate or "").lower():
            continue
        if not x.name or (x.vcpu <= 0 and x.memory_gb <= 0):
            continue
        combined_notes = x.notes or ""
        if x.recommended_azure_service and x.recommended_azure_service != "Azure Virtual Machine":
            tag = f"PaaS target: {x.recommended_azure_service}"
            combined_notes = f"{tag}. {combined_notes}".strip()
        items.append(
            InventoryItem(
                name=x.name,
                vcpu=max(int(x.vcpu), 1),
                memory_gb=float(x.memory_gb),
                storage_gb=float(x.storage_gb),
                os=x.os or "Linux",
                environment=x.environment or "prod",
                powerstate=x.powerstate or "poweredOn",
                notes=combined_notes,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Column mapping (large tabular files)
# ---------------------------------------------------------------------------

def ai_generate_mapping(
    data: bytes,
    filename: str,
    api_key: str,
    model: str = MODEL,
) -> Tuple[InventoryMapping, dict[str, pd.DataFrame]]:
    sheets = _read_file(data, filename)
    preview = _build_preview(sheets, sample_rows=12)
    client = anthropic.Anthropic(api_key=api_key, max_retries=4)

    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=[
            {"type": "text", "text": MAPPING_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"File: {filename}\n\n"
                    f"Preview of every sheet with headers and sample rows:\n\n"
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
    if mapping.sheet_name not in sheets:
        lookup = {k.lower(): k for k in sheets}
        key = lookup.get(mapping.sheet_name.lower())
        if not key:
            raise ValueError(
                f"AI chose sheet '{mapping.sheet_name}' but file has: {list(sheets)}"
            )
        df = sheets[key]
    else:
        df = sheets[mapping.sheet_name]

    # NOTE: We deliberately ignore mapping.row_filter. pandas.query() is
    # eval-based; letting an AI-produced expression run against a DataFrame
    # is a code-execution foothold if the preview is prompt-injected. Rows
    # without a name are dropped below anyway, which covers the common
    # "skip header/template rows" case.

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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def ai_parse_inventory(
    data: bytes,
    filename: str,
    api_key: str,
    include_powered_off: bool = False,
    strategy_hint: str = "",
):
    """One-shot parser. Returns (items, mode_str, spec) where:
      - mode_str is "direct" or "mapping"
      - spec is the DirectExtraction or InventoryMapping used
    """
    sheets = _read_file(data, filename)
    total_rows = sum(len(df) for df in sheets.values())

    if total_rows <= DIRECT_MODE_ROW_CAP:
        extraction = ai_extract_direct(sheets, filename, api_key, strategy_hint=strategy_hint)
        items = _to_inventory_items(extraction, include_powered_off=include_powered_off)
        return items, "direct", extraction

    mapping, _ = ai_generate_mapping(data, filename, api_key)
    items = apply_mapping(mapping, sheets, include_powered_off=include_powered_off)
    return items, "mapping", mapping
