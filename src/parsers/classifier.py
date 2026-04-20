"""Cheap, fast workload classifier using Claude Haiku 4.5.

Runs BEFORE the expensive VM-extraction pass so we can short-circuit:
- If the upload is a SIEM/SOC doc, skip VM extraction and route to the
  Sentinel calculator (saves Opus-sized tokens on every upload).
- If it's an AI/ML design, skip VM extraction and route to the Azure
  OpenAI / ML calculator.
- If it's a data platform design, route to the Fabric/Synapse/Cosmos calc.
- If it's a VM inventory (RVTools, server list, infra spec), run the
  regular Opus extraction.
- If it's mixed, run extraction AND surface additional workloads.

Input: sheet names + headers + up to 5 sample rows (small, cheap).
Output: structured AssessmentProfile (Pydantic-validated).

Token footprint: ~1-2K input + ~200 output on Haiku = ~$0.001/upload.
"""
from __future__ import annotations

from typing import List, Optional

import anthropic
import pandas as pd
from pydantic import BaseModel, Field

from .ai_inventory import _read_file, _truncate_cell
from .. import usage_tracker


MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """You are an Azure solution architect triaging an uploaded file
to decide what kind of Azure Cost Assessment to run.

Inspect the file preview (sheet names, column headers, a few sample rows)
and classify its workload intent. Options:

- vm_inventory: RVTools, server list, infra spec with CPU/RAM/disk per VM.
  The goal is to size Azure VMs + disks + LZ.
- siem_soc: security monitoring / Sentinel / SIEM design, events per second,
  log sources, retention. Goal: Sentinel + Log Analytics + Defender pricing.
- data_platform: data warehouse / lakehouse / analytics workload —
  Synapse, Databricks, Fabric, Snowflake, BigQuery migration, Cosmos DB,
  data pipelines.
- ai_ml: AI/ML use case — Azure OpenAI token volume, model choices, ML
  workspace, vector DB, GPU VMs for training, RAG architecture.
- app_modernization: web / API / microservice modernization, App Service,
  AKS, Container Apps, Functions.
- mixed: genuinely multiple categories (e.g. an architecture doc that
  lists both VMs AND data platform AND AI components).
- unknown: cannot classify confidently.

Rules:
1. Favor the more SPECIFIC type over 'mixed'. Only return 'mixed' when
   multiple top-level categories are clearly present.
2. `needs_vm_extraction` = true ONLY if the file has a table/section of
   per-VM/per-server rows with CPU/RAM/disk. Setting it true triggers an
   expensive Opus pass — be conservative.
3. `suggested_components` is a flat list of component keys the assessment
   should include. Valid keys:
   - VM/disk: "vm_sizing", "managed_disks"
   - Landing zone: "public_ip", "firewall", "bastion", "vpn_gw",
     "expressroute_circuit", "expressroute_gateway", "app_gateway_waf",
     "log_analytics", "key_vault", "recovery_vault"
   - Security: "defender_cspm", "defender_servers_p2", "sentinel"
   - HA / BCDR: "ha", "bcdr"
   - Specialized: "azure_openai", "fabric", "synapse", "databricks",
     "cosmos_db", "aks", "app_service"
4. `signals` should be short phrases quoting evidence ("column 'SIEM EPS'",
   "sheet 'Model Inventory'", "cell mentions 'RAG pipeline'").
5. Keep `summary` to one short sentence."""


class AssessmentProfile(BaseModel):
    workload_type: str = Field(description="vm_inventory | siem_soc | data_platform | ai_ml | app_modernization | mixed | unknown")
    confidence: float = Field(description="0.0-1.0", ge=0.0, le=1.0)
    complexity: str = Field(default="moderate", description="simple | moderate | complex")
    needs_vm_extraction: bool = Field(default=False, description="True if file has per-VM rows that justify the Opus extraction pass.")
    suggested_components: List[str] = Field(default_factory=list, description="Keys from the valid list in the system prompt.")
    signals: List[str] = Field(default_factory=list, description="Short evidence phrases from the file.")
    summary: str = Field(default="", description="One-sentence summary.")


def _build_short_preview(sheets: dict[str, pd.DataFrame], max_rows: int = 5) -> str:
    parts = []
    for name, df in sheets.items():
        parts.append(f"=== Sheet: {name} ({len(df)} rows × {len(df.columns)} cols) ===")
        parts.append("Headers: " + " | ".join(str(c)[:40] for c in df.columns))
        if len(df) > 0:
            head = df.head(max_rows)
            for _, row in head.iterrows():
                cells = [_truncate_cell(v, limit=40) for v in row.tolist()]
                parts.append("  | ".join(cells))
        parts.append("")
    return "\n".join(parts)


def classify(
    data: bytes,
    filename: str,
    api_key: str,
) -> AssessmentProfile:
    sheets = _read_file(data, filename)
    preview = _build_short_preview(sheets)

    client = anthropic.Anthropic(api_key=api_key, max_retries=4)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"File: {filename}\n\n"
                    f"Compact preview (headers + first 5 rows):\n\n"
                    f"{preview}\n\n"
                    "Classify the workload."
                ),
            }
        ],
        output_format=AssessmentProfile,
    )
    usage_tracker.record("Workload classifier", MODEL, getattr(response, "usage", None))
    return response.parsed_output
