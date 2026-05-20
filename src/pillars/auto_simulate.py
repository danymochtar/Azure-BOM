"""Auto-simulate pillar inputs from an uploaded use-case document.

When the user's upload is a narrative (design doc, RFP, use case) instead of
an infra list, the relevant pillar inputs (tokens/day, Fabric capacity, SIEM
GB/day, etc.) need to be INFERRED from user counts, query patterns, and
retention requirements. This module asks Sonnet 4.6 to read the document and
return:

- `suggested_inputs`: a partial pillar-inputs dict (shape matches what
  `render_inputs` of the chosen pillar consumes). Widgets will pre-fill from
  this via the `prefs` overlay.
- `assumptions`: short human-readable strings explaining the math
  ("500 DAU × 5 queries/day × 2K input + 500 output tokens → 5M tokens/day").
- `open_questions`: clarifying questions when the doc didn't contain enough
  info. The user can answer them in a text area, and the next auto-simulate
  call refines based on the answers.
- `confidence`: 0-1.

Cached per (file hash, pillar, answers) so refreshes and widget toggles don't
re-hit the API.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import anthropic
from pydantic import BaseModel, Field

from .. import usage_tracker
from ..llm import SONNET_TO_OPUS, call_with_cascade
from ..parsers.content import prepare as _prepare_content
from .ai_application import AZURE_FOUNDRY_MODELS, AZURE_OPENAI_MODELS, COGNITIVE_SERVICES


# Sonnet by default; falls back to Opus on OverloadedError (529) via
# `call_with_cascade`.
MODEL = SONNET_TO_OPUS[0]

# Auto-simulate doesn't need every row of an RVTools-sized export — it only
# needs to understand the SHAPE of the workload (sheet names, headers, row
# counts, a handful of sample rows). 20 rows is plenty to recognize the
# pattern of an inventory file; the per-pillar Sonnet call then uses the
# headers + counts + narrative content to infer sizing.
#
# Setting this low matters for Tier-1 API users where the input-tokens-per-
# minute cap is 30K — at 200 rows × ~10 cols × ~50 chars/cell each call
# burns ~25K input tokens, so 2-3 pillar calls in quick succession trip
# the 30K ITPM limit even when no single call exceeds the context window.
AUTO_SIM_SPREADSHEET_ROWS = 20

# Safety cap on the total character count of text content blocks fed to
# the model. ~200K chars ≈ 50K tokens — well under the per-minute rate
# limit even when 3 pillar calls fire in sequence. Truncated content
# gets a clear marker so the model knows what's missing.
AUTO_SIM_TEXT_CHAR_BUDGET = 200_000


PILLAR_SCHEMAS: Dict[str, str] = {
    "ai_application": """Schema for ai_application.suggested_inputs:
{
  "openai_usage": {"gpt-4o": {"input_1k_per_day": float, "output_1k_per_day": float}, ...},
      // Model keys: """ + ", ".join(AZURE_OPENAI_MODELS.keys()) + """
  "foundry_usage": {...same shape, keys from Foundry catalog...},
      // Keys: """ + ", ".join(AZURE_FOUNDRY_MODELS.keys()) + """
  "ai_search": {"tier": "none|basic|standard|s2|s3|s3hd|l1|l2", "replicas": int, "partitions": int},
  "gpu_vm":    {"sku": "<ARM SKU or none>", "count": int, "hours": float, "os_windows": bool},
  "finetune":  {"model": "<openai model key or none>", "tokens_1k": float},
  "cognitive": {"<service_key>": <qty>, ...}
      // service_keys: """ + ", ".join(COGNITIVE_SERVICES.keys()) + """
}""",
    "data_platform": """Schema for data_platform.suggested_inputs:
{
  "pbi_users": int,  // existing + planned Power BI users — drives Fabric F-SKU
                     // recommendation (< 25 = F2, 25-50 = F4, 50-100 = F8,
                     // 100-200 = F16, 200-500 = F32, 500-2k = F64, 2k-10k = F128,
                     // > 10k = F256). If doc mentions Power BI viewers, readers,
                     // or analysts, populate this from the explicit count or an
                     // inferred tenant-user count.
  "fabric_sku": "F2|F4|F8|F16|F32|F64|F128|F256|F512|F1024|F2048|null",
  "cosmos_ru_per_second": int,
  "synapse_dwu": int,
  "azure_sql_db": {"tier": "none|gp|bc|hs", "vcores": int, "storage_gb": int, "zone_redundant": bool},
  "azure_sql_mi": {"tier": "none|gp|bc", "vcores": int, "storage_gb": int},
  "adls_gen2": {"tier": "none|hot|cool|cold", "redundancy": "LRS|ZRS|GRS|RA-GRS", "storage_gb": int},
  "adf": {"pipeline_runs": int, "diu_hours": float, "ssis_ir_hours": float},
  "event_hubs": {"tier": "none|standard|premium|dedicated", "units": int},
  "databricks": {"tier": "none|standard|premium|serverless_sql|serverless_ml", "dbu_hours": float},
  "synapse_serverless": {"tb_per_month": float},
  "power_bi": {"sku": "none|F2|F8|F16|F32|F64|P1|P2|P3|A1|A2|A3|A4|A5|A6"},
  "adx": {"sku": "none|dev_d11_v2|compute_d14_v2|compute_d32d_v4|storage_e16s_v4|storage_e64s_v4", "count": int},
  "cosmos_serverless": {"request_units_month": int, "storage_gb": float},
  "postgres_flexible": {"tier": "none|burstable|general_purpose|memory_optimized", "vcores": int, "storage_gb": int, "ha_enabled": bool},
  "mysql_flexible":    {"tier": "none|burstable|general_purpose|memory_optimized", "vcores": int, "storage_gb": int, "ha_enabled": bool},
  "redis_cache":       {"tier": "none|basic|standard|premium|enterprise|enterprise_flash", "sku": "C0|C1|C2|C3|C4|C5|C6|P1|P2|P3|P4|P5|E10|E20|E50|E100|E200|E400|none"},
  "azure_files":       {"tier": "none|standard_lrs|standard_zrs|standard_grs|premium_lrs|premium_zrs", "storage_gb": int}
}""",
    "azure_security": """Schema for azure_security.suggested_inputs:
{
  "enabled":         ["cspm","servers_p2","sql_on_vms","storage","appsvc","keyvault","containers","sentinel","waf","private_link","purview","pim","ddos_ip_protection","ddos_network_protection"],
  "manual_counts":   {"<defender_plan_or_component_key>": number, ...},
  "siem_gb_per_day": float,
  "siem_retention_days": int,
  "siem_tier":       "payg|100|200|400|500|1000|2000|5000",
  "target_scope":    int
}""",
    "infra_modernization": """Schema for infra_modernization.suggested_inputs:
{
  "app_service":    {"sku": "none|P0v3|P1v3|P2v3|P3v3|P1mv3|P2mv3", "count": int, "os_windows": bool},
  "aks":            {"pool_name": str, "sku": "<ARM SKU or none>", "count": int, "os_windows": bool, "uptime_sla": bool},
  "acr":            {"sku": "none|Basic|Standard|Premium", "geo_replica_count": int},
  "container_apps": {"vcpu_seconds": float, "mem_gib_seconds": float, "request_millions": float},
  "apim":           {"tier": "none|consumption|basic_v2|standard_v2|premium", "units": int},
  "front_door":     {"tier": "none|standard|premium", "routes": int},
  "service_bus":    {"tier": "none|basic|standard|premium", "units": int},
  "github_enterprise_users":         int,
  "github_advanced_security_committers": int,
  "github_copilot_business_users":   int,
  "github_copilot_enterprise_users": int,
  "vs_professional_users":           int,
  "vs_enterprise_users":             int,
  "azdo_basic_users":                int,
  "azdo_basic_test_users":           int,
  "azdo_hosted_pipeline_jobs":       int,
  "azdo_selfhosted_pipeline_jobs":   int
}
Notes:
- Developer tooling is priced from a static 2026-04 reference sheet (the
  Retail Prices API does not cover per-seat licensing). Typical defaults
  when the doc says "X developers": github_enterprise_users = X,
  github_copilot_business_users = X, vs_professional_users ≈ X/5,
  azdo_basic_users = max(X - 5, 0).""",
    "infra_lift_shift": """Schema for infra_lift_shift.suggested_inputs (advisory only — the pillar
still reads the file with Sonnet for VM extraction):
{
  "strategy_key":         "iaas|hybrid|paas",
  "include_lz":           bool,
  "include_ha":           bool,
  "include_bcdr":         bool,
  "backup_pct":           int,
  "la_mb_per_vm_per_day": int,
  "bandwidth_gb":         int
}""",
    "hybrid_multicloud": """Schema for hybrid_multicloud.suggested_inputs:
{
  "arc_servers":   {"onprem": int, "aws": int, "gcp": int, "edge": int},
  "arc_sql":       {"licensing": "byol|std_payg|ent_payg|ent_core", "cores": int},
  "arc_winserver": {"licensing": "byol|std_payg|dc_payg",          "cores": int},
  "multicloud_defender": ["servers_p2","cspm","sql_on_vms","storage","containers"],
      // Any subset
  "multicloud_defender_counts": {"<defender_plan>": number, ...},
      // e.g. {"sql_on_vms": 16, "storage": 3, "containers": 24}
  "arc_la_mb_per_server_per_day": int
}
Notes:
- Arc server counts default to 0 per environment; fill them based on doc.
- Windows Server / SQL Server licensing: pick "byol" if the doc says SA /
  existing licenses; pick std/ent PAYG when the customer wants to switch
  to Azure monthly billing for those cores.
- Defender plans cost the SAME per-resource as for Azure VMs — reuse the
  server count for servers_p2 / cspm and enter manual vCore counts for
  sql_on_vms / containers / storage accounts.""",
    "m365_and_others": """Schema for m365_and_others.suggested_inputs:
{
  "m365_backup_gb":              float,  // protected GB / month
  "m365_archive_gb":              float,  // archived GB / month
  "sharepoint_premium_tx":        float,  // Syntex transactions / month
  "copilot_studio_pack_25k":     int,    // prepaid packs (25K messages each)
  "copilot_studio_payg_messages": int,    // pay-as-you-go messages / month
  "others": [
     {"label": str, "monthly_cost_usd": float, "notes": str}
     // Free-form Azure-marketplace / Azure-billed SaaS lines.
     // Examples: {"label": "Elastic Cloud Enterprise", "monthly_cost_usd": 1200,
     //            "notes": "observability tier"}
  ]
}
Notes:
- All rates except `others[]` come from a static 2026-04 reference sheet;
  the Cost Assumptions sheet stamps the reference date on every line.
- `others[]` is echoed back verbatim with the user-supplied monthly cost.""",
}


SYSTEM_PROMPT = """You are an Azure solution architect estimating the cost of a workload.

You will be given a use-case / design document and told which Azure pillar to
simulate inputs for. Your job is to read the document, EXTRACT any explicit
sizing numbers, and INFER the rest using clearly stated industry-standard
assumptions. **Always produce a non-empty BOM** — if the doc is sparse, fall
back to defensible baseline values from MS Learn so the user has something to
fine-tune from.

Rules:
1. Pull explicit numbers first (e.g. "we expect 1000 DAU" → use that directly).
2. Compute derived values transparently. Write each computation step to the
   `assumptions` list so the user can see WHY the number is what it is.
3. Use realistic defaults when data is missing:
   - Concurrency: peak ≈ DAU × 0.1 for B2C, × 0.3 for B2B.
   - LLM RAG chat: typical turn = 2K input + 500 output tokens.
   - Embedding: one-time index + ~10% of tokens/day for incremental.
   - SIEM: 1 EPS ≈ 2-3 MB/day → 10K EPS ≈ 25 GB/day.
   - Standard Azure workloads: 720 business hours/month unless stated.
4. **Compute-mode aware defaults** — the user message includes a
   `compute_mode` hint (`saving` / `normal` / `high_perf`). Bias your tier
   picks accordingly:
   - **saving**: Burstable VMs (B-series), App Service Basic (B1/B2),
     Azure SQL DB General Purpose Serverless, Cosmos DB Serverless,
     PostgreSQL/MySQL Burstable, Redis Basic. AKS without uptime SLA.
   - **normal**: Dsv5 VMs, App Service P0v3/P1v3 (Premium v3), Azure SQL
     DB General Purpose provisioned 2-4 vCore, Cosmos DB autoscale,
     Redis Standard. AKS with uptime SLA.
   - **high_perf**: Esv5 / Fsv2 VMs, App Service P2v3/P3v3 or P1mv3
     (memory-opt), Azure SQL DB Business Critical 8+ vCore, Cosmos DB
     multi-region, Redis Premium. AKS with GPU pools + uptime SLA.
   GPU workloads (training / inference / RAG at scale) always recommend
   NDmsv4 (H100) for training and NCadsv4 (A100) for inference regardless
   of mode — note the mode in `assumptions`.
5. **Never return all-zero / all-`none` inputs.** When the doc gives no
   hint at all, pick a minimum-viable baseline (e.g. 1 App Service P0v3,
   100 GB ADLS Hot, 1 vCore SQL GP Serverless) and call out the
   guesswork in `assumptions`. The user will fine-tune via the widgets.
6. If a critical input is missing AND a default would mislead by >30%, add a
   clarifying question to `open_questions`. Examples:
   - "How many concurrent AKS pods at peak?"
   - "What retention is required for security logs (regulatory floor)?"
   - "Do you need cross-region DR or is single-region acceptable?"
7. If `prior_answers` are provided in the user message, incorporate them and
   DO NOT re-ask.
8. Output MUST conform to the `suggested_inputs` schema for the requested
   pillar — use the exact key names. Unknown / not-relevant keys can be
   omitted entirely."""


class PillarSimulation(BaseModel):
    pillar: str = Field(description="One of the five pillar keys")
    suggested_inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Partial inputs dict matching the pillar's render_inputs schema.",
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Human-readable derivation steps. One per line.",
    )
    open_questions: List[str] = Field(
        default_factory=list,
        description="Clarifying questions the user should answer to tighten the estimate.",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0 = wild guess, 1 = fully explicit in doc")


def simulate(
    pillar: str,
    data: bytes,
    filename: str,
    api_key: str,
    prior_answers: Optional[Dict[str, str]] = None,
    compute_mode: str = "normal",
) -> PillarSimulation:
    """Run Sonnet auto-simulate for a pillar. Callers should cache by
    (file hash, pillar, answers-hash) — this function hits the API every
    time it's called."""
    if pillar not in PILLAR_SCHEMAS:
        raise ValueError(f"Unsupported pillar for simulate: {pillar}")

    # Use sampled rows (not the full sheet) — a 5000-row RVTools export
    # otherwise blows the 1M-token model limit even before we add the
    # schema + system prompt. Sampling is fine here because auto-simulate
    # only infers shape; full extraction is done separately.
    uc = _prepare_content(
        data, filename, spreadsheet_preview_rows=AUTO_SIM_SPREADSHEET_ROWS
    )

    # Defensive truncation for narrative / text content (DOCX, plain text,
    # large CSV/Excel previews). PDFs and images bypass this because they
    # ride as binary `document` / `image` blocks; for those, the model's
    # own tokenizer applies and we let the API surface the error if it's
    # still too big.
    safe_blocks = []
    text_budget = AUTO_SIM_TEXT_CHAR_BUDGET
    for blk in uc.content_blocks:
        if isinstance(blk, dict) and blk.get("type") == "text":
            txt = blk.get("text", "") or ""
            if len(txt) > text_budget:
                head = txt[: text_budget // 2]
                tail = txt[-text_budget // 2 :]
                txt = (
                    f"{head}\n\n"
                    f"… [TRUNCATED — {len(blk['text']) - text_budget:,} characters omitted "
                    f"to fit the auto-simulate context budget; sampling head + tail] …\n\n"
                    f"{tail}"
                )
                text_budget = 0
            else:
                text_budget -= len(txt)
            safe_blocks.append({"type": "text", "text": txt})
        else:
            safe_blocks.append(blk)

    instruction_parts = [
        f"File: {filename}",
        f"Summary: {uc.text_summary}",
        "",
        f"Simulate inputs for the pillar: **{pillar}**",
        "",
        PILLAR_SCHEMAS[pillar],
        "",
    ]
    # Mode hint biases tier picks (Saving / Normal / High-Performance).
    instruction_parts.append(
        f"Compute mode hint: **{compute_mode}** — bias tier picks per "
        "Rule 4 in the system prompt."
    )
    instruction_parts.append("")
    if prior_answers:
        instruction_parts.append("Prior answers from the user (incorporate and do not re-ask):")
        for q, a in prior_answers.items():
            if (a or "").strip():
                instruction_parts.append(f"- Q: {q}\n  A: {a}")
        instruction_parts.append("")
    instruction_parts.append(
        "Produce the PillarSimulation object. List every derivation step in "
        "`assumptions`. Only add `open_questions` if missing info would skew "
        "cost by >30%."
    )

    user_content = safe_blocks + [
        {"type": "text", "text": "\n".join(instruction_parts)}
    ]

    client = anthropic.Anthropic(api_key=api_key, max_retries=4)

    def _call(blocks):
        def _invoke(model: str):
            return client.messages.parse(
                model=model,
                max_tokens=8000,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": blocks}],
                output_format=PillarSimulation,
            )
        # Sonnet → Opus cascade on OverloadedError (529).
        return call_with_cascade(
            process_label=f"Auto-simulate ({pillar})",
            cascade=SONNET_TO_OPUS,
            invoke=_invoke,
        )

    try:
        response = _call(user_content)
    except anthropic.RateLimitError:
        # Tier-1 API caps at 30K input tokens / minute. Multi-pillar
        # auto-simulate can burn through that. Wait one full minute (the
        # rolling window) and retry once. If it still fails, propagate
        # so the caller falls back to defaults with a clear message.
        import time as _time
        _time.sleep(60)
        response = _call(user_content)
    except anthropic.BadRequestError as e:
        # Second-pass shrink: if the binary content blocks (PDF/image) +
        # already-truncated text still exceed the model's context window,
        # send ONLY the text_summary + schema. The user loses doc detail
        # but still gets a structurally-valid simulation instead of a
        # hard fallback to defaults.
        msg = str(e)
        if "prompt is too long" not in msg.lower():
            raise
        fallback_content = [
            {
                "type": "text",
                "text": (
                    f"[Note: the original upload was too large for the "
                    f"auto-simulate context window. Working from the "
                    f"summary only — please rely on user-supplied "
                    f"`prior_answers` and clearly stated assumptions.]\n\n"
                    f"File: {filename}\nSummary: {uc.text_summary}\n\n"
                    + "\n".join(instruction_parts)
                ),
            }
        ]
        # Second-pass fallback also goes through the Sonnet → Opus
        # cascade since the same Sonnet model might still be overloaded.
        response = _call(fallback_content)
    sim = response.parsed_output
    sim.pillar = pillar  # defensive
    return sim
