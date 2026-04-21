"""Pre-assessment Excel templates — one per pillar.

Each `build_*_template()` returns bytes of an `.xlsx` workbook with:
- An **Instructions** sheet (how to fill, which fields matter, any
  required cells like "Power BI users").
- One or more **data** sheets with headers matching the pillar's
  input-dict keys and 2-3 example rows so a user can copy the pattern.

The infra template is intentionally NOT strict — users can upload any
doc format (RVTools, Azure Migrate, PDF, etc.) and the classifier +
extractor will handle it. The template is a convenience, not a
requirement.
"""
from __future__ import annotations

import io
from typing import List, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


_HEADER_FILL = PatternFill("solid", fgColor="0078D4")  # Azure blue
_HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
_TITLE_FONT = Font(name="Calibri", bold=True, size=14)
_NOTE_FONT = Font(name="Calibri", italic=True, size=10, color="555555")


def _new_book() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    return wb


def _write_sheet(wb, name: str, headers: List[str], rows: List[List]) -> None:
    ws = wb.create_sheet(name)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = _HEADER_FONT
        c.fill = _HEADER_FILL
        c.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = max(14, len(h) + 3)
    for r, row in enumerate(rows, start=2):
        for c, val in enumerate(row, 1):
            ws.cell(row=r, column=c, value=val)
    ws.freeze_panes = "A2"


def _write_instructions(wb, title: str, lines: List[str], required: List[str] = None) -> None:
    ws = wb.create_sheet("Instructions", 0)
    ws["A1"] = title
    ws["A1"].font = _TITLE_FONT
    ws.column_dimensions["A"].width = 110
    row = 3
    for line in lines:
        ws.cell(row=row, column=1, value=line)
        row += 1
    if required:
        row += 1
        ws.cell(row=row, column=1, value="Required fields (must be filled):")
        ws.cell(row=row, column=1).font = Font(bold=True)
        row += 1
        for r in required:
            ws.cell(row=row, column=1, value=f"  • {r}")
            ws.cell(row=row, column=1).font = _NOTE_FONT
            row += 1


def _to_bytes(wb: openpyxl.Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Infra lift-and-shift
# ---------------------------------------------------------------------------

def build_infra_inventory_template() -> bytes:
    wb = _new_book()
    _write_instructions(
        wb,
        "Infra Lift-and-Shift inventory template",
        [
            "Fill the `VMs` sheet with one row per server / VM you want to migrate.",
            "",
            "You do NOT have to use this template — the app also accepts RVTools",
            "exports, Azure Migrate reports, or any PDF/doc. This template is just",
            "a clean starting point.",
            "",
            "Key fields the cost engine needs: vCPU, memory (GB), storage (GB), OS.",
            "If any VM is missing those four fields, the app will prompt you to",
            "upload an additional doc OR continue with an explicit 'assumed Linux'",
            "baseline stamped in the Cost Assumptions sheet.",
            "",
            "DB info (SQL Server / PostgreSQL / MySQL edition + version) is optional.",
            "When absent, the app assumes no separate DB license is needed.",
        ],
        required=["name", "vcpu", "memory_gb", "storage_gb", "os"],
    )
    _write_sheet(
        wb, "VMs",
        ["name", "vcpu", "memory_gb", "storage_gb", "os", "environment", "power_state", "notes"],
        [
            ["web01",  2,  4,   80,   "Ubuntu 22.04",              "prod", "poweredOn", ""],
            ["app01",  4,  16,  200,  "Red Hat Enterprise 8",      "prod", "poweredOn", "Tomcat 9"],
            ["db01",   8,  64,  1000, "Windows Server 2019",       "prod", "poweredOn", "SQL Server 2019 Enterprise"],
            ["cache1", 2,  8,   50,   "Ubuntu 22.04",              "prod", "poweredOn", "Redis 7"],
        ],
    )
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# 2. AI Application & Development
# ---------------------------------------------------------------------------

def build_ai_template() -> bytes:
    wb = _new_book()
    _write_instructions(
        wb,
        "AI Application pre-assessment template",
        [
            "Fill only the rows you need — zero = skip.",
            "",
            "Tips:",
            "  • OpenAI + Foundry token volumes are in 1K tokens/day. 1M users × 5 queries × 2K input ≈ 10M tokens/day = 10,000 × 1K.",
            "  • AI Search replicas × partitions drives cost; 1×1 is typical dev, 3×3 production.",
            "  • GPU VMs: enter hours/month (e.g. 200 for fine-tuning burst, 730 for always-on).",
            "  • Cognitive Services: enter raw transactions/pages/hours — the app converts to billable units.",
        ],
    )
    _write_sheet(
        wb, "OpenAI Models",
        ["model_key", "input_1k_per_day", "output_1k_per_day"],
        [
            ["gpt-4o",                1000, 200],
            ["gpt-4.1-mini",          5000, 800],
            ["gpt-4o-mini",           0,    0],
            ["o3-mini",               0,    0],
            ["text-embedding-3-small", 500, 0],
        ],
    )
    _write_sheet(
        wb, "Foundry Models",
        ["model_key", "input_1k_per_day", "output_1k_per_day"],
        [
            ["llama-3.1-70b", 0, 0],
            ["mistral-large-2", 0, 0],
            ["deepseek-r1", 0, 0],
            ["phi-4", 0, 0],
        ],
    )
    _write_sheet(
        wb, "AI Search",
        ["tier", "replicas", "partitions"],
        [["standard", 1, 1]],
    )
    _write_sheet(
        wb, "GPU VMs",
        ["sku", "count", "hours_per_month", "os"],
        [
            ["Standard_NC24ads_A100_v4", 1, 200, "Linux"],
            ["Standard_NC40ads_H100_v5", 0, 0,   "Linux"],
        ],
    )
    _write_sheet(
        wb, "Cognitive Services",
        ["service_key", "monthly_qty", "unit_hint"],
        [
            ["vision",                10_000, "transactions/month (raw, divided by 1000 internally)"],
            ["document_intelligence", 2_000,  "pages/month (raw)"],
            ["content_safety",        50_000, "transactions/month (raw)"],
            ["translator",            1.5,    "million characters/month"],
            ["speech_stt",            40,     "audio hours/month"],
        ],
    )
    _write_sheet(
        wb, "Fine-tuning",
        ["model_key", "training_1k_tokens"],
        [["gpt-4o-mini", 0]],
    )
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# 3. Data Platform (Fabric-first)
# ---------------------------------------------------------------------------

def build_data_template() -> bytes:
    wb = _new_book()
    _write_instructions(
        wb,
        "Data Platform pre-assessment template (Fabric-first)",
        [
            "Microsoft Fabric is the DEFAULT data-platform recommendation.",
            "Databricks is optional — tick it only if you need dedicated Spark",
            "compute or have an existing Databricks estate.",
            "",
            "KEY INPUT: Power BI users count. This drives the recommended",
            "Fabric F-SKU:",
            "   < 25  users → F2",
            "   25-50       → F4",
            "   50-100      → F8",
            "   100-200     → F16",
            "   200-500     → F32",
            "   500-2,000   → F64",
            "   2,000+      → F128 or higher",
            "",
            "Fill only the services you need; leave the rest at 0 / none.",
        ],
        required=["pbi_users (on this Instructions sheet — type the number in cell B27)"],
    )
    ws = wb["Instructions"]
    ws["A27"] = "Power BI users (required):"
    ws["A27"].font = Font(bold=True)
    ws["B27"] = 0
    _write_sheet(
        wb, "Fabric Cosmos Synapse",
        ["fabric_sku", "cosmos_ru_per_second", "synapse_dwu"],
        [["F32", 0, 0]],
    )
    _write_sheet(
        wb, "Azure SQL DB",
        ["tier", "vcores", "storage_gb", "zone_redundant"],
        [["gp", 4, 200, False]],
    )
    _write_sheet(
        wb, "Azure SQL MI",
        ["tier", "vcores", "storage_gb"],
        [["gp", 4, 200]],
    )
    _write_sheet(
        wb, "PostgreSQL Flexible",
        ["tier", "vcores", "storage_gb", "ha_enabled"],
        [["general_purpose", 4, 200, False]],
    )
    _write_sheet(
        wb, "MySQL Flexible",
        ["tier", "vcores", "storage_gb", "ha_enabled"],
        [["general_purpose", 4, 200, False]],
    )
    _write_sheet(
        wb, "Redis Cache",
        ["tier", "sku"],
        [["standard", "C1"]],
    )
    _write_sheet(
        wb, "Azure Files",
        ["tier", "storage_gb"],
        [["standard_grs", 1000]],
    )
    _write_sheet(
        wb, "ADLS Gen2",
        ["tier", "redundancy", "storage_gb"],
        [["hot", "GRS", 10_000]],
    )
    _write_sheet(
        wb, "Data Factory",
        ["pipeline_runs", "diu_hours", "ssis_ir_hours"],
        [[50_000, 100, 0]],
    )
    _write_sheet(
        wb, "Event Hubs",
        ["tier", "units"],
        [["standard", 4]],
    )
    _write_sheet(
        wb, "Databricks (optional)",
        ["tier", "dbu_hours"],
        [["premium", 0]],
    )
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# 4. Infra & App Modernization
# ---------------------------------------------------------------------------

def build_app_mod_template() -> bytes:
    wb = _new_book()
    _write_instructions(
        wb,
        "Infra & App Modernization pre-assessment template",
        [
            "App Service / AKS / ACR / Container Apps / APIM / Front Door / Service Bus",
            "PLUS developer tooling: GitHub Enterprise + Advanced Security +",
            "Copilot, Visual Studio subscriptions, and Azure DevOps.",
            "",
            "Developer tooling lines use static reference pricing (Azure Retail",
            "Prices API does not cover per-seat licensing). The Cost Assumptions",
            "sheet stamps the reference date for each line.",
            "",
            "Fill only what applies; leave rows at 0 / none to skip.",
        ],
    )
    _write_sheet(
        wb, "App Service",
        ["plan_sku", "count", "os_windows"],
        [["P2v3", 3, False]],
    )
    _write_sheet(
        wb, "AKS",
        ["pool_name", "node_sku", "count", "os_windows", "uptime_sla"],
        [["default", "Standard_D4s_v5", 3, False, True]],
    )
    _write_sheet(
        wb, "ACR",
        ["sku", "geo_replica_count"],
        [["Premium", 2]],
    )
    _write_sheet(
        wb, "Container Apps",
        ["vcpu_seconds_per_month", "mem_gib_seconds_per_month", "request_millions_per_month"],
        [[0, 0, 0]],
    )
    _write_sheet(
        wb, "API Management",
        ["tier", "units"],
        [["standard_v2", 1]],
    )
    _write_sheet(
        wb, "Front Door",
        ["tier", "routes"],
        [["standard", 5]],
    )
    _write_sheet(
        wb, "Service Bus",
        ["tier", "units"],
        [["standard", 1]],
    )
    _write_sheet(
        wb, "GitHub",
        ["component", "quantity", "unit"],
        [
            ["github_enterprise",         100, "user/month"],
            ["github_advanced_security",   40, "committer/month"],
            ["github_copilot_business",   100, "user/month"],
            ["github_copilot_enterprise",   0, "user/month"],
        ],
    )
    _write_sheet(
        wb, "Visual Studio",
        ["tier", "users"],
        [
            ["vs_professional", 20],
            ["vs_enterprise",    5],
        ],
    )
    _write_sheet(
        wb, "Azure DevOps",
        ["component", "quantity", "unit"],
        [
            ["azdo_basic",               50, "user/month (beyond free 5)"],
            ["azdo_basic_test",           5, "user/month"],
            ["azdo_hosted_pipeline",      2, "parallel job/month"],
            ["azdo_selfhosted_pipeline",  0, "parallel job/month"],
        ],
    )
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# 5. Azure Security
# ---------------------------------------------------------------------------

def build_security_template() -> bytes:
    wb = _new_book()
    _write_instructions(
        wb,
        "Azure Security pre-assessment template",
        [
            "Toggle Defender components + SIEM + DDoS.",
            "",
            "manual_count applies only to components whose scaling is NOT",
            "per-VM (e.g. Defender for SQL on machines → total vCores).",
        ],
    )
    _write_sheet(
        wb, "Components",
        ["component_key", "enabled", "manual_count"],
        [
            ["cspm",                    True, ""],
            ["servers_p2",              True, ""],
            ["sql_on_vms",              False, 16],
            ["storage",                 False, 5],
            ["appsvc",                  False, 4],
            ["keyvault",                False, 0],
            ["containers",              False, 24],
            ["sentinel",                False, ""],
            ["waf",                     False, 1],
            ["private_link",            False, 10],
            ["purview",                 False, 1],
            ["pim",                     False, 100],
            ["ddos_ip_protection",      False, 5],
            ["ddos_network_protection", False, 1],
        ],
    )
    _write_sheet(
        wb, "SIEM",
        ["gb_per_day", "retention_days_over_free_90", "commitment_tier"],
        [[50, 275, "100"]],
    )
    _write_sheet(
        wb, "Target Scope",
        ["target_scope_resources"],
        [[500]],
    )
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# 6. Hybrid & Multi-cloud
# ---------------------------------------------------------------------------

def build_hybrid_template() -> bytes:
    wb = _new_book()
    _write_instructions(
        wb,
        "Hybrid & Multi-cloud (Azure Arc) pre-assessment template",
        [
            "Azure Arc management plane is FREE. Billed items:",
            "  • Defender for Cloud plans applied to Arc machines",
            "  • Log Analytics ingestion from Arc agents",
            "  • Arc-enabled SQL Server PAYG licensing (per core)",
            "  • Windows Server via Arc PAYG licensing (per core, 8 core minimum)",
        ],
    )
    _write_sheet(
        wb, "Arc Servers by Environment",
        ["environment", "server_count"],
        [
            ["onprem", 20],
            ["aws",    10],
            ["gcp",     5],
            ["edge",    0],
        ],
    )
    _write_sheet(
        wb, "Arc SQL Licensing",
        ["licensing", "cores"],
        [["std_payg", 16]],
    )
    _write_sheet(
        wb, "Arc Windows Server Licensing",
        ["licensing", "cores"],
        [["std_payg", 64]],
    )
    _write_sheet(
        wb, "Multicloud Defender",
        ["component", "enabled", "manual_count"],
        [
            ["servers_p2", True,  ""],
            ["cspm",       True,  ""],
            ["sql_on_vms", False, 16],
            ["storage",    False, 3],
            ["containers", False, 24],
        ],
    )
    _write_sheet(
        wb, "Log Analytics",
        ["mb_per_server_per_day"],
        [[300]],
    )
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# 7. M365 & Others
# ---------------------------------------------------------------------------

def build_m365_others_template() -> bytes:
    wb = _new_book()
    _write_instructions(
        wb,
        "M365 & Others pre-assessment template",
        [
            "Items billed through an Azure subscription that don't belong to",
            "the workload-focused pillars:",
            "  • Microsoft 365 Backup (Syntex backup)",
            "  • Microsoft 365 Archive",
            "  • SharePoint Premium (Syntex content AI transactions)",
            "  • Copilot Studio (prepaid packs + PAYG messages)",
            "  • Generic Azure-marketplace / Azure-billed SaaS line items",
            "    (Confluent Cloud, Elastic Cloud, MongoDB Atlas, etc.)",
            "",
            "Static reference pricing is used when the Retail Prices API does",
            "not cover the meter. The Cost Assumptions sheet stamps the",
            "reference date for each static-priced line.",
        ],
    )
    _write_sheet(
        wb, "M365 Backup & Archive",
        ["item", "gb_per_month"],
        [
            ["m365_backup",  5000],
            ["m365_archive", 2000],
        ],
    )
    _write_sheet(
        wb, "SharePoint Premium",
        ["transactions_per_month"],
        [[10_000]],
    )
    _write_sheet(
        wb, "Copilot Studio",
        ["item", "quantity"],
        [
            ["copilot_studio_pack_25k",  4],
            ["copilot_studio_payg",      0],
        ],
    )
    _write_sheet(
        wb, "Others (marketplace SaaS)",
        ["label", "monthly_cost_usd", "notes"],
        [
            ["Elastic Cloud Enterprise",    1200.0, "Observability + security analytics"],
            ["Confluent Cloud Basic",        500.0, "Kafka streaming"],
            ["",                                0, ""],
        ],
    )
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# Registry — label + filename + builder
# ---------------------------------------------------------------------------

TEMPLATE_REGISTRY: List[Tuple[str, str, callable]] = [
    ("Infra Lift-and-Shift inventory",    "infra_inventory_template.xlsx",            build_infra_inventory_template),
    ("AI Application & Development",      "ai_usecase_preassessment.xlsx",            build_ai_template),
    ("Data Platform (Fabric-first)",      "data_platform_preassessment.xlsx",         build_data_template),
    ("Infra & App Modernization",         "app_modernization_preassessment.xlsx",     build_app_mod_template),
    ("Azure Security",                    "security_preassessment.xlsx",              build_security_template),
    ("Hybrid & Multi-cloud",              "hybrid_multicloud_preassessment.xlsx",     build_hybrid_template),
    ("M365 & Others",                     "m365_and_others_preassessment.xlsx",       build_m365_others_template),
]
