"""Cheap, fast workload classifier (Claude Haiku 4.5) that maps uploads to
one of the five Azure pillars.

Pillars:
    infra_lift_shift     — per-VM inventory, 1:1 migration
    infra_modernization  — App Service / AKS / Container Apps / APIM / Front Door
    data_platform        — Fabric / Synapse / Cosmos / Azure SQL / ADLS / ADF / Event Hubs
    ai_application       — Azure OpenAI / AI Search / ML / GPU / Cognitive Services
    azure_security       — Defender / Sentinel / WAF / Private Link / Purview / PIM
    mixed                — multiple pillars are genuinely first-class
    unknown              — cannot classify confidently

Runs BEFORE the expensive Sonnet extractor so we only pay for VM extraction
when a pillar that `needs_vm_extraction=True` is ticked.
"""
from __future__ import annotations

from typing import List

import anthropic
from pydantic import BaseModel, Field

from .. import usage_tracker
from .content import prepare as _prepare_content


MODEL = "claude-haiku-4-5"


# Back-compat: saved prefs / old clients may still use the earlier strings.
_ALIAS_MAP = {
    "vm_inventory":      "infra_lift_shift",
    "app_modernization": "infra_modernization",
    "ai_ml":             "ai_application",
    "siem_soc":          "azure_security",
}


SYSTEM_PROMPT = """You are an Azure solution architect triaging an uploaded file
to decide which Azure pillar's cost assessment to run.

Inspect the file preview (sheet names / column headers / a few sample rows /
PDF pages / image / document text) and classify its workload intent into ONE
of these five pillars (or `mixed` / `unknown`):

- **infra_lift_shift** — per-VM / per-server inventory (RVTools vInfo, infra
  list, spec sheet with CPU/RAM/disk per host). Goal: right-size Azure VMs +
  managed disks + landing zone. Signals: columns like "vCPU / Memory /
  Provisioned MiB", rows per host, phrases like "migration", "lift-and-shift",
  powerstate fields.

- **infra_modernization** — application-layer modernization (containerize,
  move to PaaS). Goal: cost App Service / AKS / Container Apps / API
  Management / Front Door / ACR. Signals: "refactor", "containerize",
  "Kubernetes / AKS", "microservices", "App Service", "container registry",
  "API gateway", "Front Door", architecture diagrams WITHOUT per-host sizing.

- **data_platform** — data warehouse / lakehouse / streaming / analytics.
  Goal: Microsoft Fabric, Synapse, Databricks, Azure SQL DB, Cosmos DB, ADLS
  Gen2, Data Factory, Event Hubs, Power BI. Signals: "lakehouse",
  "DWU / capacity unit / RU/s", "ETL / ELT", "streaming", "ingestion pipeline",
  "dimensional model".

- **ai_application** — AI / ML application design. Goal: Azure OpenAI,
  AI Search, Azure ML workspace, GPU fine-tuning, Cognitive Services.
  Signals: "GPT / OpenAI", "RAG / retrieval-augmented", "vector DB /
  embedding", "fine-tune / PEFT", "prompt flow", "tokens per day",
  "A100 / H100 / ND / NC", "ML workspace".

- **azure_security** — security / SOC / governance. Goal: Defender suite,
  Microsoft Sentinel, Log Analytics ingestion + retention, WAF managed rules,
  Private Link, Purview, PIM. Signals: "Sentinel", "Defender for X",
  "CSPM", "EPS / GB/day logs", "retention days", "Zero Trust",
  "compliance / SOC 2 / PCI".

Rules:
1. Pick the MOST SPECIFIC single pillar when possible. Only use `mixed` when
   multiple pillars are clearly described as first-class (not "VMs plus some
   Defender" — that's one pillar with cross-cutting security).
2. `needs_vm_extraction=true` ONLY when the file contains per-VM rows that
   justify running the expensive Sonnet extractor. Setting it true triggers
   that pass — be conservative.
3. `suggested_components` is a flat list of component keys the assessment
   should include. Valid keys across all pillars:
   - Lift-and-shift / LZ: "vm_sizing", "managed_disks", "public_ip", "firewall",
     "bastion", "vpn_gw", "expressroute_circuit", "expressroute_gateway",
     "app_gateway_waf", "bandwidth_egress", "log_analytics", "key_vault",
     "recovery_vault", "ha", "bcdr"
   - Modernization: "app_service", "aks", "container_apps", "api_management",
     "front_door", "acr"
   - Data: "fabric", "synapse", "cosmos_db", "azure_sql_db", "adls_gen2",
     "adf", "event_hubs", "databricks", "power_bi"
   - AI: "azure_openai", "ai_search", "ml_workspace", "gpu_vm",
     "cognitive_services", "fine_tuning"
   - Security: "defender_cspm", "defender_servers_p2", "sentinel", "waf",
     "private_link", "purview", "pim"
4. `signals` short phrases quoting evidence ("column 'SIEM EPS'",
   "sheet 'Model Inventory'", "mentions 'RAG pipeline'").
5. Keep `summary` to one short sentence."""


class AssessmentProfile(BaseModel):
    workload_type: str = Field(
        description="One of: infra_lift_shift | infra_modernization | data_platform | ai_application | azure_security | mixed | unknown"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    complexity: str = Field(default="moderate", description="simple | moderate | complex")
    needs_vm_extraction: bool = Field(default=False)
    suggested_components: List[str] = Field(default_factory=list)
    signals: List[str] = Field(default_factory=list)
    summary: str = Field(default="")


def _resolve_alias(workload_type: str) -> str:
    return _ALIAS_MAP.get(workload_type, workload_type)


def classify(data: bytes, filename: str, api_key: str) -> AssessmentProfile:
    """Classify a workload from ANY supported file type."""
    uc = _prepare_content(data, filename, spreadsheet_preview_rows=5)

    user_content = list(uc.content_blocks) + [
        {
            "type": "text",
            "text": (
                f"File: {filename}\n"
                f"Summary: {uc.text_summary}\n\n"
                "Classify the workload based on the content above."
            ),
        }
    ]

    client = anthropic.Anthropic(api_key=api_key, max_retries=4)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user_content}],
        output_format=AssessmentProfile,
    )
    usage_tracker.record("Workload classifier", MODEL, getattr(response, "usage", None))

    profile = response.parsed_output
    # Resolve legacy strings from older prompts to new pillar keys
    profile.workload_type = _resolve_alias(profile.workload_type)
    return profile
